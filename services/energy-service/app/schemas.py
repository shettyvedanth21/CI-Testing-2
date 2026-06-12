from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class LiveUpdateRequest(BaseModel):
    telemetry: dict[str, Any]
    dynamic_fields: Optional[dict[str, Any]] = None
    normalized_fields: Optional[dict[str, Any]] = None
    tenant_id: Optional[str] = Field(default=None)


class LiveUpdateBatchItem(BaseModel):
    telemetry: dict[str, Any]
    dynamic_fields: Optional[dict[str, Any]] = None
    normalized_fields: Optional[dict[str, Any]] = None


class LiveUpdateBatchRequest(BaseModel):
    tenant_id: Optional[str] = Field(default=None)
    updates: list[LiveUpdateBatchItem] = Field(default_factory=list, min_length=1)


class DeviceLifecycleRequest(BaseModel):
    status: str = Field(..., pattern="^(running|stopped|restarted)$")
    at: Optional[datetime] = None


class DeviceRangeResponse(BaseModel):
    device_id: str
    start_date: str
    end_date: str
    totals: dict[str, float]
    days: list[dict[str, Any]]
    version: int
    freshness_ts: str


class MonthlyCalendarRequest(BaseModel):
    year: int
    month: int


class DeviceRangeQuery(BaseModel):
    start_date: date
    end_date: date
