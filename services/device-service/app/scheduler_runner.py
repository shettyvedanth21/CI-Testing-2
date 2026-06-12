"""Standalone scheduler runtime for device-service background work."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.config import settings, validate_dependency_dns
from app.database import SchedulerSessionLocal, scheduler_engine
from app.logging_config import configure_logging
from app.scheduler_helpers import (
    load_active_tenant_ids,
    run_live_projection_reconciliation_cycle,
    run_activation_backfill_cycle,
    run_state_interval_retention_cycle,
    run_dashboard_snapshot_retention_cycle,
)
from services.shared.startup_contract import validate_startup_contract
from services.shared.tenant_context import TenantContext
from sqlalchemy import select

import logging

logger = logging.getLogger(__name__)

_load_active_tenant_ids = load_active_tenant_ids


async def _run_live_projection_reconciliation_cycle(*, refresh_fleet_snapshot: bool) -> None:
    await run_live_projection_reconciliation_cycle(
        refresh_fleet_snapshot=refresh_fleet_snapshot,
        session_factory=SchedulerSessionLocal,
    )


async def _run_activation_backfill_cycle() -> None:
    await run_activation_backfill_cycle(session_factory=SchedulerSessionLocal)


async def _run_state_interval_retention_cycle() -> None:
    await run_state_interval_retention_cycle(session_factory=SchedulerSessionLocal)


async def _run_dashboard_snapshot_retention_cycle() -> None:
    await run_dashboard_snapshot_retention_cycle(session_factory=SchedulerSessionLocal)


async def _run_performance_trends_once() -> None:
    from app.services.idle_running import IdleRunningService
    from app.services.performance_trends import PerformanceTrendService

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Performance trends tenant discovery failed", extra={"error": str(exc)})
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
            async with SchedulerSessionLocal() as session:
                service = PerformanceTrendService(session, tenant_ctx)
                summary = await service.materialize_latest_bucket()
                idle_service = IdleRunningService(session, tenant_ctx)
                idle_summary = await idle_service.aggregate_all_configured_devices()
                logger.info(
                    "Performance trends and idle aggregation cycle completed",
                    extra={
                        "tenant_id": tenant_id,
                        "devices_total": summary.get("devices_total", 0),
                        "created_count": summary.get("created", 0),
                        "updated_count": summary.get("updated", 0),
                        "failed_count": summary.get("failed", 0),
                        "idle_processed": idle_summary.get("processed", 0),
                        "idle_failed": idle_summary.get("failed", 0),
                    },
                )
        except Exception as exc:
            logger.error(
                "Performance trends scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_dashboard_snapshot_once() -> None:
    from app.services.dashboard import DashboardService

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Dashboard tenant discovery failed", extra={"error": str(exc)})
        return

    now_utc = datetime.now(timezone.utc)
    for tenant_id in tenant_ids:
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="system-scheduler",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )
        try:
            async with SchedulerSessionLocal() as session:
                service = DashboardService(session, tenant_ctx)
                await service.materialize_energy_and_loss_snapshots()
                current_local = now_utc.astimezone().replace(tzinfo=None)
                await service.materialize_monthly_energy_snapshot(
                    year=current_local.year,
                    month=current_local.month,
                )
                await service.materialize_dashboard_summary_snapshot()
                logger.info(
                    "Dashboard snapshot cycle completed",
                    extra={
                        "tenant_id": tenant_id,
                        "hot_interval_seconds": max(1, int(settings.DASHBOARD_SNAPSHOT_INTERVAL_SECONDS)),
                        "energy_refreshed": True,
                        "energy_interval_seconds": max(
                            max(1, int(settings.DASHBOARD_SNAPSHOT_INTERVAL_SECONDS)),
                            int(settings.DASHBOARD_ENERGY_REFRESH_SECONDS),
                        ),
                    },
                )
        except Exception as exc:
            logger.error(
                "Dashboard snapshot scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_recent_telemetry_cleanup_once() -> None:
    from app.services.live_projection import LiveProjectionService

    batch_size = max(50, int(settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_BATCH_SIZE))
    try:
        async with SchedulerSessionLocal() as session:
            service = LiveProjectionService(session)
            summary = await service.cleanup_recent_telemetry_overflow(batch_size=batch_size)
            if summary["cleaned"] > 0:
                logger.info("Recent telemetry sample overflow cleanup completed", extra=summary)
    except Exception as exc:
        logger.error(
            "Recent telemetry sample overflow cleanup failed",
            extra={"error": str(exc)},
        )


async def _run_degradation_feature_window_once() -> None:
    from app.models.device import Device, DeviceRecentTelemetrySample
    from app.services.degradation.service import (
        TelemetrySample,
        build_feature_window_from_samples,
        persist_feature_window,
    )

    interval_seconds = max(300, int(settings.DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS))

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Degradation feature window tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                for device_id in device_ids:
                    try:
                        async with session.begin_nested():
                            latest_result = await session.execute(
                                select(DeviceRecentTelemetrySample.sample_ts)
                                .where(
                                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                                    DeviceRecentTelemetrySample.device_id == device_id,
                                )
                                .order_by(DeviceRecentTelemetrySample.sample_ts.desc())
                                .limit(1)
                            )
                            latest_sample_ts = latest_result.scalar_one_or_none()
                            if latest_sample_ts is None:
                                continue

                            latest_ts = latest_sample_ts.astimezone(timezone.utc)
                            epoch_seconds = int(latest_ts.timestamp())
                            window_end_epoch = epoch_seconds - (epoch_seconds % interval_seconds)
                            if window_end_epoch <= 0:
                                continue

                            window_end = datetime.fromtimestamp(window_end_epoch, tz=timezone.utc)
                            window_start = window_end - timedelta(seconds=interval_seconds)

                            sample_result = await session.execute(
                                select(DeviceRecentTelemetrySample.telemetry_json)
                                .where(
                                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                                    DeviceRecentTelemetrySample.device_id == device_id,
                                    DeviceRecentTelemetrySample.sample_ts >= window_start,
                                    DeviceRecentTelemetrySample.sample_ts < window_end,
                                )
                                .order_by(DeviceRecentTelemetrySample.sample_ts.asc())
                            )

                            telemetry_samples: list[TelemetrySample] = []
                            for telemetry_json in sample_result.scalars().all():
                                try:
                                    payload = json.loads(telemetry_json)
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    continue

                                timestamp_raw = payload.get("timestamp")
                                if not timestamp_raw:
                                    continue
                                try:
                                    sample_ts = datetime.fromisoformat(str(timestamp_raw).replace("Z", "+00:00"))
                                except ValueError:
                                    continue

                                telemetry_samples.append(
                                    TelemetrySample(
                                        timestamp=sample_ts,
                                        current_avg=payload.get("current_avg", payload.get("current")),
                                        current_l1=payload.get("current_l1"),
                                        current_l2=payload.get("current_l2"),
                                        current_l3=payload.get("current_l3"),
                                        power=payload.get("power"),
                                        power_factor=payload.get("power_factor"),
                                        voltage_avg=payload.get("voltage_avg", payload.get("voltage")),
                                        voltage_l1=payload.get("voltage_l1"),
                                        voltage_l2=payload.get("voltage_l2"),
                                        voltage_l3=payload.get("voltage_l3"),
                                        frequency=payload.get("frequency"),
                                        energy_kwh=payload.get("energy_kwh"),
                                    )
                                )

                            if not telemetry_samples:
                                continue

                            window_dict = build_feature_window_from_samples(
                                telemetry_samples,
                                tenant_id,
                                device_id,
                                window_start,
                                window_end,
                            )
                            await persist_feature_window(session, window_dict)
                        await session.commit()
                        logger.debug(
                            "Degradation feature window cycle device processed",
                            extra={
                                "tenant_id": tenant_id,
                                "device_id": device_id,
                                "window_start": window_start.isoformat(),
                                "sample_count": len(telemetry_samples),
                            },
                        )
                    except Exception as exc:
                        logger.error(
                            "Degradation feature window device failed",
                            extra={"tenant_id": tenant_id, "device_id": device_id, "error": str(exc)},
                        )
            logger.info(
                "Degradation feature window cycle completed",
                extra={"tenant_id": tenant_id, "devices": len(device_ids)},
            )
        except Exception as exc:
            logger.error(
                "Degradation feature window scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_degradation_baseline_once() -> None:
    from app.models.device import Device, MachineHealthBaseline
    from app.services.degradation.service import (
        load_feature_windows_for_baseline,
        learn_baseline_from_windows,
        persist_baseline,
    )

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Degradation baseline tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                for device_id in device_ids:
                    try:
                        async with session.begin_nested():
                            windows = await load_feature_windows_for_baseline(
                                session,
                                tenant_id,
                                device_id,
                                minimum_days=settings.DEGRADATION_BASELINE_MINIMUM_DAYS,
                                interval_seconds=settings.DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS,
                            )
                            if not windows:
                                continue
                            existing = await session.execute(
                                select(MachineHealthBaseline).where(
                                    MachineHealthBaseline.tenant_id == tenant_id,
                                    MachineHealthBaseline.device_id == device_id,
                                    MachineHealthBaseline.status == "active",
                                )
                            )
                            active_baseline = existing.scalar_one_or_none()
                            version = (active_baseline.baseline_version + 1) if active_baseline else 1
                            baseline_dict = learn_baseline_from_windows(
                                windows, tenant_id, device_id, baseline_version=version,
                                minimum_days=settings.DEGRADATION_BASELINE_MINIMUM_DAYS,
                            )
                            await persist_baseline(session, baseline_dict)
                        await session.commit()
                    except Exception as exc:
                        logger.error(
                            "Degradation baseline device failed",
                            extra={"tenant_id": tenant_id, "device_id": device_id, "error": str(exc)},
                        )
            logger.info(
                "Degradation baseline cycle completed",
                extra={"tenant_id": tenant_id, "devices": len(device_ids)},
            )
        except Exception as exc:
            logger.error(
                "Degradation baseline scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_degradation_scoring_once() -> None:
    from app.models.device import Device
    from app.services.degradation.service import (
        score_device,
        build_latest_score_snapshot,
        build_history_entry,
        persist_latest_snapshot,
        persist_history_entry,
    )

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Degradation scoring tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                scored_count = 0
                for device_id in device_ids:
                    try:
                        async with session.begin_nested():
                            score_result = await score_device(session, tenant_id, device_id)
                            if score_result is None:
                                continue
                            now_utc = datetime.now(timezone.utc)
                            snapshot_dict = build_latest_score_snapshot(
                                score_result, tenant_id, device_id,
                                computed_at=now_utc,
                            )
                            await persist_latest_snapshot(session, snapshot_dict)
                            history_dict = build_history_entry(
                                score_result, tenant_id, device_id,
                                computed_at=now_utc,
                            )
                            await persist_history_entry(session, history_dict)
                        await session.commit()
                        scored_count += 1
                    except Exception as exc:
                        logger.error(
                            "Degradation scoring device failed",
                            extra={"tenant_id": tenant_id, "device_id": device_id, "error": str(exc)},
                        )
            logger.info(
                "Degradation scoring cycle completed",
                extra={"tenant_id": tenant_id, "devices_scored": scored_count, "devices_total": len(device_ids)},
            )
        except Exception as exc:
            logger.error(
                "Degradation scoring scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_degradation_cleanup_once() -> None:
    from app.services.degradation.service import cleanup_old_degradation_rows

    retention_days = max(1, int(settings.DEGRADATION_RETENTION_DAYS))
    try:
        async with SchedulerSessionLocal() as session:
            summary = await cleanup_old_degradation_rows(session, retention_days=retention_days)
            await session.commit()
            if summary["deleted"] > 0:
                logger.info(
                    "Degradation retention cleanup completed",
                    extra={"retention_days": retention_days, **summary},
                )
    except Exception as exc:
        logger.error(
            "Degradation retention cleanup failed",
            extra={"error": str(exc)},
        )


async def _run_anomaly_baseline_once() -> None:
    from app.models.device import Device
    from app.services.anomaly.service import refresh_anomaly_baselines_for_device

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Anomaly baseline tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                for device_id in device_ids:
                    try:
                        async with session.begin_nested():
                            persisted = await refresh_anomaly_baselines_for_device(
                                session, tenant_id, device_id,
                                minimum_days=settings.ANOMALY_BASELINE_MINIMUM_DAYS,
                                interval_seconds=settings.DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS,
                            )
                        await session.commit()
                        if persisted > 0:
                            logger.debug(
                                "Anomaly baseline device refreshed",
                                extra={"tenant_id": tenant_id, "device_id": device_id, "persisted": persisted},
                            )
                    except Exception as exc:
                        logger.error(
                            "Anomaly baseline device failed",
                            extra={"tenant_id": tenant_id, "device_id": device_id, "error": str(exc)},
                        )
            logger.info(
                "Anomaly baseline cycle completed",
                extra={"tenant_id": tenant_id, "devices": len(device_ids)},
            )
        except Exception as exc:
            logger.error(
                "Anomaly baseline scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_anomaly_detection_once() -> None:
    from app.models.device import Device
    from app.services.anomaly.service import detect_device_anomalies

    max_open_hours = max(1, int(settings.ANOMALY_MAX_OPEN_EVENT_AGE_HOURS))
    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Anomaly detection tenant discovery failed", extra={"error": str(exc)})
        return

    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                detected_count = 0
                for device_id in device_ids:
                    try:
                        async with session.begin_nested():
                            summary = await detect_device_anomalies(
                                session, tenant_id, device_id,
                                max_open_event_age_hours=max_open_hours,
                            )
                        await session.commit()
                        if summary["new_events"] > 0 or summary["extended_events"] > 0:
                            detected_count += 1
                    except Exception as exc:
                        logger.error(
                            "Anomaly detection device failed",
                            extra={"tenant_id": tenant_id, "device_id": device_id, "error": str(exc)},
                        )
            logger.info(
                "Anomaly detection cycle completed",
                extra={"tenant_id": tenant_id, "devices_with_events": detected_count, "devices_total": len(device_ids)},
            )
        except Exception as exc:
            logger.error(
                "Anomaly detection scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_anomaly_daily_count_once() -> None:
    from app.models.device import Device, MachineAnomalyEvent
    from app.services.anomaly.service import aggregate_daily_counts_for_device
    from app.services.anomaly.tz import local_today

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Anomaly daily count tenant discovery failed", extra={"error": str(exc)})
        return

    today = local_today()
    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                aggregated = 0
                for device_id in device_ids:
                    for days_ago in range(3):
                        target_date = today - timedelta(days=days_ago)
                        try:
                            async with session.begin_nested():
                                res = await aggregate_daily_counts_for_device(
                                    session, tenant_id, device_id, target_date,
                                )
                            await session.commit()
                            if res is not None:
                                aggregated += 1
                        except Exception as exc:
                            logger.error(
                                "Anomaly daily count device failed",
                                extra={"tenant_id": tenant_id, "device_id": device_id, "date": str(target_date), "error": str(exc)},
                            )
            logger.info(
                "Anomaly daily count cycle completed",
                extra={"tenant_id": tenant_id, "aggregated": aggregated},
            )
        except Exception as exc:
            logger.error(
                "Anomaly daily count scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_anomaly_weekly_count_once() -> None:
    from app.models.device import Device
    from app.services.anomaly.service import aggregate_weekly_counts_for_device
    from app.services.anomaly.tz import local_today

    try:
        async with SchedulerSessionLocal() as session:
            tenant_ids = await _load_active_tenant_ids(session)
    except Exception as exc:
        logger.error("Anomaly weekly count tenant discovery failed", extra={"error": str(exc)})
        return

    today = local_today()
    week_start = today - timedelta(days=today.weekday())
    for tenant_id in tenant_ids:
        try:
            async with SchedulerSessionLocal() as session:
                result = await session.execute(
                    select(Device.device_id).where(Device.tenant_id == tenant_id)
                )
                device_ids = [row[0] for row in result.all()]
                aggregated = 0
                for device_id in device_ids:
                    for week_offset in range(2):
                        ws = week_start - timedelta(weeks=week_offset)
                        try:
                            async with session.begin_nested():
                                res = await aggregate_weekly_counts_for_device(
                                    session, tenant_id, device_id, ws,
                                )
                            await session.commit()
                            if res is not None:
                                aggregated += 1
                        except Exception as exc:
                            logger.error(
                                "Anomaly weekly count device failed",
                                extra={"tenant_id": tenant_id, "device_id": device_id, "week": str(ws), "error": str(exc)},
                            )
            logger.info(
                "Anomaly weekly count cycle completed",
                extra={"tenant_id": tenant_id, "aggregated": aggregated},
            )
        except Exception as exc:
            logger.error(
                "Anomaly weekly count scheduler failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )


async def _run_anomaly_cleanup_once() -> None:
    from app.services.anomaly.service import cleanup_old_anomaly_rows

    retention_days = max(1, int(settings.ANOMALY_RETENTION_DAYS))
    try:
        async with SchedulerSessionLocal() as session:
            summary = await cleanup_old_anomaly_rows(session, retention_days=retention_days)
            await session.commit()
            if summary["deleted"] > 0:
                logger.info(
                    "Anomaly retention cleanup completed",
                    extra={"retention_days": retention_days, **summary},
                )
    except Exception as exc:
        logger.error(
            "Anomaly retention cleanup failed",
            extra={"error": str(exc)},
        )


async def _run_with_interval(stop_event: asyncio.Event, interval_seconds: int, coroutine_factory) -> None:
    while not stop_event.is_set():
        try:
            await coroutine_factory()
        except Exception as exc:
            logger.error(
                "scheduler_cycle_unhandled_exception",
                extra={"error": str(exc)},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass


async def run_scheduler_runtime() -> None:
    """Run scheduler-only device-service background work."""
    validate_startup_contract()
    configure_logging()
    logger.info(
        "Starting device-service scheduler runtime",
        extra={
            "service": "device-service-scheduler",
            "environment": settings.ENVIRONMENT,
        },
    )
    validate_dependency_dns(log_failures=False)

    if settings.DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE:
        await _run_live_projection_reconciliation_cycle(refresh_fleet_snapshot=True)
        await _run_activation_backfill_cycle()

    stop_event = asyncio.Event()
    tasks: list[asyncio.Task] = []

    if settings.PERFORMANCE_TRENDS_ENABLED and settings.PERFORMANCE_TRENDS_CRON_ENABLED:
        interval_minutes = max(1, settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES)
        tasks.append(
            asyncio.create_task(
                _run_with_interval(stop_event, interval_minutes * 60, _run_performance_trends_once)
            )
        )

    if settings.DASHBOARD_SNAPSHOT_ENABLED:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(1, int(settings.DASHBOARD_SNAPSHOT_INTERVAL_SECONDS)),
                    _run_dashboard_snapshot_once,
                )
            )
        )

    if settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS > 0:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(60, int(settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS)),
                    lambda: _run_live_projection_reconciliation_cycle(refresh_fleet_snapshot=True),
                )
            )
        )

    if settings.STATE_INTERVAL_RETENTION_ENABLED and settings.STATE_INTERVAL_RETENTION_DAYS > 0:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.STATE_INTERVAL_CLEANUP_INTERVAL_SECONDS)),
                    _run_state_interval_retention_cycle,
                )
            )
        )

    if settings.DASHBOARD_SNAPSHOT_CLEANUP_ENABLED and settings.DASHBOARD_SNAPSHOT_TTL_SECONDS > 0:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.DASHBOARD_SNAPSHOT_CLEANUP_INTERVAL_SECONDS)),
                    _run_dashboard_snapshot_retention_cycle,
                )
            )
        )

    if settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(60, int(settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_INTERVAL_SECONDS)),
                    _run_recent_telemetry_cleanup_once,
                )
            )
        )

    if settings.DEGRADATION_ENABLED:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.DEGRADATION_FEATURE_WINDOW_INTERVAL_SECONDS)),
                    _run_degradation_feature_window_once,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.DEGRADATION_BASELINE_INTERVAL_SECONDS)),
                    _run_degradation_baseline_once,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.DEGRADATION_SCORING_INTERVAL_SECONDS)),
                    _run_degradation_scoring_once,
                )
            )
        )
        if settings.DEGRADATION_RETENTION_DAYS > 0:
            tasks.append(
                asyncio.create_task(
                    _run_with_interval(
                        stop_event,
                        max(300, int(settings.DEGRADATION_CLEANUP_INTERVAL_SECONDS)),
                        _run_degradation_cleanup_once,
                    )
                )
            )

    if settings.ANOMALY_ENABLED:
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.ANOMALY_BASELINE_INTERVAL_SECONDS)),
                    _run_anomaly_baseline_once,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.ANOMALY_DETECTION_INTERVAL_SECONDS)),
                    _run_anomaly_detection_once,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.ANOMALY_DAILY_AGGREGATION_INTERVAL_SECONDS)),
                    _run_anomaly_daily_count_once,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _run_with_interval(
                    stop_event,
                    max(300, int(settings.ANOMALY_WEEKLY_AGGREGATION_INTERVAL_SECONDS)),
                    _run_anomaly_weekly_count_once,
                )
            )
        )
        if settings.ANOMALY_RETENTION_DAYS > 0:
            tasks.append(
                asyncio.create_task(
                    _run_with_interval(
                        stop_event,
                        max(300, int(settings.ANOMALY_CLEANUP_INTERVAL_SECONDS)),
                        _run_anomaly_cleanup_once,
                    )
                )
            )

    if not tasks:
        logger.warning("No device-service schedulers enabled; scheduler runtime exiting")
        await scheduler_engine.dispose()
        return

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.error(
                    "scheduler_task_terminated",
                    extra={"task_index": i, "error": str(result)},
                )
    except asyncio.CancelledError:
        logger.info("device-service scheduler runtime cancelled")
        raise
    finally:
        stop_event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        from app.services.shared_http import close_all as close_shared_http_clients
        await close_shared_http_clients()
        await scheduler_engine.dispose()
        logger.info("device-service scheduler runtime stopped")


def main() -> None:
    asyncio.run(run_scheduler_runtime())


if __name__ == "__main__":
    main()
