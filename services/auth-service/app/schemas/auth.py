from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=100)

    @field_validator("slug")
    @classmethod
    def slug_must_be_url_safe(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", v):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens, "
                "start and end with alphanumeric, min 2 chars"
            )
        return v


class CreatePlantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    location: str | None = Field(default=None, max_length=500)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)


class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    role: Literal["org_admin", "plant_manager", "operator", "viewer"]
    tenant_id: str
    plant_ids: list[str] = Field(default=[])
    password: str | None = Field(default=None, min_length=8, description="Minimum 8 characters")


class UpdateUserRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: Literal["org_admin", "plant_manager", "operator", "viewer"] | None = None
    is_active: bool | None = None
    plant_ids: list[str] | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SuperAdminSummaryResponse(BaseModel):
    total_organisations: int
    total_active_devices: int


class FeatureEntitlementsResponse(BaseModel):
    premium_feature_grants: list[str]
    role_feature_matrix: dict[str, list[str]]
    baseline_features_by_role: dict[str, list[str]]
    effective_features_by_role: dict[str, list[str]]
    available_features: list[str]
    entitlements_version: int


class UpdateEntitlementsRequest(BaseModel):
    premium_feature_grants: list[str] | None = None
    role_feature_matrix: dict[str, list[str]] | None = None


class PlantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    location: str | None
    timezone: str
    is_active: bool
    created_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None
    role: str
    tenant_id: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    lifecycle_state: Literal["invited", "invite_expired", "active", "deactivated"] | None = None
    invite_status: Literal["pending", "expired", "none"] | None = None
    pending_invite_expires_at: datetime | None = None
    can_resend_invite: bool | None = None
    can_reactivate: bool | None = None
    can_deactivate: bool | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user: UserResponse
    tenant: TenantResponse | None
    plant_ids: list[str]
    entitlements: FeatureEntitlementsResponse | None = None


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=32)
    password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=8)


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=32)
    password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=8)


class ActionTokenStatusResponse(BaseModel):
    status: Literal["valid", "expired", "used", "invalid"]
    action_type: Literal["invite_set_password", "password_reset"] | None = None
    email: str | None = None
    full_name: str | None = None


class GenericMessageResponse(BaseModel):
    message: str
