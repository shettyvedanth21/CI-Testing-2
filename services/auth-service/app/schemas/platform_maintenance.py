from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.auth import PlatformMaintenanceSeverity, PlatformMaintenanceStatus


UTC = timezone.utc


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dedupe_tenant_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


class PlatformMaintenanceWriteBase(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    severity: PlatformMaintenanceSeverity
    message: str = Field(min_length=1)
    starts_at: datetime
    estimated_duration_minutes: int = Field(ge=1, le=43200)
    status: PlatformMaintenanceStatus = PlatformMaintenanceStatus.DRAFT
    broadcast_all_tenants: bool = False
    target_tenant_ids: list[str] = Field(default_factory=list)

    @field_validator("target_tenant_ids")
    @classmethod
    def normalize_target_tenant_ids(cls, value: list[str]) -> list[str]:
        return _dedupe_tenant_ids(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_target_scope(self) -> PlatformMaintenanceWriteBase:
        if self.broadcast_all_tenants and self.target_tenant_ids:
            raise ValueError("target_tenant_ids must be empty when broadcast_all_tenants is true")
        if not self.broadcast_all_tenants and not self.target_tenant_ids:
            raise ValueError("target_tenant_ids must contain at least one tenant when broadcast_all_tenants is false")
        starts_at = _as_utc_datetime(self.starts_at)
        ends_at = starts_at + timedelta(minutes=self.estimated_duration_minutes)
        now = datetime.now(UTC)
        if self.status in {PlatformMaintenanceStatus.SCHEDULED, PlatformMaintenanceStatus.ACTIVE} and ends_at <= now:
            raise ValueError("Scheduled or active notices must have a maintenance window that has not already ended")
        if self.status == PlatformMaintenanceStatus.ACTIVE and starts_at > now:
            raise ValueError("Active notices must start at or before the current time")
        return self


class CreatePlatformMaintenanceAnnouncementRequest(PlatformMaintenanceWriteBase):
    pass


class UpdatePlatformMaintenanceAnnouncementRequest(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    severity: PlatformMaintenanceSeverity | None = None
    message: str | None = Field(default=None, min_length=1)
    starts_at: datetime | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=1, le=43200)
    status: PlatformMaintenanceStatus | None = None
    broadcast_all_tenants: bool | None = None
    target_tenant_ids: list[str] | None = None

    @field_validator("target_tenant_ids")
    @classmethod
    def normalize_target_tenant_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _dedupe_tenant_ids(value)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("message cannot be blank")
        return normalized


class PlatformMaintenanceAnnouncementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    severity: PlatformMaintenanceSeverity
    message: str
    starts_at: datetime
    estimated_duration_minutes: int
    ends_at: datetime
    status: PlatformMaintenanceStatus
    effective_status: PlatformMaintenanceStatus
    broadcast_all_tenants: bool
    target_tenant_ids: list[str]
    created_by: str
    updated_by: str | None
    created_at: datetime
    updated_at: datetime


class CurrentPlatformMaintenanceResponse(BaseModel):
    tenant_id: str
    announcements: list[PlatformMaintenanceAnnouncementResponse]
