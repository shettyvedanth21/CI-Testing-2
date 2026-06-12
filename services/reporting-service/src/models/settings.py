from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


TENANT_ID_LENGTH = 10


class TariffConfig(Base):
    __tablename__ = "tariff_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tariff_config_tenant_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(TENANT_ID_LENGTH), nullable=False)
    rate = Column(Numeric(10, 4), nullable=False)
    currency = Column(String(10), nullable=False, default="INR")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(100), nullable=True)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_type", "value", name="uq_notification_channels_tenant_type_value"),
        Index("ix_notification_channels_tenant_channel_active", "tenant_id", "channel_type", "is_active"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(TENANT_ID_LENGTH), nullable=False)
    channel_type = Column(String(20), nullable=False, index=True)
    value = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
