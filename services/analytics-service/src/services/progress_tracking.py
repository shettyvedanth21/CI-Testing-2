"""Phase-aware progress model for analytics jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.services.result_repository import ResultRepository


@dataclass(frozen=True)
class PhaseRange:
    start: float
    end: float
    label: str


SINGLE_JOB_PHASES: Dict[str, PhaseRange] = {
    "queued": PhaseRange(0.0, 5.0, "Queued"),
    "readiness": PhaseRange(5.0, 20.0, "Preparing dataset readiness"),
    "dataset_loading": PhaseRange(20.0, 35.0, "Loading dataset"),
    "feature_preparation": PhaseRange(35.0, 48.0, "Preparing features"),
    "model_execution": PhaseRange(48.0, 88.0, "Running model execution"),
    "metrics_formatting": PhaseRange(88.0, 96.0, "Calculating metrics and formatting"),
    "final_persistence": PhaseRange(96.0, 99.0, "Persisting results"),
    "completed": PhaseRange(100.0, 100.0, "Completed"),
    "failed": PhaseRange(0.0, 100.0, "Failed"),
}

FLEET_PARENT_PHASES: Dict[str, PhaseRange] = {
    "queued": PhaseRange(0.0, 5.0, "Queued"),
    "fleet_readiness": PhaseRange(5.0, 20.0, "Checking fleet readiness"),
    "child_submission": PhaseRange(20.0, 35.0, "Submitting child jobs"),
    "child_execution": PhaseRange(35.0, 92.0, "Running child analytics"),
    "aggregation": PhaseRange(92.0, 99.0, "Aggregating fleet results"),
    "completed": PhaseRange(100.0, 100.0, "Completed"),
    "failed": PhaseRange(0.0, 100.0, "Failed"),
}


class JobProgressReporter:
    """Maintains monotonic phase-aware progress updates for a single job."""

    def __init__(
        self,
        result_repo: ResultRepository,
        job_id: str,
        *,
        phase_ranges: Dict[str, PhaseRange],
    ) -> None:
        self._result_repo = result_repo
        self._job_id = job_id
        self._phase_ranges = phase_ranges
        self._last_progress = 0.0

    def _phase_range(self, phase: str) -> PhaseRange:
        return self._phase_ranges.get(phase) or self._phase_ranges["model_execution"]

    def _compute_progress(self, phase: str, phase_progress: float) -> float:
        phase_window = self._phase_range(phase)
        bounded_phase = max(0.0, min(1.0, phase_progress))
        computed = phase_window.start + (phase_window.end - phase_window.start) * bounded_phase
        return max(self._last_progress, min(100.0, computed))

    async def update(
        self,
        phase: str,
        *,
        phase_progress: float,
        message: str,
        phase_label: str | None = None,
    ) -> float:
        progress = self._compute_progress(phase, phase_progress)
        self._last_progress = progress
        effective_label = phase_label or self._phase_range(phase).label
        await self._result_repo.update_job_progress(
            job_id=self._job_id,
            progress=progress,
            message=message,
            phase=phase,
            phase_label=effective_label,
            phase_progress=max(0.0, min(1.0, phase_progress)),
        )
        return progress
