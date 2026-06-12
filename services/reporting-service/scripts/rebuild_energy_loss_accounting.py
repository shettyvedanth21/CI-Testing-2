from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import BigInteger, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func


def _resolve_runtime_paths(script_path: Path | None = None) -> tuple[Path, Path]:
    resolved = (script_path or Path(__file__)).resolve()
    reporting_root = resolved.parents[1]

    for candidate in (reporting_root, *reporting_root.parents):
        if (candidate / "services" / "shared" / "energy_accounting.py").exists():
            return candidate, reporting_root

    if reporting_root.name == "reporting-service" and reporting_root.parent.name == "services":
        return reporting_root.parent.parent, reporting_root
    if resolved.parent.name == "scripts":
        return reporting_root, reporting_root

    return reporting_root, reporting_root


PROJECT_ROOT, REPORTING_ROOT = _resolve_runtime_paths()

for path in (REPORTING_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.config import settings as reporting_settings  # type: ignore  # noqa: E402
from src.services.influx_reader import influx_reader  # type: ignore  # noqa: E402
from services.shared.tenant_context import build_internal_headers  # type: ignore  # noqa: E402
try:  # pragma: no cover - exercised in container image
    from shared.energy_accounting import aggregate_window  # type: ignore  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - repo runtime path
    from services.shared.energy_accounting import aggregate_window  # type: ignore  # noqa: E402


class Base(DeclarativeBase):
    pass


class EnergyDeviceDay(Base):
    __tablename__ = "energy_device_day"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", "day", name="uq_energy_device_day"),
        Index("ix_energy_device_day_day", "day"),
        Index("ix_energy_device_day_tenant_day", "tenant_id", "day"),
        Index("ix_energy_device_day_version", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyDeviceMonth(Base):
    __tablename__ = "energy_device_month"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", "month", name="uq_energy_device_month"),
        Index("ix_energy_device_month_month", "month"),
        Index("ix_energy_device_month_tenant_month", "tenant_id", "month"),
        Index("ix_energy_device_month_version", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyFleetDay(Base):
    __tablename__ = "energy_fleet_day"
    __table_args__ = (
        Index("ix_energy_fleet_day_day", "day"),
    )

    tenant_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EnergyFleetMonth(Base):
    __tablename__ = "energy_fleet_month"
    __table_args__ = (
        Index("ix_energy_fleet_month_month", "month"),
    )

    tenant_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idle_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    offhours_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    overconsumption_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loss_cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


TELEMETRY_FIELDS = [
    "energy_kwh",
    "power",
    "power_w",
    "active_power",
    "current",
    "voltage",
    "power_factor",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild canonical energy loss buckets for a date range.")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--tenant-id", default=None, help="Optional tenant scope")
    parser.add_argument("--device-id", action="append", dest="device_ids", default=[], help="Optional device filter")
    return parser.parse_args()


def _service_headers(service_name: str, tenant_id: str | None = None) -> dict[str, str]:
    return build_internal_headers(service_name, tenant_id)


async def _list_devices(client: httpx.AsyncClient, tenant_id: str | None) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{reporting_settings.DEVICE_SERVICE_URL}/api/v1/devices",
        headers=_service_headers("energy-loss-rebuild", tenant_id),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"device list fetch failed: {resp.status_code}")
    payload = resp.json()
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    return [row for row in rows if isinstance(row, dict) and row.get("device_id")]


async def _device_meta(client: httpx.AsyncClient, device_id: str, tenant_id: str | None) -> dict[str, Any]:
    headers = _service_headers("energy-loss-rebuild", tenant_id)
    idle_resp, waste_resp, shift_resp = await asyncio.gather(
        client.get(f"{reporting_settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/idle-config", headers=headers),
        client.get(f"{reporting_settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/waste-config", headers=headers),
        client.get(f"{reporting_settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/shifts", headers=headers),
    )

    idle_payload = idle_resp.json() if idle_resp.status_code == 200 else {}
    waste_payload = waste_resp.json() if waste_resp.status_code == 200 else {}
    shift_payload = shift_resp.json() if shift_resp.status_code == 200 else {}

    idle_data = idle_payload.get("data", idle_payload) if isinstance(idle_payload, dict) else {}
    waste_data = waste_payload.get("data", waste_payload) if isinstance(waste_payload, dict) else {}
    shift_data = shift_payload.get("data", shift_payload) if isinstance(shift_payload, dict) else shift_payload

    idle_threshold = None
    over_threshold = None
    full_load_current_a = None
    idle_threshold_pct_of_fla = None
    if isinstance(idle_data, dict):
        idle_threshold = idle_data.get("derived_idle_threshold_a", idle_data.get("idle_current_threshold"))
        full_load_current_a = idle_data.get("full_load_current_a")
        idle_threshold_pct_of_fla = idle_data.get("idle_threshold_pct_of_fla")
    if isinstance(waste_data, dict):
        over_threshold = waste_data.get(
            "derived_overconsumption_threshold_a",
            waste_data.get("overconsumption_current_threshold_a"),
        )
        full_load_current_a = waste_data.get("full_load_current_a", full_load_current_a)
        idle_threshold_pct_of_fla = waste_data.get("idle_threshold_pct_of_fla", idle_threshold_pct_of_fla)

    return {
        "full_load_current_a": full_load_current_a,
        "idle_threshold_pct_of_fla": idle_threshold_pct_of_fla,
        "idle_threshold": idle_threshold,
        "over_threshold": over_threshold,
        "shifts": shift_data if isinstance(shift_data, list) else [],
    }


async def _query_rebuild_rows(device_id: str, start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    previous_window = getattr(reporting_settings, "INFLUX_AGGREGATION_WINDOW", "5m")
    try:
        reporting_settings.INFLUX_AGGREGATION_WINDOW = "1m"
        return await influx_reader.query_telemetry(
            device_id=device_id,
            start_dt=start_dt,
            end_dt=end_dt,
            fields=TELEMETRY_FIELDS,
        )
    finally:
        reporting_settings.INFLUX_AGGREGATION_WINDOW = previous_window


async def _recompute_device_day(
    session,
    device_id: str,
    day: date,
    tenant_id: str,
    meta: dict[str, Any],
    platform_tz: ZoneInfo,
) -> None:
    start_dt = datetime.combine(day, time.min)
    end_dt = datetime.combine(day, time.max)
    rows = await _query_rebuild_rows(device_id, start_dt, end_dt)
    accounting = aggregate_window(
        rows,
        platform_tz=platform_tz,
        shifts=meta.get("shifts") or [],
        idle_threshold=meta.get("idle_threshold"),
        over_threshold=meta.get("over_threshold"),
    )

    stmt = select(EnergyDeviceDay).where(
        EnergyDeviceDay.tenant_id == tenant_id,
        EnergyDeviceDay.device_id == device_id,
        EnergyDeviceDay.day == day,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = EnergyDeviceDay(tenant_id=tenant_id, device_id=device_id, day=day)
        session.add(row)

    row.energy_kwh = round(accounting.total.energy_kwh, 6)
    row.idle_kwh = round(accounting.total.idle_kwh, 6)
    row.offhours_kwh = round(accounting.total.offhours_kwh, 6)
    row.overconsumption_kwh = round(accounting.total.overconsumption_kwh, 6)
    row.loss_kwh = round(accounting.total.total_loss_kwh, 6)
    row.version = int(row.version or 0) + 1


async def _rebuild_device_month(session, tenant_id: str, device_id: str, month_bucket: date) -> None:
    month_end = (month_bucket.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (
        await session.execute(
            select(EnergyDeviceDay).where(
                EnergyDeviceDay.tenant_id == tenant_id,
                EnergyDeviceDay.device_id == device_id,
                EnergyDeviceDay.day >= month_bucket,
                EnergyDeviceDay.day < month_end,
            )
        )
    ).scalars().all()

    month = (
        await session.execute(
            select(EnergyDeviceMonth).where(
                EnergyDeviceMonth.tenant_id == tenant_id,
                EnergyDeviceMonth.device_id == device_id,
                EnergyDeviceMonth.month == month_bucket,
            )
        )
    ).scalar_one_or_none()
    if month is None:
        month = EnergyDeviceMonth(tenant_id=tenant_id, device_id=device_id, month=month_bucket)
        session.add(month)

    month.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
    month.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
    month.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
    month.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
    month.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
    month.version = int(month.version or 0) + 1


async def _rebuild_fleet_day(session, tenant_id: str, day: date) -> None:
    rows = (
        await session.execute(
            select(EnergyDeviceDay).where(
                EnergyDeviceDay.tenant_id == tenant_id,
                EnergyDeviceDay.day == day,
            )
        )
    ).scalars().all()
    fleet = (
        await session.execute(
            select(EnergyFleetDay).where(
                EnergyFleetDay.tenant_id == tenant_id,
                EnergyFleetDay.day == day,
            )
        )
    ).scalar_one_or_none()
    if fleet is None:
        fleet = EnergyFleetDay(tenant_id=tenant_id, day=day)
        session.add(fleet)

    fleet.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
    fleet.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
    fleet.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
    fleet.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
    fleet.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
    fleet.version = int(fleet.version or 0) + 1


async def _rebuild_fleet_month(session, tenant_id: str, month_bucket: date) -> None:
    month_end = (month_bucket.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (
        await session.execute(
            select(EnergyFleetDay).where(
                EnergyFleetDay.tenant_id == tenant_id,
                EnergyFleetDay.day >= month_bucket,
                EnergyFleetDay.day < month_end,
            )
        )
    ).scalars().all()
    fleet = (
        await session.execute(
            select(EnergyFleetMonth).where(
                EnergyFleetMonth.tenant_id == tenant_id,
                EnergyFleetMonth.month == month_bucket,
            )
        )
    ).scalar_one_or_none()
    if fleet is None:
        fleet = EnergyFleetMonth(tenant_id=tenant_id, month=month_bucket)
        session.add(fleet)

    fleet.energy_kwh = round(sum(float(r.energy_kwh or 0.0) for r in rows), 6)
    fleet.idle_kwh = round(sum(float(r.idle_kwh or 0.0) for r in rows), 6)
    fleet.offhours_kwh = round(sum(float(r.offhours_kwh or 0.0) for r in rows), 6)
    fleet.overconsumption_kwh = round(sum(float(r.overconsumption_kwh or 0.0) for r in rows), 6)
    fleet.loss_kwh = round(sum(float(r.loss_kwh or 0.0) for r in rows), 6)
    fleet.version = int(fleet.version or 0) + 1


async def main() -> None:
    args = _parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise SystemExit("end-date must be on or after start-date")

    engine = create_async_engine(
        reporting_settings.DATABASE_URL,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    platform_tz = ZoneInfo(reporting_settings.PLATFORM_TIMEZONE)

    async with httpx.AsyncClient(timeout=20.0) as client:
        devices = await _list_devices(client, args.tenant_id)
        if args.device_ids:
            selected = set(args.device_ids)
            devices = [d for d in devices if str(d.get("device_id")) in selected]
        meta_by_device = {
            str(device["device_id"]): await _device_meta(client, str(device["device_id"]), args.tenant_id)
            for device in devices
        }

    touched_days: set[tuple[str, date]] = set()
    touched_months: set[tuple[str, date]] = set()
    device_months: dict[str, set[date]] = defaultdict(set)

    async with session_factory() as session:
        cur = start
        while cur <= end:
            for device in devices:
                device_id = str(device["device_id"])
                tenant_id = str(device.get("tenant_id") or args.tenant_id or "").strip()
                if not tenant_id:
                    raise RuntimeError(f"device {device_id} is missing tenant_id; rebuild cannot safely continue")
                await _recompute_device_day(session, device_id, cur, tenant_id, meta_by_device[device_id], platform_tz)
                month_bucket = cur.replace(day=1)
                device_months[f"{tenant_id}:{device_id}"].add(month_bucket)
                touched_days.add((tenant_id, cur))
                touched_months.add((tenant_id, month_bucket))
            cur += timedelta(days=1)

        for scoped_device_id, months in device_months.items():
            tenant_id, device_id = scoped_device_id.split(":", 1)
            for month_bucket in months:
                await _rebuild_device_month(session, tenant_id, device_id, month_bucket)

        for tenant_id, day in touched_days:
            await _rebuild_fleet_day(session, tenant_id, day)
        for tenant_id, month_bucket in touched_months:
            await _rebuild_fleet_month(session, tenant_id, month_bucket)

        await session.commit()

    await engine.dispose()
    influx_reader.close()


if __name__ == "__main__":
    asyncio.run(main())
