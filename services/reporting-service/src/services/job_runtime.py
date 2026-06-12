from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.energy_reports import EnergyReport, ReportStatus, ReportWorkerHeartbeat


@dataclass
class RuntimeEstimate:
    queue_position: int | None
    estimated_wait_seconds: int | None
    estimated_completion_seconds: int | None
    estimate_quality: str | None


def external_report_status(status: object) -> str:
    resolved = status.value if hasattr(status, "value") else str(status)
    if resolved == ReportStatus.processing.value:
        return "running"
    return resolved


def artifact_download_path(report_id: str) -> str:
    return f"/api/reports/{report_id}/download"


def result_path(report_id: str) -> str:
    return f"/api/reports/{report_id}/result"


def _duration(values: Iterable[float]) -> float | None:
    cleaned = [float(v) for v in values if float(v) > 0]
    if not cleaned:
        return None
    return float(median(cleaned))


def _estimate_quality(sample_size: int) -> str:
    if sample_size >= 20:
        return "high"
    if sample_size >= 8:
        return "medium"
    return "low"


class ReportJobStatusEstimator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def estimate(self, report: EnergyReport) -> RuntimeEstimate:
        durations_q = (
            select(
                EnergyReport.created_at,
                EnergyReport.processing_started_at,
                EnergyReport.completed_at,
            )
            .where(EnergyReport.status == ReportStatus.completed)
            .where(EnergyReport.report_type == report.report_type)
            .order_by(EnergyReport.completed_at.desc())
            .limit(60)
        )
        rows = list((await self._session.execute(durations_q)).all())
        durations: list[float] = []
        for row in rows:
            start = row.processing_started_at or row.created_at
            end = row.completed_at
            if not start or not end:
                continue
            durations.append(max(1.0, (self._as_utc(end) - self._as_utc(start)).total_seconds()))

        sample_size = len(durations)
        baseline = _duration(durations) or 45.0
        queue_position = await self._queue_position(report)
        active_workers = await count_active_workers(self._session)

        wait_seconds: int | None = None
        completion_seconds: int | None = None
        normalized_status = external_report_status(report.status)

        if normalized_status == "pending":
            queue_position = max(0, queue_position or 0)
            wait_seconds = int(max(0.0, (queue_position + 1) * baseline / max(1, active_workers)))
            completion_seconds = wait_seconds + int(baseline)
        elif normalized_status == "running":
            started_at = report.processing_started_at or report.created_at
            if started_at:
                elapsed = max(0.0, (self._utc_now() - self._as_utc(started_at)).total_seconds())
                completion_seconds = max(1, int(baseline - elapsed))

        return RuntimeEstimate(
            queue_position=queue_position if normalized_status == "pending" else None,
            estimated_wait_seconds=wait_seconds,
            estimated_completion_seconds=completion_seconds,
            estimate_quality=_estimate_quality(sample_size),
        )

    async def _queue_position(self, report: EnergyReport) -> int | None:
        normalized_status = external_report_status(report.status)
        if normalized_status != "pending":
            return None
        q = (
            select(func.count())
            .select_from(EnergyReport)
            .where(EnergyReport.status == ReportStatus.pending)
            .where(EnergyReport.created_at < report.created_at)
        )
        if report.tenant_id:
            q = q.where(EnergyReport.tenant_id == report.tenant_id)
        result = await self._session.execute(q)
        return int(result.scalar() or 0)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


async def count_active_workers(session: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(10, settings.REPORT_WORKER_HEARTBEAT_TTL_SECONDS))
    result = await session.execute(
        select(func.count())
        .select_from(ReportWorkerHeartbeat)
        .where(ReportWorkerHeartbeat.last_heartbeat_at >= cutoff)
    )
    return int(result.scalar() or 0)
