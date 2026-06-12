"""Result repository interface."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.models.schemas import JobStatus

UNSET = object()


class ResultRepository(ABC):
    """Abstract interface for analytics result storage."""

    @abstractmethod
    async def create_job(
        self,
        job_id: str,
        device_id: str,
        analysis_type: str,
        model_name: str,
        date_range_start: datetime,
        date_range_end: datetime,
        parameters: Optional[Dict[str, Any]],
        job_kind: str = "single",
        parent_job_id: Optional[str] = None,
    ) -> None:
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Any:
        pass

    @abstractmethod
    async def get_job_scoped(
        self,
        job_id: str,
        tenant_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> Any:
        pass

    @abstractmethod
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        phase: Optional[str] = None,
        phase_label: Optional[str] = None,
        phase_progress: Optional[float] = None,
    ) -> None:
        pass

    @abstractmethod
    async def update_job_progress(
        self,
        job_id: str,
        progress: float,
        message: str,
        phase: Optional[str] = None,
        phase_label: Optional[str] = None,
        phase_progress: Optional[float] = None,
    ) -> None:
        pass

    @abstractmethod
    async def save_results(
        self,
        job_id: str,
        results: Dict[str, Any],
        accuracy_metrics: Optional[Dict[str, float]],
        execution_time_seconds: int,
    ) -> None:
        pass

    @abstractmethod
    async def list_jobs(
        self,
        status: Optional[str] = None,
        device_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
        job_kinds: Optional[list[str]] = None,
        parent_job_id: Optional[str] = None,
        only_undispatched: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Any]:
        pass

    @abstractmethod
    async def list_jobs_for_worker_scan(
        self,
        *,
        status: Optional[str] = None,
        job_kinds: Optional[list[str]] = None,
        only_undispatched: Optional[bool] = None,
        limit: int = 5000,
    ) -> List[Any]:
        """Return narrow worker-safe job rows without large result payloads or forced sort order."""
        pass

    @abstractmethod
    async def list_running_jobs_for_recovery(
        self,
        *,
        limit: int = 5000,
    ) -> List[Any]:
        """Return narrow running job rows used for stale worker lease recovery."""
        pass

    @abstractmethod
    async def update_job_queue_metadata(
        self,
        job_id: str,
        attempt: Optional[int] = None,
        queue_position: Optional[int] = None,
        queue_enqueued_at: Optional[datetime] | object = UNSET,
        queue_dispatched_at: Optional[datetime] | object = UNSET,
        queue_started_at: Optional[datetime] | object = UNSET,
        worker_lease_expires_at: Optional[datetime] | object = UNSET,
        last_heartbeat_at: Optional[datetime] | object = UNSET,
        error_code: Optional[str] | object = UNSET,
    ) -> None:
        pass

    @abstractmethod
    async def claim_child_dispatch(
        self,
        job_id: str,
        dispatched_at: datetime,
        attempt: Optional[int] = None,
    ) -> bool:
        """Atomically reserve a pending fleet child for queue dispatch."""
        pass

    @abstractmethod
    async def release_child_dispatch(
        self,
        job_id: str,
    ) -> None:
        """Release a previously reserved fleet child dispatch if queue submission fails."""
        pass

    # -------------------------------------------------
    # ✅ REQUIRED for async SQLAlchemy correctness
    # -------------------------------------------------
    @abstractmethod
    async def rollback(self) -> None:
        """Rollback current transaction."""
        pass

    @abstractmethod
    async def count_jobs(
        self,
        statuses: Optional[list[str]] = None,
        tenant_id: Optional[str] = None,
        attempts_gte: Optional[int] = None,
        job_kinds: Optional[list[str]] = None,
        parent_job_id: Optional[str] = None,
        only_undispatched: Optional[bool] = None,
    ) -> int:
        """Return count of jobs matching the given filters."""
        pass

    @abstractmethod
    async def list_tenant_job_counts(
        self,
        statuses: Optional[list[str]] = None,
        job_kinds: Optional[list[str]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return top tenants by job volume for the given statuses."""
        pass

    @abstractmethod
    async def list_jobs_for_parent(
        self,
        parent_job_id: str,
    ) -> List[Any]:
        """Return child jobs for a fleet parent ordered by creation time."""
        pass

    @abstractmethod
    async def find_active_duplicate(
        self,
        *,
        tenant_id: str,
        device_id: str,
        analysis_type: str,
        model_name: str,
    ) -> Optional[Any]:
        """Return a pending/running duplicate job for the same device + analysis_type + model, if any."""
        pass

    @abstractmethod
    async def get_model_artifact(
        self,
        tenant_id: Optional[str],
        device_id: str,
        analysis_type: str,
        model_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Return latest artifact payload + metadata for model or None."""
        pass

    @abstractmethod
    async def upsert_model_artifact(
        self,
        tenant_id: Optional[str],
        device_id: str,
        analysis_type: str,
        model_key: str,
        feature_schema_hash: str,
        artifact_payload: bytes,
        model_version: str = "v1",
        metrics: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        """Create/update latest artifact for device/model/schema."""
        pass
