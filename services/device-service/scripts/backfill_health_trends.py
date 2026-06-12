"""Backfill historical health trend scores for existing performance trend buckets."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (SERVICE_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _run(args: argparse.Namespace) -> dict[str, int | str | bool]:
    from app.database import AsyncSessionLocal
    from app.services.performance_trends import PerformanceTrendService
    from services.shared.tenant_context import TenantContext

    tenant_ctx = None
    if args.tenant_id:
        tenant_ctx = TenantContext(
            tenant_id=args.tenant_id,
            user_id="health-trend-backfill",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )

    async with AsyncSessionLocal() as session:
        service = PerformanceTrendService(session, tenant_ctx)
        return await service.backfill_health_scores(
            start_utc=_parse_dt(args.start),
            end_utc=_parse_dt(args.end),
            tenant_id=args.tenant_id,
            device_id=args.device_id,
            only_missing_health=not args.rewrite_all_health,
            include_current_bucket=args.include_current_bucket,
            batch_size=args.batch_size,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recompute health_score for existing device_performance_trends rows.",
    )
    parser.add_argument("--start", required=True, help="Inclusive UTC/ISO8601 bucket start lower bound.")
    parser.add_argument("--end", required=True, help="Exclusive UTC/ISO8601 bucket start upper bound.")
    parser.add_argument("--tenant-id", help="Restrict backfill to one tenant.")
    parser.add_argument("--device-id", help="Restrict backfill to one device. Requires --tenant-id.")
    parser.add_argument(
        "--rewrite-all-health",
        action="store_true",
        help="Recompute health_score for all matching rows, not only rows with null health_score.",
    )
    parser.add_argument(
        "--include-current-bucket",
        action="store_true",
        help="Also rewrite the currently open materialization bucket.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Commit progress every N processed rows.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.device_id and not args.tenant_id:
        parser.error("--device-id requires --tenant-id")

    summary = asyncio.run(_run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if int(summary.get("failed", 0)) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
