from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WasteAnalysisRunRequest(BaseModel):
    job_name: Optional[str] = Field(default=None, max_length=255)
    scope: Literal["all", "selected"]
    device_ids: Optional[list[str]] = None
    start_date: date
    end_date: date
    granularity: Literal["daily", "weekly", "monthly"] = "daily"


class WasteAnalysisRunResponse(BaseModel):
    job_id: str
    status: str
    backend_status: str
    estimated_completion_seconds: int
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    progress_pct: int = 0
    stage: Optional[str] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    phase_progress: float | None = None
    result_ready: bool = False
    artifact_ready: bool = False
    download_ready: bool = False
    result_url: str | None = None
    download_url: str | None = None
    scope: Literal["all", "selected"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    granularity: Literal["daily", "weekly", "monthly"] | None = None
    requested_device_count: int | None = None
    coverage_result: dict | None = None


class WasteStatusResponse(BaseModel):
    job_id: str
    status: str
    backend_status: str
    progress_pct: int
    stage: Optional[str] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    phase_progress: float | None = None
    result_ready: bool = False
    artifact_ready: bool = False
    download_ready: bool = False
    result_url: str | None = None
    download_url: str | None = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    scope: Literal["all", "selected"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    granularity: Literal["daily", "weekly", "monthly"] | None = None
    requested_device_count: int | None = None
    coverage_result: dict | None = None


class WasteDownloadResponse(BaseModel):
    job_id: str
    status: str
    download_url: str
    expires_in_seconds: int = 900
    result_ready: bool = False
    artifact_ready: bool = False
    download_ready: bool = False


class WasteHistoryItem(BaseModel):
    job_id: str
    job_name: Optional[str]
    status: str
    backend_status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str]
    started_at: Optional[str] = None
    completed_at: Optional[str]
    progress_pct: int
    stage: Optional[str] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    phase_progress: float | None = None
    result_ready: bool = False
    artifact_ready: bool = False
    download_ready: bool = False
    result_url: str | None = None
    download_url: str | None = None
    scope: Literal["all", "selected"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    granularity: Literal["daily", "weekly", "monthly"] | None = None
    requested_device_count: int | None = None
    coverage_result: dict | None = None


class WasteHistoryResponse(BaseModel):
    items: list[WasteHistoryItem]
