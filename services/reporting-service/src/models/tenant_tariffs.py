from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


TENANT_ID_LENGTH = 10


class TenantTariff(Base):
    __tablename__ = "tenant_tariffs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(TENANT_ID_LENGTH), unique=True, nullable=False)
    energy_rate_per_kwh = Column(Float, nullable=False)
    demand_charge_per_kw = Column(Float, default=0.0)
    reactive_penalty_rate = Column(Float, default=0.0)
    fixed_monthly_charge = Column(Float, default=0.0)
    power_factor_threshold = Column(Float, default=0.90)
    currency = Column(String(10), default="INR", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TenantTariffVersion(Base):
    __tablename__ = "tenant_tariff_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(TENANT_ID_LENGTH), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    effective_start_at = Column(DateTime, nullable=False)
    effective_end_at = Column(DateTime, nullable=True)
    energy_rate_per_kwh = Column(Float, nullable=False)
    demand_charge_per_kw = Column(Float, default=0.0, nullable=False)
    reactive_penalty_rate = Column(Float, default=0.0, nullable=False)
    fixed_monthly_charge = Column(Float, default=0.0, nullable=False)
    power_factor_threshold = Column(Float, default=0.90, nullable=False)
    currency = Column(String(10), default="INR", nullable=False)
    change_reason = Column(String(255), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    superseded_by_version_id = Column(Integer, nullable=True)
