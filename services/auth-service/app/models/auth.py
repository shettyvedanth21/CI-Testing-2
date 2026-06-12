from __future__ import annotations

import enum
from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

TENANT_ID_PREFIX = "SH"
TENANT_ID_LENGTH = 10
TENANT_ID_SEQUENCE_WIDTH = 8


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    PLANT_MANAGER = "plant_manager"
    OPERATOR = "operator"
    VIEWER = "viewer"


class AuthActionType(str, enum.Enum):
    INVITE_SET_PASSWORD = "invite_set_password"
    PASSWORD_RESET = "password_reset"


class PlatformMaintenanceSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PlatformMaintenanceStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlatformMaintenanceEmailDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (Index("ix_organizations_slug", "slug"),)

    id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    entitlements_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    premium_feature_grants_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    role_feature_matrix_json: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    plants: Mapped[list[Plant]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    users: Mapped[list[User]] = relationship(back_populates="organization")

    def __repr__(self) -> str:
        return f"Organization(id={self.id!r}, slug={self.slug!r}, name={self.name!r})"


class Plant(Base):
    __tablename__ = "plants"
    __table_args__ = (Index("ix_plants_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(
        String(TENANT_ID_LENGTH),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    organization: Mapped[Organization] = relationship(back_populates="plants")
    user_access: Mapped[list[UserPlantAccess]] = relationship(back_populates="plant", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Plant(id={self.id!r}, tenant_id={self.tenant_id!r}, name={self.name!r})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_tenant_id", "tenant_id"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str | None] = mapped_column(
        String(TENANT_ID_LENGTH),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )
    permissions_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization | None] = relationship(back_populates="users")
    plant_access: Mapped[list[UserPlantAccess]] = relationship(back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!s})"


class UserPlantAccess(Base):
    __tablename__ = "user_plant_access"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("plants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="plant_access")
    plant: Mapped[Plant] = relationship(back_populates="user_access")

    def __repr__(self) -> str:
        return f"UserPlantAccess(user_id={self.user_id!r}, plant_id={self.plant_id!r})"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_token_hash", "token_hash"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
        Index("ix_refresh_tokens_revoked_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"RefreshToken(id={self.id!r}, user_id={self.user_id!r}, revoked_at={self.revoked_at!r})"


class TenantIdSequence(Base):
    __tablename__ = "tenant_id_sequences"

    prefix: Mapped[str] = mapped_column(String(2), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuthActionToken(Base):
    __tablename__ = "auth_action_tokens"
    __table_args__ = (
        Index("ix_auth_action_tokens_user_id", "user_id"),
        Index("ix_auth_action_tokens_token_hash", "token_hash"),
        Index("ix_auth_action_tokens_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[AuthActionType] = mapped_column(
        Enum(AuthActionType, name="authactiontype", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(TENANT_ID_LENGTH), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:
        return (
            f"AuthActionToken(id={self.id!r}, user_id={self.user_id!r}, "
            f"action_type={self.action_type.value!r}, used_at={self.used_at!r})"
        )


class PlatformMaintenanceAnnouncement(Base):
    __tablename__ = "platform_maintenance_announcements"
    __table_args__ = (
        Index("ix_platform_maintenance_announcements_status", "status"),
        Index("ix_platform_maintenance_announcements_starts_at", "starts_at"),
        Index("ix_platform_maintenance_announcements_ends_at", "ends_at"),
        Index("ix_platform_maintenance_announcements_created_by_user_id", "created_by_user_id"),
        Index("ix_platform_maintenance_announcements_updated_by_user_id", "updated_by_user_id"),
        Index("ix_platform_maintenance_announcements_delivery_status", "status", "broadcast_all_tenants"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[PlatformMaintenanceSeverity] = mapped_column(
        Enum(
            PlatformMaintenanceSeverity,
            name="platformmaintenanceseverity",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text(), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PlatformMaintenanceStatus] = mapped_column(
        Enum(
            PlatformMaintenanceStatus,
            name="platformmaintenancestatus",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    broadcast_all_tenants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=False,
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_user_id])
    targets: Mapped[list[PlatformMaintenanceAnnouncementTarget]] = relationship(
        back_populates="announcement",
        cascade="all, delete-orphan",
    )
    email_deliveries: Mapped[list[PlatformMaintenanceEmailDelivery]] = relationship(
        back_populates="announcement",
        cascade="all, delete-orphan",
    )


class PlatformMaintenanceAnnouncementTarget(Base):
    __tablename__ = "platform_maintenance_announcement_targets"
    __table_args__ = (
        Index("ix_platform_maintenance_targets_announcement_id", "announcement_id"),
        Index("ix_platform_maintenance_targets_tenant_id", "tenant_id"),
        UniqueConstraint("announcement_id", "tenant_id", name="uq_platform_maintenance_target_announcement_tenant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    announcement_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("platform_maintenance_announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(TENANT_ID_LENGTH),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    announcement: Mapped[PlatformMaintenanceAnnouncement] = relationship(back_populates="targets")
    organization: Mapped[Organization] = relationship()


class PlatformMaintenanceEmailDelivery(Base):
    __tablename__ = "platform_maintenance_email_deliveries"
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_platform_maintenance_email_delivery_announcement_user"),
        Index("ix_platform_maintenance_email_deliveries_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_platform_maintenance_email_deliveries_tenant_status", "tenant_id", "status"),
        Index("ix_platform_maintenance_email_deliveries_announcement_status", "announcement_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    announcement_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("platform_maintenance_announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_role: Mapped[str] = mapped_column(String(50), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[PlatformMaintenanceEmailDeliveryStatus] = mapped_column(
        Enum(
            PlatformMaintenanceEmailDeliveryStatus,
            name="platformmaintenanceemaildeliverystatus",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=PlatformMaintenanceEmailDeliveryStatus.PENDING,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    announcement: Mapped[PlatformMaintenanceAnnouncement] = relationship(back_populates="email_deliveries")
