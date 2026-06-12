"""Pydantic schemas for API requests and responses."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class AppBaseModel(BaseModel):
    model_config = {"protected_namespaces": ()}


class AnalyticsType(str, Enum):
    ANOMALY = "anomaly"
    PREDICTION = "prediction"
    FORECAST = "forecast"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyticsRequest(AppBaseModel):
    """
    Analytics job request.

    At least one of the following must be provided:
      - dataset_key
      - start_time + end_time

    When both are provided, dataset_key is used for loading and time range is
    retained for audit/metadata purposes.
    """

    device_id: str

    dataset_key: Optional[str] = None

    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    analysis_type: AnalyticsType
    model_name: str
    parameters: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_dataset_or_timerange(self):
        has_dataset = self.dataset_key is not None
        has_range = self.start_time is not None and self.end_time is not None

        if not has_dataset and not has_range:
            raise ValueError(
                "Either dataset_key or start_time/end_time must be provided"
            )

        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be after start_time")

        return self


class AnalyticsPreflightRequest(AppBaseModel):
    device_ids: List[str] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_timerange(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if not self.device_ids:
            raise ValueError("At least one device_id must be provided")
        return self


class AnalyticsPreflightDeviceStatus(AppBaseModel):
    device_id: str
    has_telemetry_in_range: bool
    reason: Literal["telemetry_available", "no_telemetry_in_range", "availability_check_failed"]
    message: str


class AnalyticsPreflightResponse(AppBaseModel):
    devices: List[AnalyticsPreflightDeviceStatus]
    checked_device_count: int
    devices_with_telemetry: int
    devices_without_telemetry: int
    devices_unverified: int
    guaranteed_no_data: bool
    message: str
    coverage_result: Optional[Dict[str, Any]] = None


class AnalyticsJobResponse(AppBaseModel):
    job_id: str
    status: JobStatus
    message: str


class FleetAnalyticsRequest(AppBaseModel):
    """Fleet analytics request for strict all-device orchestration."""

    device_ids: List[str] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime
    analysis_type: Literal["anomaly", "prediction"]
    model_name: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_timerange(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class FleetProgressResponse(AppBaseModel):
    selected_device_count: Optional[int] = None
    child_jobs_total: Optional[int] = None
    queued_devices: Optional[int] = None
    running_devices: Optional[int] = None
    completed_devices: Optional[int] = None
    failed_devices: Optional[int] = None
    skipped_devices: Optional[int] = None
    coverage_pct: Optional[float] = None


class JobStatusResponse(AppBaseModel):
    job_id: str
    status: JobStatus
    workflow_kind: Optional[Literal["single", "fleet"]] = None
    progress: Optional[float] = Field(None, ge=0, le=100)
    message: Optional[str] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    queue_position: Optional[int] = None
    attempt: Optional[int] = None
    worker_lease_expires_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    estimated_wait_seconds: Optional[int] = None
    estimated_completion_seconds: Optional[int] = None
    estimate_quality: Optional[Literal["low", "medium", "high"]] = None
    activity_state: Optional[Literal["active", "stalled", "unknown"]] = None
    eta_reliable: Optional[bool] = None
    heartbeat_age_seconds: Optional[int] = None
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    phase_progress: Optional[float] = Field(None, ge=0, le=1)
    result_ready: bool = False
    artifact_ready: bool = False
    download_ready: bool = False
    result_url: Optional[str] = None
    download_url: Optional[str] = None
    fleet_progress: Optional[FleetProgressResponse] = None
    coverage_result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------
# PERMANENT METRICS CONTRACT
# ---------------------------------------------------------

class AccuracyMetrics(AppBaseModel):
    # shared / generic
    accuracy: Optional[float] = None

    # classification (failure prediction)
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None

    # IMPORTANT: can be None when only one class is present
    auc_roc: Optional[float] = None

    # regression / forecasting
    mae: Optional[float] = None
    rmse: Optional[float] = None
    mape: Optional[float] = None

    # convenience fields used by your pipelines
    mean_actual: Optional[float] = None
    mean_predicted: Optional[float] = None


class AnalyticsResultsResponse(AppBaseModel):
    job_id: str
    status: JobStatus
    device_id: str
    analysis_type: AnalyticsType
    model_name: str

    date_range_start: Optional[datetime]
    date_range_end: Optional[datetime]

    results: Dict[str, Any]
    coverage_result: Optional[Dict[str, Any]] = None

    # 👇 this is the real fix
    accuracy_metrics: Optional[AccuracyMetrics] = None

    execution_time_seconds: Optional[int] = None
    completed_at: Optional[datetime] = None


class SupportedModelsResponse(AppBaseModel):
    anomaly_detection: List[str]
    failure_prediction: List[str]
    forecasting: List[str]
    ensembles: Optional[List[Dict[str, Any]]] = None


class ErrorResponse(AppBaseModel):
    success: bool = False
    error: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
