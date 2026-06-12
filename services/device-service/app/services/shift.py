"""Shift service layer - business logic for shift management and uptime calculation."""

import json
from typing import Optional, List, Any, Dict, Tuple
from datetime import datetime, time as time_type, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.device import DeviceShift, Device, DeviceRecentTelemetrySample
from app.schemas.device import ShiftCreate, ShiftUpdate
from app.services.shared_http import get_client, request_with_retries
from services.shared.telemetry_normalization import normalize_telemetry_sample
from services.shared.tenant_context import build_internal_headers
import logging

logger = logging.getLogger(__name__)


class ShiftOverlapError(Exception):
    """Raised when a shift overlaps with existing shifts for a device."""

    def __init__(self, message: str, conflicts: List[Dict[str, Any]]):
        super().__init__(message)
        self.conflicts = conflicts


class ShiftService:
    """Service layer for shift management and uptime calculation."""
    
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _time_to_minutes(value: time_type) -> int:
        return value.hour * 60 + value.minute

    def _expand_shift_segments(
        self,
        shift_start: time_type,
        shift_end: time_type,
        day_of_week: Optional[int],
    ) -> List[Tuple[int, int, int]]:
        """Expand shift into daily segments with end-exclusive minute ranges.

        Returns tuples of (weekday_0_mon, start_minute, end_minute).
        """
        start_m = self._time_to_minutes(shift_start)
        end_m = self._time_to_minutes(shift_end)
        if start_m == end_m:
            raise ValueError("Shift start and end times cannot be the same")

        days = list(range(7)) if day_of_week is None else [day_of_week]
        segments: List[Tuple[int, int, int]] = []
        for day in days:
            if end_m > start_m:
                segments.append((day, start_m, end_m))
                continue
            # Crossing midnight: [start,24:00) on start day + [00:00,end) on next day.
            segments.append((day, start_m, 24 * 60))
            segments.append(((day + 1) % 7, 0, end_m))
        return segments

    @staticmethod
    def _segments_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        # End-exclusive boundary: touching edges are valid.
        return a_start < b_end and b_start < a_end

    @staticmethod
    def _normalize_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _decode_recent_payload(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    async def _fetch_recent_projection_window(
        self,
        device_id: str,
        tenant_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[list[dict[str, Any]], bool]:
        oldest_recent = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample.sample_ts)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.asc(), DeviceRecentTelemetrySample.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        oldest_recent_utc = self._normalize_utc(oldest_recent)
        if oldest_recent_utc is None or start_utc < oldest_recent_utc:
            return [], False

        rows = (
            await self._session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == device_id,
                    DeviceRecentTelemetrySample.tenant_id == tenant_id,
                    DeviceRecentTelemetrySample.sample_ts >= start_utc,
                    DeviceRecentTelemetrySample.sample_ts <= end_utc,
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.asc(), DeviceRecentTelemetrySample.id.asc())
            )
        ).scalars().all()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            payload = self._decode_recent_payload(row.telemetry_json)
            sample_ts = self._normalize_utc(row.sample_ts)
            if not payload or sample_ts is None:
                continue
            payload.setdefault("timestamp", sample_ts.isoformat())
            payload.setdefault("device_id", device_id)
            payloads.append(payload)
        return payloads, True

    async def _validate_no_shift_overlap(
        self,
        device_id: str,
        tenant_id: Optional[str],
        shift_start: time_type,
        shift_end: time_type,
        day_of_week: Optional[int],
        exclude_shift_id: Optional[int] = None,
    ) -> None:
        """Validate a candidate shift does not overlap with existing shifts."""
        candidate_segments = self._expand_shift_segments(shift_start, shift_end, day_of_week)

        query = select(DeviceShift).where(DeviceShift.device_id == device_id)
        if tenant_id:
            query = query.where(DeviceShift.tenant_id == tenant_id)
        if exclude_shift_id is not None:
            query = query.where(DeviceShift.id != exclude_shift_id)

        result = await self._session.execute(query)
        existing_shifts = list(result.scalars().all())

        conflicts: List[Dict[str, Any]] = []
        for existing in existing_shifts:
            existing_segments = self._expand_shift_segments(
                existing.shift_start,
                existing.shift_end,
                existing.day_of_week,
            )
            has_overlap = False
            for c_day, c_start, c_end in candidate_segments:
                for e_day, e_start, e_end in existing_segments:
                    if c_day != e_day:
                        continue
                    if self._segments_overlap(c_start, c_end, e_start, e_end):
                        has_overlap = True
                        break
                if has_overlap:
                    break
            if has_overlap:
                conflicts.append(
                    {
                        "shift_id": existing.id,
                        "shift_name": existing.shift_name,
                        "day_of_week": existing.day_of_week,
                        "shift_start": existing.shift_start.strftime("%H:%M"),
                        "shift_end": existing.shift_end.strftime("%H:%M"),
                        "is_active": existing.is_active,
                    }
                )

        if conflicts:
            logger.warning(
                "Shift overlap rejected",
                extra={
                    "device_id": device_id,
                    "tenant_id": tenant_id,
                    "candidate_day_of_week": day_of_week,
                    "candidate_start": shift_start.strftime("%H:%M"),
                    "candidate_end": shift_end.strftime("%H:%M"),
                    "conflicting_shift_ids": [c["shift_id"] for c in conflicts],
                },
            )
            raise ShiftOverlapError(
                "Shift overlaps with existing shifts for this device",
                conflicts=conflicts,
            )
    
    async def create_shift(self, shift_data: ShiftCreate) -> DeviceShift:
        """Create a new shift configuration for a device.
        
        Args:
            shift_data: Shift creation data
            
        Returns:
            Created DeviceShift instance
        """
        # Verify device exists
        device_query = select(Device).where(Device.device_id == shift_data.device_id)
        if shift_data.tenant_id:
            device_query = device_query.where(Device.tenant_id == shift_data.tenant_id)

        result = await self._session.execute(device_query)
        device = result.scalar_one_or_none()
        
        if not device:
            raise ValueError(f"Device '{shift_data.device_id}' not found")
        
        # Validate shift times / overlap constraints
        if shift_data.shift_start == shift_data.shift_end:
            raise ValueError("Shift start and end times cannot be the same")
        await self._validate_no_shift_overlap(
            device_id=shift_data.device_id,
            tenant_id=shift_data.tenant_id,
            shift_start=shift_data.shift_start,
            shift_end=shift_data.shift_end,
            day_of_week=shift_data.day_of_week,
            exclude_shift_id=None,
        )
        
        # Create shift
        shift = DeviceShift(
            device_id=shift_data.device_id,
            tenant_id=shift_data.tenant_id,
            shift_name=shift_data.shift_name,
            shift_start=shift_data.shift_start,
            shift_end=shift_data.shift_end,
            maintenance_break_minutes=shift_data.maintenance_break_minutes,
            day_of_week=shift_data.day_of_week,
            is_active=shift_data.is_active,
        )
        
        self._session.add(shift)
        
        try:
            await self._session.commit()
            await self._session.refresh(shift)
            logger.info(
                "Shift created successfully",
                extra={
                    "shift_id": shift.id,
                    "device_id": shift.device_id,
                    "shift_name": shift.shift_name,
                }
            )
        except IntegrityError as e:
            await self._session.rollback()
            logger.error("Failed to create shift", extra={"error": str(e)})
            raise
        
        return shift
    
    async def get_shifts_by_device(
        self, 
        device_id: str, 
        tenant_id: Optional[str] = None
    ) -> List[DeviceShift]:
        """Get all shifts for a device.
        
        Args:
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            List of DeviceShift instances
        """
        query = select(DeviceShift).where(DeviceShift.device_id == device_id)
        
        if tenant_id:
            query = query.where(DeviceShift.tenant_id == tenant_id)
        
        query = query.order_by(DeviceShift.shift_start)
        
        result = await self._session.execute(query)
        return list(result.scalars().all())
    
    async def get_shift(
        self, 
        shift_id: int, 
        device_id: str,
        tenant_id: Optional[str] = None
    ) -> Optional[DeviceShift]:
        """Get a specific shift by ID.
        
        Args:
            shift_id: Shift ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            DeviceShift instance or None
        """
        query = select(DeviceShift).where(
            DeviceShift.id == shift_id,
            DeviceShift.device_id == device_id
        )
        
        if tenant_id:
            query = query.where(DeviceShift.tenant_id == tenant_id)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def update_shift(
        self,
        shift_id: int,
        device_id: str,
        tenant_id: Optional[str],
        shift_data: ShiftUpdate
    ) -> Optional[DeviceShift]:
        """Update an existing shift.
        
        Args:
            shift_id: Shift ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            shift_data: Update data
            
        Returns:
            Updated DeviceShift instance or None
        """
        shift = await self.get_shift(shift_id, device_id, tenant_id)
        
        if not shift:
            return None
        
        # Update fields
        update_data = shift_data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(shift, field, value)
        
        # Validate shift times if updated
        if shift.shift_start == shift.shift_end:
            raise ValueError("Shift start and end times cannot be the same")
        await self._validate_no_shift_overlap(
            device_id=device_id,
            tenant_id=tenant_id,
            shift_start=shift.shift_start,
            shift_end=shift.shift_end,
            day_of_week=shift.day_of_week,
            exclude_shift_id=shift_id,
        )
        
        try:
            await self._session.commit()
            await self._session.refresh(shift)
            logger.info(
                "Shift updated successfully",
                extra={"shift_id": shift.id}
            )
        except IntegrityError as e:
            await self._session.rollback()
            logger.error("Failed to update shift", extra={"error": str(e)})
            raise
        
        return shift
    
    async def delete_shift(
        self,
        shift_id: int,
        device_id: str,
        tenant_id: Optional[str]
    ) -> bool:
        """Delete a shift.
        
        Args:
            shift_id: Shift ID
            device_id: Device ID
            tenant_id: Optional tenant ID for filtering
            
        Returns:
            True if deleted, False if not found
        """
        shift = await self.get_shift(shift_id, device_id, tenant_id)
        
        if not shift:
            return False
        
        await self._session.delete(shift)
        await self._session.commit()
        
        logger.info(
            "Shift deleted successfully",
            extra={"shift_id": shift_id}
        )
        
        return True

    @staticmethod
    def _is_running_sample(sample: dict[str, Any]) -> bool:
        """Determine running signal from telemetry sample."""
        normalized = normalize_telemetry_sample(sample, sample)
        if normalized.business_power_w > 0:
            return True
        if normalized.current_a is None:
            return False
        if normalized.voltage_v is None:
            return normalized.current_a > 0
        return normalized.current_a > 0 and normalized.voltage_v > 0

    @staticmethod
    def _is_shift_active_now(
        shift: DeviceShift,
        now_local: datetime,
    ) -> bool:
        """Check if a shift is active for the current local timestamp."""
        start = shift.shift_start
        end = shift.shift_end
        now_t = now_local.time()
        weekday = now_local.weekday()
        configured_day = shift.day_of_week

        # Non-crossing shift, e.g. 09:00-18:00
        if end > start:
            in_time = start <= now_t < end
            if not in_time:
                return False
            return configured_day is None or configured_day == weekday

        # Crossing-midnight shift, e.g. 20:00-06:00
        if now_t >= start:
            # Start segment on current day
            return configured_day is None or configured_day == weekday

        # End segment after midnight; start day is previous day
        prev_day = (weekday - 1) % 7
        return configured_day is None or configured_day == prev_day

    @staticmethod
    def _shift_window_for_now(
        shift: DeviceShift,
        now_local: datetime,
    ) -> tuple[datetime, datetime]:
        """Get active shift window bounds for current local time."""
        start = shift.shift_start
        end = shift.shift_end
        today = now_local.date()
        now_t = now_local.time()

        if end > start:
            start_dt = datetime.combine(today, start, tzinfo=now_local.tzinfo)
            end_dt = datetime.combine(today, end, tzinfo=now_local.tzinfo)
            return start_dt, end_dt

        # Crossing-midnight
        if now_t >= start:
            start_dt = datetime.combine(today, start, tzinfo=now_local.tzinfo)
            end_dt = datetime.combine(today + timedelta(days=1), end, tzinfo=now_local.tzinfo)
        else:
            start_dt = datetime.combine(today - timedelta(days=1), start, tzinfo=now_local.tzinfo)
            end_dt = datetime.combine(today, end, tzinfo=now_local.tzinfo)
        return start_dt, end_dt

    @staticmethod
    def _shift_occurrence_for_date(
        shift: DeviceShift,
        occurrence_date,
        tzinfo,
    ) -> tuple[datetime, datetime]:
        start_dt = datetime.combine(occurrence_date, shift.shift_start, tzinfo=tzinfo)
        end_date = occurrence_date if shift.shift_end > shift.shift_start else occurrence_date + timedelta(days=1)
        end_dt = datetime.combine(end_date, shift.shift_end, tzinfo=tzinfo)
        return start_dt, end_dt

    def _iter_shift_overlaps(
        self,
        shifts: List[DeviceShift],
        window_start_local: datetime,
        window_end_local: datetime,
    ) -> List[tuple[datetime, datetime, DeviceShift]]:
        overlaps: List[tuple[datetime, datetime, DeviceShift]] = []
        start_date = window_start_local.date() - timedelta(days=1)
        end_date = window_end_local.date()
        current_date = start_date

        while current_date <= end_date:
            weekday = current_date.weekday()
            for shift in shifts:
                if shift.day_of_week is not None and shift.day_of_week != weekday:
                    continue
                occurrence_start, occurrence_end = self._shift_occurrence_for_date(
                    shift,
                    current_date,
                    window_start_local.tzinfo,
                )
                overlap_start = max(window_start_local, occurrence_start)
                overlap_end = min(window_end_local, occurrence_end)
                if overlap_end <= overlap_start:
                    continue
                overlaps.append((overlap_start, overlap_end, shift))
            current_date += timedelta(days=1)

        overlaps.sort(key=lambda item: item[0])
        return overlaps

    async def calculate_uptime_for_window(
        self,
        device_id: str,
        tenant_id: Optional[str],
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> dict:
        """Calculate uptime for a specific historical window using overlapping active shifts."""
        if tenant_id is None:
            raise ValueError("tenant_id is required for uptime telemetry reads")

        window_start_utc = window_start_utc.astimezone(timezone.utc)
        window_end_utc = window_end_utc.astimezone(timezone.utc)
        if window_end_utc <= window_start_utc:
            raise ValueError("window_end_utc must be after window_start_utc")

        shifts = await self.get_shifts_by_device(device_id, tenant_id)
        local_tz = ZoneInfo(settings.PLATFORM_TIMEZONE)

        if not shifts:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": 0,
                "window_start": window_start_utc.astimezone(local_tz).isoformat(),
                "window_end": window_end_utc.astimezone(local_tz).isoformat(),
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_historical_shift_window",
                "message": "No shifts configured. Please configure shifts to calculate uptime.",
            }

        active_shifts = [s for s in shifts if s.is_active]
        if not active_shifts:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": len(shifts),
                "window_start": window_start_utc.astimezone(local_tz).isoformat(),
                "window_end": window_end_utc.astimezone(local_tz).isoformat(),
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_historical_shift_window",
                "message": "No active shifts configured. Please activate at least one shift.",
            }

        window_start_local = window_start_utc.astimezone(local_tz)
        window_end_local = window_end_utc.astimezone(local_tz)
        overlaps = self._iter_shift_overlaps(active_shifts, window_start_local, window_end_local)
        if not overlaps:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": len(active_shifts),
                "window_start": window_start_local.isoformat(),
                "window_end": window_end_local.isoformat(),
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_historical_shift_window",
                "message": "No active shift window intersects the selected period.",
            }

        telemetry_items, recent_covered = await self._fetch_recent_projection_window(
            device_id,
            tenant_id,
            window_start_utc,
            window_end_utc,
        )
        if not recent_covered:
            try:
                telemetry_items = await self._fetch_telemetry_window(
                    device_id,
                    tenant_id,
                    window_start_utc,
                    window_end_utc,
                )
            except Exception as exc:
                logger.warning(
                    "Historical uptime telemetry fetch failed",
                    extra={"device_id": device_id, "error": str(exc)},
                )
                planned_minutes = int(round(sum((end - start).total_seconds() for start, end, _ in overlaps) / 60.0))
                effective_minutes = 0
                return {
                    "device_id": device_id,
                    "uptime_percentage": None,
                    "total_planned_minutes": planned_minutes,
                    "total_effective_minutes": effective_minutes,
                    "actual_running_minutes": 0,
                    "shifts_configured": len(active_shifts),
                    "window_start": window_start_local.isoformat(),
                    "window_end": window_end_local.isoformat(),
                    "window_timezone": settings.PLATFORM_TIMEZONE,
                    "data_coverage_pct": 0.0,
                    "data_quality": "low",
                    "calculation_mode": "runtime_telemetry_historical_shift_window",
                    "message": "Telemetry unavailable for historical uptime computation.",
                }

        points: list[tuple[datetime, dict[str, Any]]] = []
        for item in telemetry_items:
            ts_raw = item.get("timestamp")
            if ts_raw is None:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if window_start_utc <= ts <= window_end_utc:
                points.append((ts, item))

        points.sort(key=lambda x: x[0])

        planned_seconds_total = 0.0
        effective_seconds_total = 0.0
        for overlap_start_local, overlap_end_local, shift in overlaps:
            overlap_seconds = max(0.0, (overlap_end_local - overlap_start_local).total_seconds())
            planned_seconds_total += overlap_seconds
            planned_duration_seconds = max(shift.planned_duration_minutes * 60.0, 1.0)
            effective_ratio = max(0.0, min(1.0, float(shift.effective_runtime_minutes) / float(max(shift.planned_duration_minutes, 1))))
            effective_seconds_total += overlap_seconds * effective_ratio

        running_seconds = 0.0
        skipped_non_monotonic = 0
        skipped_duplicate = 0

        overlap_windows_utc = [
            (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
            for start, end, _shift in overlaps
        ]

        def _inside_overlap(ts: datetime) -> bool:
            return any(start <= ts < end for start, end in overlap_windows_utc)

        for idx in range(1, len(points)):
            prev_ts, prev_sample = points[idx - 1]
            cur_ts, _ = points[idx]
            dt_sec = (cur_ts - prev_ts).total_seconds()
            if dt_sec < 0:
                skipped_non_monotonic += 1
                continue
            if dt_sec == 0:
                skipped_duplicate += 1
                continue
            if not _inside_overlap(prev_ts):
                continue
            if self._is_running_sample(prev_sample):
                running_seconds += dt_sec

        actual_running_minutes = int(round(running_seconds / 60.0))
        effective_minutes = int(round(effective_seconds_total / 60.0))
        planned_minutes = int(round(planned_seconds_total / 60.0))
        uptime_percentage = None
        if effective_seconds_total > 0:
            uptime_percentage = max(0.0, min(100.0, (running_seconds / effective_seconds_total) * 100.0))

        coverage_seconds = 0.0
        if len(points) >= 2:
            inside_points = [ts for ts, _ in points if _inside_overlap(ts)]
            if len(inside_points) >= 2:
                coverage_seconds = max((inside_points[-1] - inside_points[0]).total_seconds(), 0.0)
        coverage_pct = round(max(0.0, min(100.0, (coverage_seconds / planned_seconds_total) * 100.0)), 2) if planned_seconds_total > 0 else 0.0

        if coverage_pct >= 80:
            quality = "high"
        elif coverage_pct >= 40:
            quality = "medium"
        else:
            quality = "low"

        notes = []
        if skipped_non_monotonic:
            notes.append(f"skipped {skipped_non_monotonic} non-monotonic samples")
        if skipped_duplicate:
            notes.append(f"skipped {skipped_duplicate} duplicate-timestamp samples")
        note_text = f" ({'; '.join(notes)})" if notes else ""
        shift_names = ", ".join(dict.fromkeys(shift.shift_name for _, _, shift in overlaps))

        return {
            "device_id": device_id,
            "uptime_percentage": round(uptime_percentage, 2) if uptime_percentage is not None else None,
            "total_planned_minutes": planned_minutes,
            "total_effective_minutes": effective_minutes,
            "actual_running_minutes": actual_running_minutes,
            "shifts_configured": len(active_shifts),
            "window_start": window_start_local.isoformat(),
            "window_end": window_end_local.isoformat(),
            "window_timezone": settings.PLATFORM_TIMEZONE,
            "data_coverage_pct": coverage_pct,
            "data_quality": quality,
            "calculation_mode": "runtime_telemetry_historical_shift_window",
            "message": f"Runtime uptime computed from telemetry for recent shift window(s): {shift_names}{note_text}",
        }

    async def _fetch_telemetry_window(
        self,
        device_id: str,
        tenant_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch bounded telemetry for uptime computation."""
        url = f"{settings.DATA_SERVICE_BASE_URL}/api/v1/data/telemetry/{device_id}"
        params = {
            "start_time": start_utc.isoformat(),
            "end_time": end_utc.isoformat(),
            "limit": 10000,
        }
        client = await get_client(settings.DATA_SERVICE_BASE_URL)
        response = await request_with_retries(
            client,
            "GET",
            f"/api/v1/data/telemetry/{device_id}",
            operation="shift_fetch_telemetry_window",
            params=params,
            headers=build_internal_headers("device-service", tenant_id),
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()

        items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        return items
    
    async def calculate_uptime(
        self,
        device_id: str,
        tenant_id: Optional[str] = None,
        shifts: Optional[List[DeviceShift]] = None,
    ) -> dict:
        """Calculate real uptime from telemetry within the current active shift window."""
        if shifts is None:
            shifts = await self.get_shifts_by_device(device_id, tenant_id)
        local_tz = ZoneInfo(settings.PLATFORM_TIMEZONE)
        
        if not shifts:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": 0,
                "window_start": None,
                "window_end": None,
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_shift_window",
                "message": "No shifts configured. Please configure shifts to calculate uptime.",
            }
        
        active_shifts = [s for s in shifts if s.is_active]
        
        if not active_shifts:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": len(shifts),
                "window_start": None,
                "window_end": None,
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_shift_window",
                "message": "No active shifts configured. Please activate at least one shift.",
            }

        now_local = datetime.now(timezone.utc).astimezone(local_tz)
        current_shift = next((s for s in active_shifts if self._is_shift_active_now(s, now_local)), None)

        if current_shift is None:
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": len(active_shifts),
                "window_start": None,
                "window_end": None,
                "window_timezone": settings.PLATFORM_TIMEZONE,
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_shift_window",
                "message": "No currently active shift window at this time.",
            }

        window_start_local, window_end_local = self._shift_window_for_now(current_shift, now_local)
        window_start_utc = window_start_local.astimezone(timezone.utc)
        window_end_utc = window_end_local.astimezone(timezone.utc)

        planned_minutes = int(current_shift.planned_duration_minutes)
        effective_minutes = max(0, int(current_shift.effective_runtime_minutes))

        if tenant_id is None:
            raise ValueError("tenant_id is required for uptime telemetry reads")

        telemetry_items, recent_covered = await self._fetch_recent_projection_window(
            device_id,
            tenant_id,
            window_start_utc,
            window_end_utc,
        )
        if not recent_covered:
            try:
                telemetry_items = await self._fetch_telemetry_window(
                    device_id,
                    tenant_id,
                    window_start_utc,
                    window_end_utc,
                )
            except Exception as exc:
                logger.warning(
                    "Uptime telemetry fetch failed",
                    extra={"device_id": device_id, "error": str(exc)},
                )
                return {
                    "device_id": device_id,
                    "uptime_percentage": None,
                    "total_planned_minutes": planned_minutes,
                    "total_effective_minutes": effective_minutes,
                    "actual_running_minutes": 0,
                    "shifts_configured": len(active_shifts),
                    "window_start": window_start_local.isoformat(),
                    "window_end": window_end_local.isoformat(),
                    "window_timezone": settings.PLATFORM_TIMEZONE,
                    "data_coverage_pct": 0.0,
                    "data_quality": "low",
                    "calculation_mode": "runtime_telemetry_shift_window",
                    "message": "Telemetry unavailable for uptime computation.",
                }

        points: list[tuple[datetime, dict[str, Any]]] = []
        for item in telemetry_items:
            ts_raw = item.get("timestamp")
            if ts_raw is None:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < window_start_utc or ts > window_end_utc:
                continue
            points.append((ts, item))

        points.sort(key=lambda x: x[0])
        running_seconds = 0.0
        skipped_non_monotonic = 0
        skipped_duplicate = 0

        for idx in range(1, len(points)):
            prev_ts, prev_sample = points[idx - 1]
            cur_ts, _ = points[idx]
            dt_sec = (cur_ts - prev_ts).total_seconds()
            if dt_sec < 0:
                skipped_non_monotonic += 1
                continue
            if dt_sec == 0:
                skipped_duplicate += 1
                continue
            if self._is_running_sample(prev_sample):
                running_seconds += dt_sec

        actual_running_minutes = int(round(running_seconds / 60.0))
        if effective_minutes > 0:
            effective_seconds = float(effective_minutes * 60)
            uptime_percentage = max(0.0, min(100.0, (running_seconds / effective_seconds) * 100.0))
        else:
            uptime_percentage = None

        window_seconds = max((window_end_utc - window_start_utc).total_seconds(), 0.0)
        coverage_seconds = 0.0
        if len(points) >= 2:
            coverage_seconds = max((points[-1][0] - points[0][0]).total_seconds(), 0.0)
        coverage_pct = round(max(0.0, min(100.0, (coverage_seconds / window_seconds) * 100.0)), 2) if window_seconds > 0 else 0.0

        if coverage_pct >= 80:
            quality = "high"
        elif coverage_pct >= 40:
            quality = "medium"
        else:
            quality = "low"

        notes = []
        if skipped_non_monotonic:
            notes.append(f"skipped {skipped_non_monotonic} non-monotonic samples")
        if skipped_duplicate:
            notes.append(f"skipped {skipped_duplicate} duplicate-timestamp samples")
        note_text = f" ({'; '.join(notes)})" if notes else ""

        return {
            "device_id": device_id,
            "uptime_percentage": round(uptime_percentage, 2) if uptime_percentage is not None else None,
            "total_planned_minutes": planned_minutes,
            "total_effective_minutes": effective_minutes,
            "actual_running_minutes": actual_running_minutes,
            "shifts_configured": len(active_shifts),
            "window_start": window_start_local.isoformat(),
            "window_end": window_end_local.isoformat(),
            "window_timezone": settings.PLATFORM_TIMEZONE,
            "data_coverage_pct": coverage_pct,
            "data_quality": quality,
            "calculation_mode": "runtime_telemetry_shift_window",
            "message": f"Runtime uptime computed from telemetry for active shift '{current_shift.shift_name}'{note_text}",
        }
