"""Tests for phase-aware progress tracking."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.services.progress_tracking import JobProgressReporter, SINGLE_JOB_PHASES


@pytest.mark.asyncio
async def test_progress_reporter_is_monotonic_within_phase_ranges():
    repo = AsyncMock()
    reporter = JobProgressReporter(repo, "job-1", phase_ranges=SINGLE_JOB_PHASES)

    await reporter.update("dataset_loading", phase_progress=0.2, message="loading")
    await reporter.update("dataset_loading", phase_progress=0.1, message="loading retry")
    await reporter.update("model_execution", phase_progress=0.5, message="running")

    calls = repo.update_job_progress.await_args_list
    first = float(calls[0].kwargs["progress"])
    second = float(calls[1].kwargs["progress"])
    third = float(calls[2].kwargs["progress"])

    assert second >= first
    assert third > second
    assert third <= SINGLE_JOB_PHASES["model_execution"].end
