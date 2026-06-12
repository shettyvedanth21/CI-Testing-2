"""Admission control and observability policy for analytics queueing."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.config.settings import Settings
from src.models.schemas import JobStatus
from src.services.result_repository import ResultRepository


@dataclass
class AnalyticsAdmissionDecision:
    """Outcome of submission-time capacity checks."""

    allowed: bool
    status_code: int
    error_code: Optional[str] = None
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    queue_position: int = 0


class AnalyticsScalingPolicy:
    """Enforces tenant fairness and backlog safety before enqueue."""

    def __init__(self, settings: Settings, repo: ResultRepository):
        self._settings = settings
        self._repo = repo

    async def evaluate_submission(
        self,
        *,
        tenant_id: Optional[str],
        requested_jobs: int = 1,
    ) -> AnalyticsAdmissionDecision:
        requested_jobs = max(1, int(requested_jobs))
        safe_backlog_threshold = min(
            max(1, int(self._settings.queue_backlog_reject_threshold)),
            max(1, int(self._settings.queue_max_length)),
        )
        pending_statuses = [JobStatus.PENDING.value]

        global_pending = await self._repo.count_jobs(statuses=pending_statuses)
        queue_position = global_pending

        if global_pending + requested_jobs > safe_backlog_threshold:
            return AnalyticsAdmissionDecision(
                allowed=False,
                status_code=503,
                error_code="ANALYTICS_BACKLOG_OVERLOADED",
                message="Analytics queue backlog is above the safe submission threshold. Please retry shortly.",
                details={
                    "requested_jobs": requested_jobs,
                    "queued_jobs": global_pending,
                    "queue_backlog_reject_threshold": safe_backlog_threshold,
                },
                queue_position=queue_position,
            )

        if tenant_id:
            tenant_pending = await self._repo.count_jobs(
                statuses=pending_statuses,
                tenant_id=tenant_id,
            )

            if tenant_pending + requested_jobs > self._settings.tenant_max_queued_jobs:
                return AnalyticsAdmissionDecision(
                    allowed=False,
                    status_code=429,
                    error_code="TENANT_QUEUE_CAP_EXCEEDED",
                    message="This tenant has reached the queued analytics job limit. Wait for queued work to drain before submitting more.",
                    details={
                        "tenant_id": tenant_id,
                        "requested_jobs": requested_jobs,
                        "tenant_queued_jobs": tenant_pending,
                        "tenant_max_queued_jobs": self._settings.tenant_max_queued_jobs,
                    },
                    queue_position=queue_position,
                )

        return AnalyticsAdmissionDecision(
            allowed=True,
            status_code=202,
            queue_position=queue_position,
            details={
                "requested_jobs": requested_jobs,
                "queued_jobs": global_pending,
            },
        )
