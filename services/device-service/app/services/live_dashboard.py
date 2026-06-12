"""Low-latency dashboard read service backed by live projection rows."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device import (
    Device,
    DeviceLatestTelemetrySnapshot,
    DeviceLiveState,
    DeviceRecentTelemetrySample,
    DeviceShift,
    ParameterHealthConfig,
    RuntimeStatus,
    TELEMETRY_TIMEOUT_SECONDS,
)
from app.services.idle_running import TariffCache
from app.services.emission_factor_cache import EmissionFactorCache, build_co2_overview
from app.services.shared_http import get_client, request_with_retries
from app.services.load_thresholds import classify_current_band, resolve_device_thresholds
from app.services.live_projection import LiveProjectionService, _health_machine_state_from_live_state
from app.services.health_config import HealthConfigService
from app.services.device_property import DevicePropertyService
from app.services.runtime_state import load_state_sql, resolve_load_state, resolve_runtime_status, runtime_status_sql
from app.services.status_model import OPERATIONAL_STATUS_VALUES, operational_status_sql, resolve_operational_status
from services.shared.tenant_context import TenantContext, build_internal_headers


def _get_platform_tz() -> ZoneInfo:
    return ZoneInfo(settings.PLATFORM_TIMEZONE)


class LiveDashboardService:
    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        self._projection = LiveProjectionService(session)
        self._ctx = ctx

    @staticmethod
    def _normalize_tariff(tariff: Optional[dict]) -> dict:
        if not isinstance(tariff, dict):
            return {"configured": False, "rate": 0.0, "currency": "INR", "cache": "empty"}
        normalized = dict(tariff)
        normalized.setdefault("configured", False)
        normalized.setdefault("rate", 0.0)
        normalized.setdefault("currency", "INR")
        return normalized

    @staticmethod
    def _tenant_context(tenant_id: Optional[str]) -> Optional[TenantContext]:
        if tenant_id is None:
            return None
        return TenantContext(
            tenant_id=tenant_id,
            user_id="system",
            role="system",
            plant_ids=[],
            is_super_admin=False,
        )

    def _resolve_plant_scope(
        self,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> Optional[list[str]]:
        if accessible_plant_ids is not None:
            if plant_id:
                return [plant_id] if plant_id in accessible_plant_ids else []
            return list(accessible_plant_ids)
        if plant_id:
            return [plant_id]
        if self._ctx is None:
            return None
        if self._ctx.role in {"plant_manager", "operator", "viewer"}:
            return list(self._ctx.plant_ids)
        return None

    @staticmethod
    def normalize_device_name_search(search: Optional[str]) -> Optional[str]:
        if search is None:
            return None
        normalized = " ".join(str(search).split()).strip()
        return normalized or None

    @classmethod
    def device_name_matches_search(cls, device_name: Optional[str], search: Optional[str]) -> bool:
        normalized_search = cls.normalize_device_name_search(search)
        if normalized_search is None:
            return True
        return normalized_search.casefold() in str(device_name or "").casefold()

    @classmethod
    def _device_name_search_pattern(cls, search: Optional[str]) -> Optional[str]:
        normalized_search = cls.normalize_device_name_search(search)
        if normalized_search is None:
            return None
        escaped = (
            normalized_search.casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"%{escaped}%"

    @staticmethod
    def _apply_plant_scope(query, plant_ids: Optional[list[str]]):
        if plant_ids is None:
            return query
        if not plant_ids:
            return query.where(False)
        return query.where(Device.plant_id.in_(plant_ids))

    @staticmethod
    def _iso_utc(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _round_float(value: object, digits: int = 4) -> float:
        if value is None:
            return 0.0
        return round(float(value), digits)

    async def _build_loss_overview(
        self,
        *,
        state: Optional[DeviceLiveState],
        authoritative_last_seen: Optional[datetime],
        now_utc: datetime,
        tenant_id: Optional[str] = None,
        canonical_today_energy_kwh: Optional[float] = None,
        canonical_today_loss_kwh: Optional[float] = None,
        canonical_today_loss_cost_inr: Optional[float] = None,
        canonical_idle_kwh: Optional[float] = None,
        canonical_offhours_kwh: Optional[float] = None,
        canonical_overconsumption_kwh: Optional[float] = None,
    ) -> dict:
        local_today = now_utc.astimezone(_get_platform_tz()).date()
        day_bucket = state.day_bucket if state is not None else None
        is_current_day = bool(day_bucket == local_today)

        live_idle_kwh = self._round_float(state.today_idle_kwh if is_current_day and state is not None else 0.0)
        idle_kwh = canonical_idle_kwh if canonical_idle_kwh is not None else live_idle_kwh
        live_off_hours_kwh = self._round_float(state.today_offhours_kwh if is_current_day and state is not None else 0.0)
        off_hours_kwh = canonical_offhours_kwh if canonical_offhours_kwh is not None else live_off_hours_kwh
        live_overconsumption_kwh = self._round_float(
            state.today_overconsumption_kwh if is_current_day and state is not None else 0.0,
        )
        overconsumption_kwh = canonical_overconsumption_kwh if canonical_overconsumption_kwh is not None else live_overconsumption_kwh
        live_total_loss_kwh = self._round_float(state.today_loss_kwh if is_current_day and state is not None else 0.0)
        total_loss_kwh = canonical_today_loss_kwh if canonical_today_loss_kwh is not None else live_total_loss_kwh
        live_energy_kwh = self._round_float(state.today_energy_kwh if is_current_day and state is not None else 0.0)
        today_energy_kwh = canonical_today_energy_kwh if canonical_today_energy_kwh is not None else live_energy_kwh
        live_loss_cost_inr = (
            round(float(state.today_loss_cost_inr), 4)
            if is_current_day and state is not None and state.today_loss_cost_inr is not None
            else None
        )
        total_loss_cost_inr = canonical_today_loss_cost_inr if canonical_today_loss_cost_inr is not None else live_loss_cost_inr

        if total_loss_cost_inr is None:
            costs_available = False
        elif total_loss_kwh == 0:
            costs_available = True
        else:
            costs_available = total_loss_cost_inr > 0

        def allocate_cost(bucket_kwh: float) -> Optional[float]:
            if not costs_available or total_loss_cost_inr is None:
                return None
            if total_loss_kwh <= 0:
                return 0.0
            return round(total_loss_cost_inr * (bucket_kwh / total_loss_kwh), 4)

        factor_payload = await EmissionFactorCache.get(tenant_id)
        month_energy_kwh = self._round_float(state.month_energy_kwh if state is not None else 0.0)
        co2_overview = build_co2_overview(
            tenant_id=tenant_id,
            today_energy_kwh=today_energy_kwh,
            today_loss_kwh=total_loss_kwh,
            today_loss_available=is_current_day and state is not None,
            month_energy_kwh=month_energy_kwh,
            factor_payload=factor_payload,
        )

        return {
            "day_bucket": day_bucket.isoformat() if isinstance(day_bucket, date) else None,
            "updated_at": self._iso_utc(state.updated_at) if state is not None else None,
            "last_telemetry_ts": self._iso_utc(authoritative_last_seen),
            "currency": "INR",
            "costs_available": costs_available,
            "idle_kwh": idle_kwh,
            "idle_cost_inr": allocate_cost(idle_kwh),
            "off_hours_kwh": off_hours_kwh,
            "off_hours_cost_inr": allocate_cost(off_hours_kwh),
            "overconsumption_kwh": overconsumption_kwh,
            "overconsumption_cost_inr": allocate_cost(overconsumption_kwh),
            "total_loss_kwh": total_loss_kwh,
            "total_loss_cost_inr": total_loss_cost_inr if costs_available else None,
            "today_energy_kwh": today_energy_kwh,
            "co2_overview": co2_overview,
        }

    async def _fetch_energy_json(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        base = (settings.ENERGY_SERVICE_BASE_URL or "").rstrip("/")
        if not base:
            return None
        try:
            client = await get_client(base)
            resp = await request_with_retries(
                client,
                "GET",
                path,
                operation="live_dashboard_fetch_energy_json",
                params=params,
                headers=build_internal_headers(
                    "device-service",
                    params.get("tenant_id") if isinstance(params, dict) else None,
                ),
                timeout=max(0.5, settings.ENERGY_SERVICE_TIMEOUT_SECONDS),
            )
            if resp.status_code != 200:
                return None
            payload = resp.json()
            if not isinstance(payload, dict) or not payload.get("success"):
                return None
            return payload
        except Exception:
            return None

    async def _build_current_day_loss_view(
        self,
        tenant_id: Optional[str],
        *,
        tariff_rate: float,
        currency: str,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> dict:
        local_day = datetime.now(timezone.utc).astimezone(_get_platform_tz()).date()
        plant_scope = self._resolve_plant_scope(plant_id, accessible_plant_ids)
        rows_query = (
            select(Device, DeviceLiveState)
            .outerjoin(
                DeviceLiveState,
                (DeviceLiveState.device_id == Device.device_id)
                & (DeviceLiveState.tenant_id == Device.tenant_id),
            )
            .where(Device.deleted_at.is_(None))
        )
        if tenant_id:
            rows_query = rows_query.where(Device.tenant_id == tenant_id)
        rows_query = self._apply_plant_scope(rows_query, plant_scope)
        rows = (await self._session.execute(rows_query)).all()

        table_rows: list[dict] = []
        totals = {
            "idle_kwh": 0.0,
            "off_hours_kwh": 0.0,
            "overconsumption_kwh": 0.0,
            "total_loss_kwh": 0.0,
            "today_energy_kwh": 0.0,
            "idle_cost_inr": 0.0,
            "off_hours_cost_inr": 0.0,
            "overconsumption_cost_inr": 0.0,
            "total_loss_cost_inr": 0.0,
        }

        for device, state in rows:
            day_matches = state is not None and state.day_bucket == local_day
            idle = float(state.today_idle_kwh or 0.0) if day_matches and state else 0.0
            off_hours = float(state.today_offhours_kwh or 0.0) if day_matches and state else 0.0
            over = float(state.today_overconsumption_kwh or 0.0) if day_matches and state else 0.0
            total_loss = float(state.today_loss_kwh or 0.0) if day_matches and state else 0.0
            today_energy = float(state.today_energy_kwh or 0.0) if day_matches and state else 0.0

            row_loss_cost = (
                float(state.today_loss_cost_inr)
                if day_matches and state and state.today_loss_cost_inr is not None and float(state.today_loss_cost_inr) > 0
                else None
            )

            if row_loss_cost is not None and total_loss > 0:
                idle_cost = round(row_loss_cost * (idle / total_loss), 4)
                off_hours_cost = round(row_loss_cost * (off_hours / total_loss), 4) if off_hours > 0 else 0.0
                over_cost = round(row_loss_cost * (over / total_loss), 4) if over > 0 else 0.0
                bucket_sum = idle_cost + off_hours_cost + over_cost
                remainder = round(row_loss_cost - bucket_sum, 4)
                if abs(remainder) >= 0.0001:
                    buckets = [("idle", idle_cost, idle), ("over", over_cost, over), ("off", off_hours_cost, off_hours)]
                    largest = max(buckets, key=lambda b: b[2])
                    if largest[0] == "idle":
                        idle_cost = round(idle_cost + remainder, 4)
                    elif largest[0] == "over":
                        over_cost = round(over_cost + remainder, 4)
                    else:
                        off_hours_cost = round(off_hours_cost + remainder, 4)
            elif row_loss_cost is not None:
                idle_cost = 0.0
                off_hours_cost = 0.0
                over_cost = 0.0
            else:
                idle_cost = round(idle * tariff_rate, 4)
                off_hours_cost = round(off_hours * tariff_rate, 4)
                over_cost = round(over * tariff_rate, 4)
                row_loss_cost = round(total_loss * tariff_rate, 4)

            totals["idle_kwh"] += idle
            totals["off_hours_kwh"] += off_hours
            totals["overconsumption_kwh"] += over
            totals["total_loss_kwh"] += total_loss
            totals["today_energy_kwh"] += today_energy
            totals["idle_cost_inr"] += idle_cost
            totals["off_hours_cost_inr"] += off_hours_cost
            totals["overconsumption_cost_inr"] += over_cost
            totals["total_loss_cost_inr"] += row_loss_cost

            table_rows.append(
                {
                    "device_id": device.device_id,
                    "device_name": device.device_name,
                    "idle_kwh": round(idle, 4),
                    "idle_cost_inr": idle_cost,
                    "off_hours_kwh": round(off_hours, 4),
                    "off_hours_cost_inr": off_hours_cost,
                    "overconsumption_kwh": round(over, 4),
                    "overconsumption_cost_inr": over_cost,
                    "total_loss_kwh": round(total_loss, 4),
                    "total_loss_cost_inr": round(row_loss_cost, 4),
                    "status": "computed",
                    "reason": None,
                }
            )

        table_rows.sort(key=lambda row: row["total_loss_cost_inr"], reverse=True)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "currency": currency,
            "totals": {
                "idle_kwh": round(totals["idle_kwh"], 4),
                "idle_cost_inr": round(totals["idle_cost_inr"], 4),
                "off_hours_kwh": round(totals["off_hours_kwh"], 4),
                "off_hours_cost_inr": round(totals["off_hours_cost_inr"], 4),
                "overconsumption_kwh": round(totals["overconsumption_kwh"], 4),
                "overconsumption_cost_inr": round(totals["overconsumption_cost_inr"], 4),
                "total_loss_kwh": round(totals["total_loss_kwh"], 4),
                "total_loss_cost_inr": round(totals["total_loss_cost_inr"], 4),
                "today_energy_kwh": round(totals["today_energy_kwh"], 4),
                "today_energy_cost_inr": round(totals["today_energy_kwh"] * tariff_rate, 4),
            },
            "rows": table_rows,
        }

    async def get_fleet_snapshot(
        self,
        page: int = 1,
        page_size: int = 50,
        sort: str = "device_name",
        tenant_id: Optional[str] = None,
        runtime_filter: Optional[str] = None,
        operational_status_filter: Optional[str] = None,
        accessible_plant_ids: Optional[list[str]] = None,
        search: Optional[str] = None,
    ) -> dict:
        now_utc = datetime.now(timezone.utc)
        derived_last_seen = func.coalesce(DeviceLiveState.last_telemetry_ts, Device.last_seen_timestamp)
        derived_runtime_status = runtime_status_sql(derived_last_seen, now_utc=now_utc)
        derived_load_state = load_state_sql(DeviceLiveState.load_state, derived_last_seen, now_utc=now_utc)
        derived_operational_status = operational_status_sql(derived_runtime_status, derived_load_state, derived_last_seen)
        search_pattern = self._device_name_search_pattern(search)

        count_query = (
            select(func.count())
            .select_from(Device)
            .outerjoin(
                DeviceLiveState,
                (DeviceLiveState.device_id == Device.device_id)
                & (DeviceLiveState.tenant_id == Device.tenant_id),
            )
            .where(Device.deleted_at.is_(None))
        )
        page_query = (
            select(
                Device,
                DeviceLiveState,
                Device.first_telemetry_timestamp,
                derived_runtime_status.label("resolved_runtime_status"),
                derived_load_state.label("resolved_load_state"),
                derived_operational_status.label("resolved_operational_status"),
                derived_last_seen.label("resolved_last_seen"),
            )
            .outerjoin(
                DeviceLiveState,
                (DeviceLiveState.device_id == Device.device_id)
                & (DeviceLiveState.tenant_id == Device.tenant_id),
            )
            .where(Device.deleted_at.is_(None))
        )

        if tenant_id:
            count_query = count_query.where(Device.tenant_id == tenant_id)
            page_query = page_query.where(Device.tenant_id == tenant_id)

        if accessible_plant_ids is not None:
            if accessible_plant_ids:
                count_query = count_query.where(Device.plant_id.in_(accessible_plant_ids))
                page_query = page_query.where(Device.plant_id.in_(accessible_plant_ids))
            else:
                count_query = count_query.where(False)
                page_query = page_query.where(False)

        if runtime_filter:
            count_query = count_query.where(derived_runtime_status == runtime_filter)
            page_query = page_query.where(derived_runtime_status == runtime_filter)
        if operational_status_filter in OPERATIONAL_STATUS_VALUES:
            count_query = count_query.where(derived_operational_status == operational_status_filter)
            page_query = page_query.where(derived_operational_status == operational_status_filter)
        if search_pattern is not None:
            search_clause = func.lower(Device.device_name).like(search_pattern, escape="\\")
            count_query = count_query.where(search_clause)
            page_query = page_query.where(search_clause)

        last_seen_nulls_last = case((derived_last_seen.is_(None), 1), else_=0)
        if sort == "last_seen":
            page_query = page_query.order_by(last_seen_nulls_last.asc(), derived_last_seen.desc(), func.lower(Device.device_name).asc())
        else:
            page_query = page_query.order_by(func.lower(Device.device_name).asc(), last_seen_nulls_last.asc(), derived_last_seen.desc())

        total = int((await self._session.execute(count_query)).scalar() or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        safe_page = max(1, min(page, total_pages))
        offset = (safe_page - 1) * page_size

        rows = (
            await self._session.execute(
                page_query.offset(offset).limit(page_size)
            )
        ).all()

        page_items: list[dict] = []
        device_ids: list[str] = []
        for device, state, first_telemetry_ts, runtime_status, load_state, operational_status, last_seen_ts in rows:
            device_ids.append(device.device_id)
            daily_uptime = getattr(state, "today_uptime_percentage", None) if state else None
            if daily_uptime is None and state and state.uptime_percentage is not None:
                daily_uptime = float(state.uptime_percentage)
            thresholds = resolve_device_thresholds(device)
            current_band = (
                classify_current_band(
                    float(state.last_current_a) if state and state.last_current_a is not None else None,
                    float(state.last_voltage_v) if state and state.last_voltage_v is not None else None,
                    thresholds,
                )
                if runtime_status == RuntimeStatus.RUNNING.value
                else "unknown"
            )
            resolved_operational_status = resolve_operational_status(
                runtime_status=runtime_status,
                load_state=load_state,
                current_band=current_band,
                has_telemetry=last_seen_ts is not None,
            )
            page_items.append(
                {
                    "device_id": device.device_id,
                    "device_name": device.device_name,
                    "device_type": device.device_type,
                    "plant_id": device.plant_id,
                    "runtime_status": runtime_status,
                    "load_state": load_state or "unknown",
                    "current_band": current_band,
                    "operational_status": resolved_operational_status or operational_status,
                    "location": device.location,
                    "first_telemetry_timestamp": self._iso_utc(first_telemetry_ts),
                    "last_seen_timestamp": last_seen_ts.isoformat() if last_seen_ts is not None else None,
                    "health_score": round(float(state.health_score), 2) if state and state.health_score is not None else None,
                    "uptime_percentage": round(float(state.uptime_percentage), 2) if state and state.uptime_percentage is not None else None,
                    "daily_uptime_percentage": round(float(daily_uptime), 2) if daily_uptime is not None else None,
                    "has_uptime_config": False,
                    "data_freshness_ts": now_utc.isoformat(),
                    "version": int(state.version) if state else 0,
                }
            )

        shift_map: dict[str, int] = {}
        if device_ids:
            active_shift_counts = (
                await self._session.execute(
                    select(DeviceShift.device_id, func.count(DeviceShift.id))
                    .where(DeviceShift.is_active.is_(True), DeviceShift.device_id.in_(device_ids))
                    .group_by(DeviceShift.device_id)
                )
            ).all()
            shift_map = {row[0]: int(row[1]) for row in active_shift_counts}
        for item in page_items:
            item["has_uptime_config"] = shift_map.get(item["device_id"], 0) > 0

        return {
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "warnings": [],
            "degraded_services": [],
            "total": total,
            "page": safe_page,
            "page_size": page_size,
            "total_pages": total_pages,
            "devices": page_items,
        }

    async def get_dashboard_summary(
        self,
        tenant_id: Optional[str] = None,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> dict:
        now_utc = datetime.now(timezone.utc)
        plant_scope = self._resolve_plant_scope(plant_id, accessible_plant_ids)
        authoritative_last_seen = func.coalesce(DeviceLiveState.last_telemetry_ts, Device.last_seen_timestamp)
        derived_runtime_status = runtime_status_sql(authoritative_last_seen, now_utc=now_utc)
        derived_load_state = load_state_sql(DeviceLiveState.load_state, authoritative_last_seen, now_utc=now_utc)
        derived_operational_status = operational_status_sql(derived_runtime_status, derived_load_state, authoritative_last_seen)

        summary_query = (
            select(
                func.count(Device.device_id),
                func.sum(case((derived_runtime_status == RuntimeStatus.RUNNING.value, 1), else_=0)),
                func.sum(case((derived_operational_status == "stopped", 1), else_=0)),
                func.sum(case((derived_operational_status == "idle", 1), else_=0)),
                func.sum(case((derived_operational_status == "running", 1), else_=0)),
                func.sum(case((derived_operational_status == "overconsumption", 1), else_=0)),
                func.sum(case((derived_operational_status == "unknown", 1), else_=0)),
                func.count(DeviceLiveState.health_score),
                func.avg(DeviceLiveState.health_score),
                func.avg(func.coalesce(DeviceLiveState.today_uptime_percentage, DeviceLiveState.uptime_percentage)),
            )
            .select_from(Device)
            .outerjoin(
                DeviceLiveState,
                (DeviceLiveState.device_id == Device.device_id)
                & (DeviceLiveState.tenant_id == Device.tenant_id),
            )
            .where(Device.deleted_at.is_(None))
        )
        if tenant_id:
            summary_query = summary_query.where(Device.tenant_id == tenant_id)
        summary_query = self._apply_plant_scope(summary_query, plant_scope)

        total, running, stopped, idle, in_load, overconsumption, unknown, devices_with_health_data, avg_health, avg_uptime = (
            await self._session.execute(summary_query)
        ).one()

        health_config_query = (
            select(func.count(func.distinct(ParameterHealthConfig.device_id)))
            .select_from(ParameterHealthConfig)
            .join(
                Device,
                (Device.device_id == ParameterHealthConfig.device_id)
                & (Device.tenant_id == ParameterHealthConfig.tenant_id),
            )
            .where(
                Device.deleted_at.is_(None),
                ParameterHealthConfig.is_active.is_(True),
            )
        )
        if tenant_id:
            health_config_query = health_config_query.where(ParameterHealthConfig.tenant_id == tenant_id)
        health_config_query = self._apply_plant_scope(health_config_query, plant_scope)
        health_configured = int((await self._session.execute(health_config_query)).scalar() or 0)

        uptime_query = (
            select(func.count(func.distinct(DeviceShift.device_id)))
            .select_from(DeviceShift)
            .join(
                Device,
                (Device.device_id == DeviceShift.device_id)
                & (Device.tenant_id == DeviceShift.tenant_id),
            )
            .where(
                Device.deleted_at.is_(None),
                DeviceShift.is_active.is_(True),
            )
        )
        if tenant_id:
            uptime_query = uptime_query.where(DeviceShift.tenant_id == tenant_id)
        uptime_query = self._apply_plant_scope(uptime_query, plant_scope)
        uptime_configured = int((await self._session.execute(uptime_query)).scalar() or 0)

        total = int(total or 0)
        running = int(running or 0)
        stopped = int(stopped or 0)
        idle = int(idle or 0)
        in_load = int(in_load or 0)
        overconsumption = int(overconsumption or 0)
        unknown = int(unknown or 0)
        devices_with_health_data = int(devices_with_health_data or 0)

        tariff = self._normalize_tariff(await TariffCache.get(tenant_id))
        rate = float(tariff.get("rate") or 0.0)
        live_loss_view = await self._build_current_day_loss_view(
            tenant_id,
            tariff_rate=rate,
            currency=str(tariff.get("currency") or "INR"),
            plant_id=plant_id,
            accessible_plant_ids=accessible_plant_ids,
        )
        currency = str(tariff.get("currency") or "INR")
        totals_query = (
            select(
                func.sum(DeviceLiveState.month_energy_kwh),
                func.sum(DeviceLiveState.today_energy_kwh),
            )
            .select_from(Device)
            .outerjoin(
                DeviceLiveState,
                (DeviceLiveState.device_id == Device.device_id)
                & (DeviceLiveState.tenant_id == Device.tenant_id),
            )
            .where(Device.deleted_at.is_(None))
        )
        if tenant_id:
            totals_query = totals_query.where(Device.tenant_id == tenant_id)
        totals_query = self._apply_plant_scope(totals_query, plant_scope)
        totals = (await self._session.execute(totals_query)).first()
        month_energy = float(totals[0] or 0.0)
        today_energy = float(totals[1] or 0.0)
        today_cost = today_energy * rate
        month_cost = month_energy * rate

        energy_service_reached = False
        energy_service_attempted = True
        cost_data_reasons: list[str] = []
        now_local = datetime.now(_get_platform_tz())

        device_ids_str: Optional[str] = None
        plant_id_for_calendar: Optional[str] = None
        if plant_scope is not None:
            device_ids_query = select(Device.device_id).where(Device.deleted_at.is_(None))
            if tenant_id:
                device_ids_query = device_ids_query.where(Device.tenant_id == tenant_id)
            device_ids_query = self._apply_plant_scope(device_ids_query, plant_scope)
            device_ids_rows = (await self._session.execute(device_ids_query)).scalars().all()
            device_ids_str = ",".join(device_ids_rows) if device_ids_rows else None
            plant_id_for_calendar = plant_id

        monthly_payload = await self._fetch_energy_json(
            "/api/v1/energy/calendar/monthly",
            params={
                "year": now_local.year,
                "month": now_local.month,
                **({"tenant_id": tenant_id} if tenant_id else {}),
                **({"device_ids": device_ids_str} if device_ids_str else {}),
                **({"plant_id": plant_id_for_calendar} if plant_id_for_calendar else {}),
            },
        )
        if monthly_payload is not None:
            energy_service_reached = True
        else:
            cost_data_reasons.append("energy_service_unavailable")

        canonical_today_loss_kwh: Optional[float] = None
        canonical_today_loss_cost_inr: Optional[float] = None

        summary = monthly_payload.get("summary") if isinstance(monthly_payload, dict) else None
        if isinstance(summary, dict):
            canonical_month_energy = summary.get("total_energy_kwh")
            canonical_month_cost = summary.get("total_energy_cost_inr")
            if canonical_month_energy is not None:
                month_energy = float(canonical_month_energy or 0.0)
            if canonical_month_cost is not None:
                month_cost = float(canonical_month_cost or 0.0)
            else:
                month_cost = month_energy * rate
        for day_entry in (monthly_payload.get("days") or []) if isinstance(monthly_payload, dict) else []:
            if day_entry.get("date") == now_local.date().isoformat():
                canonical_today_cost = day_entry.get("energy_cost_inr")
                canonical_today_energy = day_entry.get("energy_kwh")
                canonical_today_loss = day_entry.get("loss_kwh")
                canonical_today_loss_cost = day_entry.get("loss_cost_inr")
                if isinstance(canonical_today_cost, (int, float)):
                    today_cost = float(canonical_today_cost)
                if isinstance(canonical_today_energy, (int, float)):
                    today_energy = float(canonical_today_energy)
                if isinstance(canonical_today_loss, (int, float)):
                    canonical_today_loss_kwh = float(canonical_today_loss)
                if isinstance(canonical_today_loss_cost, (int, float)):
                    canonical_today_loss_cost_inr = float(canonical_today_loss_cost)
                break

        live_totals = live_loss_view["totals"]
        today_loss = canonical_today_loss_kwh if canonical_today_loss_kwh is not None else float(live_totals["total_loss_kwh"] or 0.0)
        today_loss_cost = canonical_today_loss_cost_inr if canonical_today_loss_cost_inr is not None else float(live_totals["total_loss_cost_inr"] or 0.0)

        using_local_fallback = energy_service_attempted and not energy_service_reached
        return {
            "success": True,
            "generated_at": now_utc.isoformat(),
            "stale": False,
            "warnings": [],
            "degraded_services": [],
            "summary": {
                "total_devices": total,
                "running_devices": running,
                "stopped_devices": stopped,
                "idle_devices": idle,
                "in_load_devices": in_load,
                "overconsumption_devices": overconsumption,
                "unknown_devices": unknown,
                "status_counts": {
                    "unknown": unknown,
                    "stopped": stopped,
                    "idle": idle,
                    "running": in_load,
                    "overconsumption": overconsumption,
                },
                "devices_with_health_data": devices_with_health_data,
                "devices_with_health_configured": health_configured,
                "devices_missing_health_config": max(0, total - health_configured),
                "devices_with_uptime_configured": uptime_configured,
                "devices_missing_uptime_config": max(0, total - uptime_configured),
                "system_health": round(float(avg_health), 2) if avg_health is not None else None,
                "average_efficiency": round(float(avg_uptime), 2) if avg_uptime is not None else None,
            },
            "alerts": {
                "active_alerts": 0,
                "alerts_triggered": 0,
                "alerts_cleared": 0,
                "rules_created": 0,
            },
            "devices": [],
            "energy_widgets": {
                "month_energy_kwh": round(month_energy, 4),
                "month_energy_cost_inr": round(month_cost, 4),
                "today_energy_kwh": round(today_energy, 4),
                "today_energy_cost_inr": round(today_cost, 4),
                "today_loss_kwh": round(today_loss, 4),
                "today_loss_cost_inr": round(today_loss_cost, 4),
                "generated_at": now_utc.isoformat(),
                "currency": currency,
                "data_quality": "stale" if using_local_fallback else "ok",
                "invariant_checks": {},
                "reconciliation_warning": "energy_service_unavailable" if using_local_fallback else None,
                "no_nan_inf": True,
            },
            "cost_data_state": "stale" if using_local_fallback else "fresh",
            "cost_data_reasons": cost_data_reasons,
            "cost_generated_at": now_utc.isoformat(),
        }

    async def get_today_loss_breakdown(
        self,
        tenant_id: Optional[str] = None,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[list[str]] = None,
    ) -> dict:
        tariff = self._normalize_tariff(await TariffCache.get(tenant_id))
        rate = float(tariff.get("rate") or 0.0)
        live_loss_view = await self._build_current_day_loss_view(
            tenant_id,
            tariff_rate=rate,
            currency=str(tariff.get("currency") or "INR"),
            plant_id=plant_id,
            accessible_plant_ids=accessible_plant_ids,
        )

        return {
            "success": True,
            "generated_at": live_loss_view["generated_at"],
            "stale": False,
            "currency": live_loss_view["currency"],
            "totals": live_loss_view["totals"],
            "rows": live_loss_view["rows"],
            "data_quality": "ok",
            "invariant_checks": {},
            "no_nan_inf": True,
            "warnings": [],
            "cost_data_state": "fresh",
            "cost_data_reasons": [],
            "cost_generated_at": live_loss_view["generated_at"],
        }

    async def get_monthly_energy_calendar(self, year: int, month: int, tenant_id: Optional[str] = None) -> dict:
        energy_payload = await self._fetch_energy_json(
            "/api/v1/energy/calendar/monthly",
            params={"year": year, "month": month, **({"tenant_id": tenant_id} if tenant_id else {})},
        )
        if energy_payload:
            payload = dict(energy_payload)
            payload.setdefault("stale", False)
            payload.setdefault("warnings", [])
            payload.setdefault("data_quality", "ok")
            payload.setdefault("no_nan_inf", True)
            payload.setdefault("cost_data_state", "fresh")
            payload.setdefault("cost_data_reasons", [])
            payload.setdefault("cost_generated_at", payload.get("generated_at"))
            return payload

        from app.services.dashboard import DashboardService

        svc = DashboardService(self._session, self._tenant_context(tenant_id))
        return await svc.get_monthly_energy(year=year, month=month)

    async def get_dashboard_bootstrap_summary(self, device_id: str, tenant_id: str) -> dict:
        now_utc = datetime.now(timezone.utc)

        row = (
            await self._session.execute(
                select(Device, DeviceLiveState)
                .outerjoin(
                    DeviceLiveState,
                    (DeviceLiveState.device_id == Device.device_id)
                    & (DeviceLiveState.tenant_id == Device.tenant_id),
                )
                .where(
                    Device.device_id == device_id,
                    Device.tenant_id == tenant_id,
                    Device.deleted_at.is_(None),
                )
            )
        ).first()

        if row is None:
            from app.services.dashboard import DashboardDeviceNotFoundError
            raise DashboardDeviceNotFoundError(device_id)

        device, state = row

        authoritative_last_seen = (
            state.last_telemetry_ts if state and state.last_telemetry_ts is not None
            else device.last_seen_timestamp
        )
        runtime_status = resolve_runtime_status(authoritative_last_seen, now_utc=now_utc)
        load_state = resolve_load_state(
            state.load_state if state else None,
            authoritative_last_seen,
            now_utc=now_utc,
        )

        thresholds = resolve_device_thresholds(device)
        current_band = (
            classify_current_band(
                float(state.last_current_a) if state and state.last_current_a is not None else None,
                float(state.last_voltage_v) if state and state.last_voltage_v is not None else None,
                thresholds,
            )
            if runtime_status == RuntimeStatus.RUNNING.value
            else "unknown"
        )

        operational_status = resolve_operational_status(
            runtime_status=runtime_status,
            load_state=load_state,
            current_band=current_band,
            has_telemetry=authoritative_last_seen is not None,
        )
        canonical_today_energy_kwh = None
        canonical_today_loss_kwh = None
        canonical_today_loss_cost_inr = None
        canonical_idle_kwh = None
        canonical_offhours_kwh = None
        canonical_overconsumption_kwh = None
        canonical_range = await self._fetch_energy_json(
            f"/api/v1/energy/device/{device_id}/range",
            params={
                "start_date": now_utc.astimezone(_get_platform_tz()).date().isoformat(),
                "end_date": now_utc.astimezone(_get_platform_tz()).date().isoformat(),
                **({"tenant_id": tenant_id} if tenant_id else {}),
            },
        )
        if canonical_range and isinstance(canonical_range.get("days"), list):
            today_iso = now_utc.astimezone(_get_platform_tz()).date().isoformat()
            for day_entry in canonical_range["days"]:
                if day_entry.get("date") == today_iso:
                    ce = day_entry.get("energy_kwh")
                    if isinstance(ce, (int, float)):
                        canonical_today_energy_kwh = round(float(ce), 4)
                    cl = day_entry.get("loss_kwh")
                    if isinstance(cl, (int, float)):
                        canonical_today_loss_kwh = round(float(cl), 4)
                    clc = day_entry.get("loss_cost_inr")
                    if isinstance(clc, (int, float)):
                        canonical_today_loss_cost_inr = round(float(clc), 4)
                    ci = day_entry.get("idle_kwh")
                    if isinstance(ci, (int, float)):
                        canonical_idle_kwh = round(float(ci), 4)
                    cof = day_entry.get("offhours_kwh")
                    if isinstance(cof, (int, float)):
                        canonical_offhours_kwh = round(float(cof), 4)
                    cov = day_entry.get("overconsumption_kwh")
                    if isinstance(cov, (int, float)):
                        canonical_overconsumption_kwh = round(float(cov), 4)
                    break
        loss_overview = await self._build_loss_overview(
            state=state,
            authoritative_last_seen=authoritative_last_seen,
            now_utc=now_utc,
            tenant_id=tenant_id,
            canonical_today_energy_kwh=canonical_today_energy_kwh,
            canonical_today_loss_kwh=canonical_today_loss_kwh,
            canonical_today_loss_cost_inr=canonical_today_loss_cost_inr,
            canonical_idle_kwh=canonical_idle_kwh,
            canonical_offhours_kwh=canonical_offhours_kwh,
            canonical_overconsumption_kwh=canonical_overconsumption_kwh,
        )
        current_shift_uptime = getattr(state, "current_shift_uptime_percentage", None) if state else None
        if current_shift_uptime is not None:
            shift_rows = (
                await self._session.execute(
                    select(DeviceShift).where(
                        DeviceShift.device_id == device_id,
                        DeviceShift.tenant_id == tenant_id,
                        DeviceShift.is_active.is_(True),
                    )
                )
            ).scalars().all()
            now_local = now_utc.astimezone(_get_platform_tz())
            if not LiveProjectionService._is_inside_shift(now_local, list(shift_rows)):
                current_shift_uptime = None
        daily_uptime = getattr(state, "today_uptime_percentage", None) if state else None
        if daily_uptime is None and state and state.uptime_percentage is not None:
            daily_uptime = float(state.uptime_percentage)
        overview_readiness = {
            "summary_ready": True,
            "telemetry_ready": authoritative_last_seen is not None,
            "health_ready": bool(state and state.health_score is not None),
            "uptime_ready": current_shift_uptime is not None,
            "loss_ready": bool(loss_overview.get("day_bucket")),
        }

        return {
            "success": True,
            "generated_at": now_utc.isoformat(),
            "version": int(state.version) if state else 0,
            "device_id": device.device_id,
            "device_name": device.device_name,
            "device_type": device.device_type,
            "plant_id": device.plant_id,
            "location": device.location,
            "runtime_status": runtime_status,
            "load_state": load_state,
            "current_band": current_band,
            "operational_status": operational_status,
            "last_seen_timestamp": self._iso_utc(authoritative_last_seen),
            "first_telemetry_timestamp": self._iso_utc(device.first_telemetry_timestamp),
            "health_score": round(float(state.health_score), 2) if state and state.health_score is not None else None,
            "uptime_percentage": round(float(current_shift_uptime), 2) if current_shift_uptime is not None else None,
            "current_shift_uptime_percentage": round(float(current_shift_uptime), 2) if current_shift_uptime is not None else None,
            "daily_uptime_percentage": round(float(daily_uptime), 2) if daily_uptime is not None else None,
            "full_load_current_a": thresholds.full_load_current_a,
            "idle_threshold_pct_of_fla": thresholds.idle_threshold_pct_of_fla,
            "derived_idle_threshold_a": thresholds.derived_idle_threshold_a,
            "derived_overconsumption_threshold_a": thresholds.derived_overconsumption_threshold_a,
            "last_current_a": float(state.last_current_a) if state and state.last_current_a is not None else None,
            "last_voltage_v": float(state.last_voltage_v) if state and state.last_voltage_v is not None else None,
            "data_source_type": getattr(device, "data_source_type", None),
            "data_freshness_ts": now_utc.isoformat(),
            "live_updated_at": self._iso_utc(state.updated_at) if state is not None else None,
            "loss_overview": loss_overview,
            "overview_readiness": overview_readiness,
        }

    @staticmethod
    def _decode_snapshot_json(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _decode_recent_telemetry_payload(payload: str | None) -> dict[str, Any] | None:
        if not payload:
            return None
        try:
            decoded = json.loads(payload)
        except Exception:
            return None
        return decoded if isinstance(decoded, dict) else None

    async def get_device_detail_snapshot(self, device_id: str, tenant_id: str) -> dict:
        now_utc = datetime.now(timezone.utc)
        row = (
            await self._session.execute(
                select(Device, DeviceLiveState, DeviceLatestTelemetrySnapshot)
                .outerjoin(
                    DeviceLiveState,
                    (DeviceLiveState.device_id == Device.device_id)
                    & (DeviceLiveState.tenant_id == Device.tenant_id),
                )
                .outerjoin(
                    DeviceLatestTelemetrySnapshot,
                    (DeviceLatestTelemetrySnapshot.device_id == Device.device_id)
                    & (DeviceLatestTelemetrySnapshot.tenant_id == Device.tenant_id),
                )
                .where(
                    Device.device_id == device_id,
                    Device.tenant_id == tenant_id,
                    Device.deleted_at.is_(None),
                )
            )
        ).first()

        if row is None:
            from app.services.dashboard import DashboardDeviceNotFoundError

            raise DashboardDeviceNotFoundError(device_id)

        device, state, snapshot = row
        thresholds = resolve_device_thresholds(device)
        authoritative_last_seen = (
            state.last_telemetry_ts if state and state.last_telemetry_ts is not None else device.last_seen_timestamp
        )
        runtime_status = resolve_runtime_status(authoritative_last_seen, now_utc=now_utc)
        load_state = resolve_load_state(
            state.load_state if state else None,
            authoritative_last_seen,
            now_utc=now_utc,
        )
        current_band = (
            classify_current_band(
                float(state.last_current_a) if state and state.last_current_a is not None else None,
                float(state.last_voltage_v) if state and state.last_voltage_v is not None else None,
                thresholds,
            )
            if runtime_status == RuntimeStatus.RUNNING.value
            else "unknown"
        )

        widget_config = await DevicePropertyService(self._session).get_dashboard_widget_config(device_id, tenant_id)
        health_configs = await HealthConfigService(self._session).get_health_configs_by_device(device_id, tenant_id)

        numeric_fields = self._decode_snapshot_json(snapshot.numeric_fields_json if snapshot else None)
        source_fields = self._decode_snapshot_json(snapshot.source_fields_json if snapshot else None)
        snapshot_sample_ts = self._as_utc(snapshot.sample_ts) if snapshot is not None else None
        freshness_age_seconds = (
            max(0, int((now_utc - snapshot_sample_ts).total_seconds()))
            if snapshot_sample_ts is not None
            else None
        )
        stale = bool(
            snapshot_sample_ts is None
            or freshness_age_seconds is None
            or freshness_age_seconds > TELEMETRY_TIMEOUT_SECONDS
        )
        recent_seed_rows = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                )
                .order_by(
                    DeviceRecentTelemetrySample.sample_ts.desc(),
                    DeviceRecentTelemetrySample.id.desc(),
                )
                .limit(100)
            )
        ).scalars().all()
        recent_telemetry = [
            payload
            for payload in (
                self._decode_recent_telemetry_payload(row.telemetry_json)
                for row in recent_seed_rows
            )
            if payload is not None
        ]
        if not recent_telemetry and snapshot_sample_ts is not None and numeric_fields:
            recent_telemetry = [
                {
                    "timestamp": self._iso_utc(snapshot_sample_ts),
                    "device_id": device_id,
                    "schema_version": "v1",
                    "enrichment_status": "pending",
                    **numeric_fields,
                }
            ]

        health_score: dict[str, Any] | None = None
        telemetry_values = HealthConfigService.extract_numeric_telemetry_values(numeric_fields)
        if telemetry_values and health_configs:
            try:
                health_score = await HealthConfigService(self._session).calculate_health_score(
                    device_id=device_id,
                    telemetry_values=telemetry_values,
                    machine_state=_health_machine_state_from_live_state(load_state, runtime_status),
                    tenant_id=tenant_id,
                )
            except Exception:
                health_score = None

        availability = {
            "snapshot_ready": bool(snapshot is not None and numeric_fields),
            "health_score_ready": health_score is not None,
            "widget_config_ready": bool(widget_config),
            "health_configs_ready": bool(health_configs),
            "recent_telemetry_ready": bool(recent_telemetry),
            "stale": stale,
        }

        return {
            "success": True,
            "generated_at": now_utc.isoformat(),
            "device_id": device.device_id,
            "data_freshness_ts": self._iso_utc(snapshot_sample_ts),
            "freshness_age_seconds": freshness_age_seconds,
            "availability": availability,
            "snapshot": (
                {
                    "sample_ts": self._iso_utc(snapshot_sample_ts),
                    "projection_version": int(snapshot.projection_version or 0),
                    "snapshot_version": int(snapshot.snapshot_version or 0),
                    "runtime_status": runtime_status,
                    "load_state": load_state,
                    "current_band": current_band,
                    "last_power_kw": float(snapshot.last_power_kw) if snapshot and snapshot.last_power_kw is not None else None,
                    "last_current_a": float(snapshot.last_current_a) if snapshot and snapshot.last_current_a is not None else None,
                    "last_voltage_v": float(snapshot.last_voltage_v) if snapshot and snapshot.last_voltage_v is not None else None,
                    "numeric_fields": numeric_fields,
                    "source_fields": source_fields,
                    "normalization_version": snapshot.normalization_version if snapshot is not None else None,
                    "updated_at": self._iso_utc(snapshot.updated_at) if snapshot is not None else None,
                }
                if snapshot is not None
                else None
            ),
            "health_score": health_score,
            "health_configs": [
                {
                    "id": config.id,
                    "device_id": config.device_id,
                    "tenant_id": config.tenant_id,
                    "parameter_name": config.parameter_name,
                    "normal_min": config.normal_min,
                    "normal_max": config.normal_max,
                    "weight": config.weight,
                    "ignore_zero_value": config.ignore_zero_value,
                    "is_active": config.is_active,
                    "created_at": config.created_at,
                    "updated_at": config.updated_at,
                }
                for config in health_configs
            ],
            "widget_config": widget_config,
            "recent_telemetry": recent_telemetry,
        }

    async def get_dashboard_bootstrap(self, device_id: str, tenant_id: str) -> dict:
        from app.services.dashboard import DashboardService

        svc = DashboardService(self._session, self._tenant_context(tenant_id))
        return await svc.get_dashboard_bootstrap(device_id=device_id, tenant_id=tenant_id)

    async def publish_device_update(self, device_id: str, tenant_id: str, *, partial: bool = True) -> dict:
        item = await self._projection.get_device_snapshot_item(device_id, tenant_id)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "warnings": [],
            "devices": [item],
            "partial": partial,
            "version": int(item.get("version") or 0),
        }

    @staticmethod
    def observe_stream_emit_lag(created_at: datetime) -> None:
        from app.services.dashboard import DashboardService

        DashboardService.observe_stream_emit_lag(created_at)

    @staticmethod
    def record_stream_disconnect(reason: str) -> None:
        from app.services.dashboard import DashboardService

        DashboardService.record_stream_disconnect(reason)
