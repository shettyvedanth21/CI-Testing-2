"""Rebuild anomaly daily/weekly count rows using IST (platform timezone) day boundaries.

Two-phase safe approach:
  Phase 1 (rebuild): Re-aggregate all daily and weekly counts using IST day
    boundaries.  Device discovery uses MachineAnomalyEvent (not Device.is_active)
    so inactive devices with anomaly data are included.  Old UTC-date rows
    remain untouched; new/overwritten rows get updated_at >= rebuild_start_utc.
  Phase 2 (cleanup):  Delete orphaned rows whose updated_at predates the
    rebuild start timestamp — but ONLY for devices that have anomaly events,
    so rows for devices whose events were retention-cleaned are preserved.

Run phases separately for maximum safety:
  python backfill_anomaly_tz.py                          # rebuild only
  python backfill_anomaly_tz.py --dry-run                # preview rebuild scope
  python backfill_anomaly_tz.py --cleanup-orphans        # count + delete orphans
  python backfill_anomaly_tz.py --cleanup-orphans --dry-run  # count only, no delete
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else Path(__file__).resolve().parents[1]
for _p in (SERVICE_DIR, REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _scope_filter_expr(model_class, tenant_id: str | None, device_id: str | None):
    from sqlalchemy import and_
    clauses = []
    if tenant_id:
        clauses.append(model_class.tenant_id == tenant_id)
    if device_id:
        clauses.append(model_class.device_id == device_id)
    return and_(*clauses) if clauses else True


async def _discover_range(
    session,
    tenant_id: str | None = None,
    device_id: str | None = None,
) -> tuple[list[tuple[str, str]], date, date]:
    from sqlalchemy import select, func, and_, distinct
    from app.models.device import MachineAnomalyEvent
    from app.services.anomaly.tz import get_platform_tz

    scope_clauses = []
    if tenant_id:
        scope_clauses.append(MachineAnomalyEvent.tenant_id == tenant_id)
    if device_id:
        scope_clauses.append(MachineAnomalyEvent.device_id == device_id)
    scope_where = and_(*scope_clauses) if scope_clauses else True

    earliest_result = await session.execute(
        select(func.min(MachineAnomalyEvent.occurred_at)).where(scope_where)
    )
    earliest = earliest_result.scalar()
    if earliest is None:
        return [], date.today(), date.today()

    platform_tz = get_platform_tz()
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    earliest_ist = earliest.astimezone(platform_tz).date()

    today_ist = datetime.now(timezone.utc).astimezone(platform_tz).date()

    dev_result = await session.execute(
        select(
            MachineAnomalyEvent.tenant_id,
            MachineAnomalyEvent.device_id,
        )
        .where(scope_where)
        .distinct()
        .order_by(MachineAnomalyEvent.tenant_id, MachineAnomalyEvent.device_id)
    )
    devices = list(dev_result.all())

    return devices, earliest_ist, today_ist


async def _rebuild(args: argparse.Namespace) -> dict:
    from app.database import AsyncSessionLocal
    from app.models.device import MachineAnomalyDailyCount, MachineAnomalyWeeklyCount
    from app.services.anomaly.service import (
        aggregate_daily_counts_for_device,
        aggregate_weekly_counts_for_device,
    )
    from app.services.anomaly.tz import local_today

    rebuild_start_utc = datetime.now(timezone.utc)
    is_dry_run = args.dry_run
    summary: dict = {
        "rebuild_start_utc": rebuild_start_utc.isoformat(),
        "mode": "dry_run" if is_dry_run else "rebuild",
        "tenant_id": args.tenant_id,
        "device_id": args.device_id,
        "scope_description": "",
        "devices_processed": 0,
        "daily_rows_rebuilt": 0,
        "weekly_rows_rebuilt": 0,
        "errors": 0,
    }

    async with AsyncSessionLocal() as session:
        if args.device_id and not args.tenant_id:
            print("ERROR: --device-id requires --tenant-id")
            summary["errors"] = 1
            return summary

        devices, earliest_ist, today_ist = await _discover_range(
            session, tenant_id=args.tenant_id, device_id=args.device_id,
        )

        if not devices:
            print("No devices found matching filters.")
            return summary

        date_range_days = (today_ist - earliest_ist).days + 1
        ist_dates = [earliest_ist + timedelta(days=i) for i in range(date_range_days)]

        scope_parts = [f"{len(devices)} device(s)"]
        if args.tenant_id:
            scope_parts.append(f"tenant={args.tenant_id}")
        if args.device_id:
            scope_parts.append(f"device={args.device_id}")
        scope_parts.append(f"dates={earliest_ist} to {today_ist} ({date_range_days} days)")
        scope_desc = "  ".join(scope_parts)
        summary["scope_description"] = scope_desc

        print(f"Mode: {'DRY RUN' if is_dry_run else 'REBUILD'}")
        print(f"Scope: {scope_desc}")
        print(f"Rebuild start UTC: {rebuild_start_utc.isoformat()}")
        print(f"IMPORTANT: Record rebuild_start_utc for orphan cleanup later:")
        print(f"  --rebuild-start-utc {rebuild_start_utc.isoformat()}")

        batch_size = args.batch_size
        batch_count = 0

        for idx, (tenant_id, device_id) in enumerate(devices, 1):
            daily_rebuilt = 0
            weekly_rebuilt = 0

            for target_date in ist_dates:
                if is_dry_run:
                    daily_rebuilt += 1
                    continue
                try:
                    async with session.begin_nested():
                        res = await aggregate_daily_counts_for_device(
                            session, tenant_id, device_id, target_date,
                        )
                    if res is not None:
                        daily_rebuilt += 1
                except Exception as exc:
                    summary["errors"] += 1
                    print(f"  ERROR daily {tenant_id} {device_id} {target_date}: {exc}")

            if daily_rebuilt > 0 and not is_dry_run:
                first_monday = earliest_ist - timedelta(days=earliest_ist.weekday())
                week_monday = first_monday
                while week_monday <= today_ist:
                    try:
                        async with session.begin_nested():
                            res = await aggregate_weekly_counts_for_device(
                                session, tenant_id, device_id, week_monday,
                            )
                        if res is not None:
                            weekly_rebuilt += 1
                    except Exception as exc:
                        summary["errors"] += 1
                        print(f"  ERROR weekly {tenant_id} {device_id} {week_monday}: {exc}")
                    week_monday += timedelta(weeks=1)

            if not is_dry_run:
                batch_count += 1
                if batch_count >= batch_size:
                    await session.commit()
                    batch_count = 0

            summary["devices_processed"] = idx
            summary["daily_rows_rebuilt"] += daily_rebuilt
            summary["weekly_rows_rebuilt"] += weekly_rebuilt

            if idx % 50 == 0 or idx == len(devices):
                elapsed = (datetime.now(timezone.utc) - rebuild_start_utc).total_seconds()
                rate = idx / elapsed if elapsed > 0 else 0
                remaining = (len(devices) - idx) / rate if rate > 0 else 0
                print(f"  [{idx}/{len(devices)}] daily={daily_rebuilt} weekly={weekly_rebuilt} "
                      f"~{remaining:.0f}s remaining")

        if not is_dry_run and batch_count > 0:
            await session.commit()

    summary["duration_seconds"] = (datetime.now(timezone.utc) - rebuild_start_utc).total_seconds()
    return summary


async def _cleanup_orphans(args: argparse.Namespace) -> dict:
    from app.database import AsyncSessionLocal
    from app.models.device import MachineAnomalyEvent, MachineAnomalyDailyCount, MachineAnomalyWeeklyCount
    from sqlalchemy import select, func, delete, and_

    rebuild_start_utc = datetime.fromisoformat(args.rebuild_start_utc)
    if rebuild_start_utc.tzinfo is None:
        rebuild_start_utc = rebuild_start_utc.replace(tzinfo=timezone.utc)

    is_dry_run = args.dry_run
    scope_parts = [f"cutoff={rebuild_start_utc.isoformat()}"]
    if args.tenant_id:
        scope_parts.append(f"tenant={args.tenant_id}")
    if args.device_id:
        scope_parts.append(f"device={args.device_id}")
    scope_desc = "  ".join(scope_parts)

    print(f"Mode: {'DRY RUN (count only, no delete)' if is_dry_run else 'CLEANUP (will delete)'}")
    print(f"Scope: {scope_desc}")

    summary: dict = {
        "mode": "dry_run_count" if is_dry_run else "cleanup",
        "rebuild_start_utc": rebuild_start_utc.isoformat(),
        "tenant_id": args.tenant_id,
        "device_id": args.device_id,
        "scope_description": scope_desc,
        "daily_orphan_candidates": 0,
        "weekly_orphan_candidates": 0,
        "daily_orphans_deleted": 0,
        "weekly_orphans_deleted": 0,
    }

    async with AsyncSessionLocal() as session:
        rebuilt_device_subq = (
            select(
                MachineAnomalyEvent.tenant_id,
                MachineAnomalyEvent.device_id,
            )
            .distinct()
            .where(_scope_filter_expr(MachineAnomalyEvent, args.tenant_id, args.device_id))
        ).subquery()

        orphan_where_daily = and_(
            MachineAnomalyDailyCount.updated_at < rebuild_start_utc,
            _scope_filter_expr(MachineAnomalyDailyCount, args.tenant_id, args.device_id),
            MachineAnomalyDailyCount.tenant_id == rebuilt_device_subq.c.tenant_id,
            MachineAnomalyDailyCount.device_id == rebuilt_device_subq.c.device_id,
        )
        orphan_where_weekly = and_(
            MachineAnomalyWeeklyCount.updated_at < rebuild_start_utc,
            _scope_filter_expr(MachineAnomalyWeeklyCount, args.tenant_id, args.device_id),
            MachineAnomalyWeeklyCount.tenant_id == rebuilt_device_subq.c.tenant_id,
            MachineAnomalyWeeklyCount.device_id == rebuilt_device_subq.c.device_id,
        )

        daily_count_result = await session.execute(
            select(func.count()).select_from(MachineAnomalyDailyCount).where(orphan_where_daily)
        )
        daily_candidates = daily_count_result.scalar() or 0
        summary["daily_orphan_candidates"] = daily_candidates

        weekly_count_result = await session.execute(
            select(func.count()).select_from(MachineAnomalyWeeklyCount).where(orphan_where_weekly)
        )
        weekly_candidates = weekly_count_result.scalar() or 0
        summary["weekly_orphan_candidates"] = weekly_candidates

        print(f"Candidate daily orphan rows: {daily_candidates}")
        print(f"Candidate weekly orphan rows: {weekly_candidates}")

        if daily_candidates == 0 and weekly_candidates == 0:
            print("No orphan rows found. Nothing to do.")
            return summary

        if is_dry_run:
            print("DRY RUN: No rows deleted. Re-run without --dry-run to delete.")
            return summary

        if not args.confirm_cleanup:
            print("WARNING: Destructive operation. Re-run with --confirm-cleanup to proceed.")
            summary["mode"] = "cleanup_aborted_no_confirm"
            return summary

        daily_result = await session.execute(
            delete(MachineAnomalyDailyCount).where(orphan_where_daily)
        )
        summary["daily_orphans_deleted"] = daily_result.rowcount

        weekly_result = await session.execute(
            delete(MachineAnomalyWeeklyCount).where(orphan_where_weekly)
        )
        summary["weekly_orphans_deleted"] = weekly_result.rowcount

        await session.commit()

        print(f"Deleted {daily_result.rowcount} daily orphan rows, "
              f"{weekly_result.rowcount} weekly orphan rows.")

    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild anomaly daily/weekly counts using IST day boundaries.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be rebuilt/counted without making changes.")
    parser.add_argument("--tenant-id", help="Restrict to one tenant.")
    parser.add_argument("--device-id", help="Restrict to one device (requires --tenant-id).")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Commit every N devices (default: 50).")
    parser.add_argument("--cleanup-orphans", action="store_true",
                        help="Count and delete orphan rows with updated_at before rebuild start.")
    parser.add_argument("--confirm-cleanup", action="store_true",
                        help="Required to actually delete rows during --cleanup-orphans "
                             "(without this flag, cleanup only counts candidates).")
    parser.add_argument("--rebuild-start-utc", type=str,
                        help="ISO-8601 UTC timestamp for orphan cutoff (required with --cleanup-orphans).")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.device_id and not args.tenant_id:
        parser.error("--device-id requires --tenant-id")
    if args.cleanup_orphans and not args.rebuild_start_utc:
        parser.error("--cleanup-orphans requires --rebuild-start-utc")
    if args.confirm_cleanup and not args.cleanup_orphans:
        parser.error("--confirm-cleanup requires --cleanup-orphans")

    if args.cleanup_orphans:
        result = asyncio.run(_cleanup_orphans(args))
    else:
        result = asyncio.run(_rebuild(args))

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if int(result.get("errors", 0)) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
