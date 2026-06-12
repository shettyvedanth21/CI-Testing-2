"""Shared scheduler cycle implementations for device-service.

Both the API runtime (app.__init__) and the dedicated scheduler runtime
(app.scheduler_runner) call these cycles.  The only difference is the
session factory: the API runtime uses AsyncSessionLocal while the
scheduler runtime uses SchedulerSessionLocal.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable, Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.models.device import DashboardSnapshot, Device
from app.services.energy_sync import sync_energy_device_days
from services.shared.tenant_context import TenantContext
from sqlalchemy import delete, select, tuple_

logger = logging.getLogger(__name__)


async def load_active_tenant_ids(session) -> list[str]:
    result = await session.execute(
        select(Device.tenant_id)
        .where(Device.deleted_at.is_(None), Device.tenant_id.is_not(None))
        .distinct()
        .order_by(Device.tenant_id.asc())
    )
    return [row[0] for row in result.all() if row[0] is not None]


async def run_live_projection_reconciliation_cycle(
    *,
    refresh_fleet_snapshot: bool,
    session_factory: Callable[..., Any],
) -> None:
    from app.services.dashboard import DashboardService
    from app.services.live_projection import LiveProjectionService

    try:
        async with session_factory() as session:
            tenant_ids = await load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Live projection tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="system-scheduler",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )
        try:
            async with session_factory() as session:
                projection = LiveProjectionService(session, tenant_ctx)
                summary = await projection.reconcile_recent_projections(max_devices=500)
                repaired_device_ids = sorted(
                    {
                        str(device_id)
                        for device_id in (
                            list(summary.get("repaired_device_ids", []) or [])
                            + list(summary.get("timeout_closed_device_ids", []) or [])
                        )
                        if str(device_id).strip()
                    }
                )
                if repaired_device_ids:
                    await sync_energy_device_days(
                        session=session,
                        tenant_id=tenant_id,
                        device_ids=repaired_device_ids,
                        day=datetime.now(ZoneInfo(settings.PLATFORM_TIMEZONE)).date(),
                    )
                if refresh_fleet_snapshot and (
                    int(summary.get("repaired", 0)) > 0 or int(summary.get("closed_intervals", 0)) > 0
                ):
                    dashboard = DashboardService(session, tenant_ctx)
                    await dashboard.materialize_fleet_state_snapshot()
                logger.info(
                    "Live projection reconciliation cycle completed",
                    extra={
                        "tenant_id": tenant_id,
                        "scanned": summary.get("scanned", 0),
                        "repaired": summary.get("repaired", 0),
                        "repaired_device_ids": summary.get("repaired_device_ids", []),
                        "closed_intervals": summary.get("closed_intervals", 0),
                        "timeout_closed_device_ids": summary.get("timeout_closed_device_ids", []),
                        "fleet_snapshot_refreshed": bool(
                            refresh_fleet_snapshot
                            and (
                                int(summary.get("repaired", 0)) > 0
                                or int(summary.get("closed_intervals", 0)) > 0
                            )
                        ),
                    },
                )
        except Exception as exc:
            logger.error(
                "Live projection reconciliation failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def run_activation_backfill_cycle(
    *,
    session_factory: Callable[..., Any],
) -> None:
    from app.services.dashboard import DashboardService
    from app.services.live_projection import LiveProjectionService

    try:
        async with session_factory() as session:
            tenant_ids = await load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Activation backfill tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="system-scheduler",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )
        try:
            async with session_factory() as session:
                projection = LiveProjectionService(session, tenant_ctx)
                backfill = getattr(projection, "backfill_first_telemetry_timestamps", None)
                if callable(backfill):
                    summary = await backfill(max_devices=500)
                else:
                    summary = {"scanned": 0, "repaired": 0, "repaired_device_ids": []}
                if int(summary.get("repaired", 0)) > 0:
                    dashboard = DashboardService(session, tenant_ctx)
                    await dashboard.materialize_fleet_state_snapshot()
                    await dashboard.materialize_dashboard_summary_snapshot()
                logger.info(
                    "Activation backfill cycle completed",
                    extra={
                        "tenant_id": tenant_id,
                        "scanned": summary.get("scanned", 0),
                        "repaired": summary.get("repaired", 0),
                        "repaired_device_ids": summary.get("repaired_device_ids", []),
                    },
                )
        except Exception as exc:
            logger.error(
                "Activation backfill failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def run_state_interval_retention_cycle(
    *,
    session_factory: Callable[..., Any],
) -> None:
    from app.services.device_state_intervals import DeviceStateIntervalService

    try:
        async with session_factory() as session:
            tenant_ids = await load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("State interval retention tenant discovery failed", extra={"error": str(exc)})
        return

    retention_days = max(1, int(settings.STATE_INTERVAL_RETENTION_DAYS))
    batch_size = max(1, int(settings.STATE_INTERVAL_CLEANUP_BATCH_SIZE))
    max_batches = max(1, int(settings.STATE_INTERVAL_CLEANUP_MAX_BATCHES_PER_RUN))
    stale_open_alert_days = max(1, int(settings.STATE_INTERVAL_STALE_OPEN_ALERT_DAYS))

    for tenant_id in tenant_ids:
        try:
            async with session_factory() as session:
                service = DeviceStateIntervalService(session)
                cleanup = await service.cleanup_closed_intervals_for_tenant(
                    tenant_id=tenant_id,
                    retention_days=retention_days,
                    batch_size=batch_size,
                    max_batches=max_batches,
                )
                observability = await service.collect_open_interval_observability(
                    tenant_id=tenant_id,
                    stale_open_alert_days=stale_open_alert_days,
                )
                await session.commit()

                logger.info(
                    "State interval retention cycle completed",
                    extra={
                        "tenant_id": tenant_id,
                        "retention_days": retention_days,
                        "batch_size": batch_size,
                        "max_batches_per_run": max_batches,
                        "deleted": cleanup.get("deleted", 0),
                        "batches": cleanup.get("batches", 0),
                        "cutoff_ts": cleanup.get("cutoff_ts"),
                        "open_total": observability.get("open_total", 0),
                        "open_counts_by_state": observability.get("open_counts_by_state", {}),
                        "stale_open_count": observability.get("stale_open_count", 0),
                    },
                )
                if int(observability.get("stale_open_count", 0)) > 0:
                    logger.warning(
                        "State interval stale open rows detected",
                        extra={
                            "tenant_id": tenant_id,
                            "stale_open_count": observability.get("stale_open_count", 0),
                            "stale_open_alert_days": stale_open_alert_days,
                        },
                    )
        except Exception as exc:
            logger.error(
                "State interval retention cycle failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def run_dashboard_snapshot_retention_cycle(
    *,
    session_factory: Callable[..., Any],
) -> None:
    from app.services.dashboard import DashboardService

    cutoff = datetime.now(timezone.utc)
    batch_size = max(1, int(settings.DASHBOARD_SNAPSHOT_CLEANUP_BATCH_SIZE))

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(DashboardSnapshot.tenant_id, DashboardSnapshot.snapshot_key, DashboardSnapshot.s3_key)
                    .where(DashboardSnapshot.expires_at.is_not(None))
                    .where(DashboardSnapshot.expires_at < cutoff)
                    .order_by(DashboardSnapshot.expires_at.asc())
                    .limit(batch_size)
                )
            ).all()
        )
        if not rows:
            return

        s3_keys_to_delete = [row.s3_key for row in rows if row.s3_key]
        if s3_keys_to_delete:
            await asyncio.to_thread(
                DashboardService._delete_expired_snapshots_from_storage,
                s3_keys_to_delete,
            )

        await session.execute(
            delete(DashboardSnapshot).where(
                tuple_(DashboardSnapshot.tenant_id, DashboardSnapshot.snapshot_key).in_(
                    [(row.tenant_id, row.snapshot_key) for row in rows]
                )
            )
        )
        await session.commit()
        logger.info(
            "Dashboard snapshot retention cycle completed",
            extra={"deleted": len(rows), "cutoff": cutoff.isoformat()},
        )
