from datetime import datetime

from sqlalchemy import Column, BigInteger, String, Float, DateTime, Boolean, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase

from src.models.tenant_tariffs import Base


class TenantEmissionFactor(Base):
    __tablename__ = "tenant_emission_factors"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(24), nullable=False)
    country = Column(String(8), nullable=False, default="IN")
    region = Column(String(64), nullable=False, default="all_india_grid")
    method = Column(String(32), nullable=False, default="location_based")
    factor_value = Column(Float, nullable=False)
    factor_unit = Column(String(32), nullable=False, default="kg_co2_per_kwh")
    source_name = Column(String(255), nullable=False)
    source_version = Column(String(64), nullable=True)
    factor_year = Column(String(32), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "country", "region", "method", "is_active",
            name="uq_tenant_emission_factor_active",
        ),
        Index("ix_tenant_emission_factors_tenant_id", "tenant_id"),
    )
