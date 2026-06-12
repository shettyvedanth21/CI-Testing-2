"""Dataset access service - reads from S3 only."""

import io
import re
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd
import structlog

from src.config.settings import get_settings
from src.infrastructure.s3_client import S3Client
from src.utils.exceptions import AnalyticsError, DatasetNotFoundError, DatasetReadError

try:
    from services.shared.tenant_context import build_internal_headers, build_tenant_scoped_internal_headers
    from services.shared.telemetry_normalization import (
        DevicePowerConfig,
        build_device_power_config,
        normalize_telemetry_sample,
    )
    from services.shared.telemetry_contract import DIAGNOSTIC_PHASE_TELEMETRY_FIELDS
except ModuleNotFoundError:  # pragma: no cover - unit-test path fallback
    import sys
    from pathlib import Path

    services_root = Path(__file__).resolve().parents[3]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))
    from shared.tenant_context import build_internal_headers, build_tenant_scoped_internal_headers  # type: ignore
    from shared.telemetry_normalization import (  # type: ignore
        DevicePowerConfig,
        build_device_power_config,
        normalize_telemetry_sample,
    )
    from shared.telemetry_contract import DIAGNOSTIC_PHASE_TELEMETRY_FIELDS  # type: ignore

logger = structlog.get_logger()


class DatasetService:
    """Service for accessing datasets from S3."""

    def __init__(self, s3_client: S3Client):
        self._s3 = s3_client
        self._logger = logger.bind(service="DatasetService")

    def _enforce_dataset_size(
        self,
        df: pd.DataFrame,
        *,
        device_id: Optional[str] = None,
    ) -> pd.DataFrame:
        settings = get_settings()
        if df.empty:
            return df
        size_mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
        max_mb = self._coerce_positive_int(
            getattr(settings, "max_dataset_size_mb", 500),
            default=500,
        )

        if size_mb > max_mb:
            self._logger.warning(
                "dataset_size_limit_exceeded",
                device_id=device_id,
                rows=len(df),
                size_mb=round(size_mb, 1),
                max_mb=max_mb,
            )

        self._logger.info(
            "dataset_loaded_size",
            device_id=device_id,
            rows=len(df),
            size_mb=round(size_mb, 1),
        )
        max_rows = self._coerce_positive_int(
            getattr(settings, "ml_max_dataset_rows", 500000),
            default=500000,
        )
        if len(df) > max_rows:
            self._logger.warning(
                "dataset_truncated_for_ml",
                device_id=device_id,
                original_rows=len(df),
                truncated_to=max_rows,
            )
            df = df.tail(max_rows).reset_index(drop=True)
        return df

    @staticmethod
    def _coerce_positive_int(value: object, *, default: int) -> int:
        if not isinstance(value, (int, float, str)):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    async def load_dataset(
        self,
        device_id: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        s3_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load dataset from S3.

        If s3_key is provided, it is used directly.
        Otherwise, the key is constructed from device_id and time range.
        """

        # s3_key takes priority and does NOT require start/end time
        requested_range = start_time is not None and end_time is not None
        generated_key = False
        if s3_key is None:
            if start_time is None or end_time is None:
                raise DatasetReadError(
                    "start_time and end_time must be provided when s3_key is not specified"
                )

            s3_key = self._construct_s3_key(
                device_id,
                start_time,
                end_time,
            )
            generated_key = True

            self._logger.info(
                "loading_dataset",
                device_id=device_id,
                s3_key=s3_key,
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
            )
        else:
            self._logger.info(
                "loading_dataset",
                device_id=device_id,
                s3_key=s3_key,
                mode="explicit_dataset_key",
            )

        try:
            data = await self._s3.download_file(s3_key)

            df = pd.read_parquet(io.BytesIO(data))

            df = self._enforce_dataset_size(df, device_id=device_id)
            self._logger.info(
                "dataset_loaded",
                device_id=device_id,
                rows=len(df),
                columns=list(df.columns),
            )

            return await self._normalize_business_dataset(
                df,
                device_id=device_id,
                tenant_id=tenant_id,
            )

        except Exception as e:
            self._logger.error(
                "dataset_load_failed",
                device_id=device_id,
                s3_key=s3_key,
                error=str(e),
            )

            not_found = "Not Found" in str(e) or "NoSuchKey" in str(e)
            if not_found and generated_key and requested_range and start_time and end_time:
                fallback_key = await self._find_best_available_key(
                    device_id=device_id,
                    start_time=start_time,
                    end_time=end_time,
                )
                settings = get_settings()
                strict_range = bool(
                    settings.ml_require_exact_dataset_range and settings.app_env.lower() != "test"
                )
                allow_fallback = (
                    not strict_range
                    or self.dataset_key_covers_range(fallback_key, start_time, end_time)
                )
                if fallback_key and allow_fallback:
                    self._logger.warning(
                        "dataset_range_missing_fallback_to_available",
                        device_id=device_id,
                        requested_key=s3_key,
                        fallback_key=fallback_key,
                    )
                    data = await self._s3.download_file(fallback_key)
                    df = pd.read_parquet(io.BytesIO(data))
                    df = self._enforce_dataset_size(df, device_id=device_id)
                    self._logger.info(
                        "dataset_loaded",
                        device_id=device_id,
                        rows=len(df),
                        columns=list(df.columns),
                        fallback_key=fallback_key,
                    )
                    return await self._normalize_business_dataset(
                        df,
                        device_id=device_id,
                        tenant_id=tenant_id,
                    )
                if fallback_key and not allow_fallback:
                    self._logger.warning(
                        "dataset_range_fallback_rejected",
                        device_id=device_id,
                        requested_key=s3_key,
                        fallback_key=fallback_key,
                        reason="fallback_does_not_cover_requested_range",
                    )
                live_df = await self._load_from_data_service(
                    device_id=device_id,
                    start_time=start_time,
                    end_time=end_time,
                    tenant_id=tenant_id,
                )
                if not live_df.empty:
                    self._logger.warning(
                        "dataset_loaded_from_data_service_fallback",
                        device_id=device_id,
                        rows=len(live_df),
                        requested_key=s3_key,
                    )
                    return await self._normalize_business_dataset(
                        live_df,
                        device_id=device_id,
                        tenant_id=tenant_id,
                    )

            if not_found:
                raise DatasetNotFoundError(f"Dataset not found: {s3_key}")

            raise DatasetReadError(f"Failed to read dataset: {e}") from e

    async def _load_from_data_service(
        self,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
        tenant_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """Load exact-range telemetry directly from data-service as a hard fallback."""
        settings = get_settings()
        url = f"{settings.data_service_url}/api/v1/data/telemetry/{device_id}"
        chunk_hours = max(1, int(settings.data_service_fallback_chunk_hours))
        query_limit = max(1, int(settings.data_service_query_limit))
        chunk_dfs: list[pd.DataFrame] = []

        async def _fetch_chunk(client: httpx.AsyncClient, chunk_start: datetime, chunk_end: datetime) -> list[dict]:
            params = {
                "start_time": chunk_start.isoformat(),
                "end_time": chunk_end.isoformat(),
                "limit": query_limit,
            }
            resp = await client.get(
                url,
                params=params,
                headers=build_internal_headers("analytics-service", tenant_id),
            )
            resp.raise_for_status()
            body = resp.json()
            items = []
            if isinstance(body, dict):
                data = body.get("data", {})
                if isinstance(data, dict):
                    items = data.get("items", []) or []
                elif isinstance(data, list):
                    items = data
            elif isinstance(body, list):
                items = body
            return [x for x in items if isinstance(x, dict)]

        try:
            async with httpx.AsyncClient(timeout=float(settings.data_service_query_timeout_seconds)) as client:
                cursor = start_time
                while cursor < end_time:
                    chunk_end = min(end_time, cursor + timedelta(hours=chunk_hours))
                    items = await _fetch_chunk(client, cursor, chunk_end)
                    if items:
                        chunk_dfs.append(pd.DataFrame(items))
                    if len(items) >= query_limit:
                        self._logger.warning(
                            "data_service_fallback_chunk_hit_limit",
                            device_id=device_id,
                            chunk_start=cursor.isoformat(),
                            chunk_end=chunk_end.isoformat(),
                            limit=query_limit,
                        )
                    cursor = chunk_end
        except Exception as e:
            self._logger.warning(
                "data_service_fallback_failed",
                device_id=device_id,
                error=str(e),
            )
            return pd.DataFrame()

        if not chunk_dfs:
            return pd.DataFrame()

        df = pd.concat(chunk_dfs, ignore_index=True) if chunk_dfs else pd.DataFrame()
        if df.empty:
            return df
        if "timestamp" not in df.columns and "_time" in df.columns:
            df = df.rename(columns={"_time": "timestamp"})
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.reset_index(drop=True)
        df = self._enforce_dataset_size(df, device_id=device_id)
        return df

    async def _fetch_device_power_config(
        self,
        *,
        device_id: str,
        tenant_id: Optional[str],
    ) -> DevicePowerConfig:
        if not tenant_id:
            return build_device_power_config({})

        settings = get_settings()
        if not settings.device_service_url:
            return build_device_power_config({})

        headers = build_tenant_scoped_internal_headers("analytics-service", tenant_id)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{settings.device_service_url}/api/v1/devices/{device_id}",
                    headers=headers,
                )
                response.raise_for_status()
        except Exception as exc:
            self._logger.warning(
                "device_power_config_lookup_failed",
                device_id=device_id,
                tenant_id=tenant_id,
                error=str(exc),
            )
            return build_device_power_config({})

        payload = response.json()
        device = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(device, dict):
            self._logger.warning(
                "device_power_config_missing",
                device_id=device_id,
                tenant_id=tenant_id,
            )
            return build_device_power_config({})

        return build_device_power_config(device)

    async def _normalize_business_dataset(
        self,
        df: pd.DataFrame,
        *,
        device_id: str,
        tenant_id: Optional[str],
    ) -> pd.DataFrame:
        if df.empty:
            return df

        if "timestamp" not in df.columns and "_time" not in df.columns:
            return df

        power_config = await self._fetch_device_power_config(
            device_id=device_id,
            tenant_id=tenant_id,
        )

        out = df.copy()
        normalized_samples = [
            normalize_telemetry_sample(
                {
                    **row,
                    "timestamp": row.get("timestamp") or row.get("_time"),
                },
                power_config,
            )
            for row in out.to_dict(orient="records")
        ]

        out["power"] = [sample.business_power_w for sample in normalized_samples]
        out["current"] = [sample.current_a for sample in normalized_samples]
        out["voltage"] = [sample.voltage_v for sample in normalized_samples]
        out["power_factor"] = [sample.pf_business for sample in normalized_samples]
        out["power_direction"] = [sample.power_direction for sample in normalized_samples]
        out["quality_flags"] = [",".join(sample.quality_flags) for sample in normalized_samples]
        out["normalization_version"] = [sample.normalization_version for sample in normalized_samples]
        out["energy_flow_mode"] = power_config.energy_flow_mode
        out["polarity_mode"] = power_config.polarity_mode

        raw_alias_fields = [
            field
            for field in ("active_power", "active_power_kw", "power_kw")
            if field in out.columns
        ]
        if raw_alias_fields:
            out = out.drop(columns=raw_alias_fields)

        diagnostic_fields = [
            field
            for field in out.columns
            if str(field).strip().lower() in DIAGNOSTIC_PHASE_TELEMETRY_FIELDS
        ]
        if diagnostic_fields:
            out = out.drop(columns=diagnostic_fields)

        return out

    def _construct_s3_key(
        self,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        """Construct S3 key from parameters."""
        return (
            f"datasets/{device_id}/"
            f"{start_time.strftime('%Y%m%d')}_{end_time.strftime('%Y%m%d')}.parquet"
        )

    def construct_expected_s3_key(
        self,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        """Public helper for other services/routes needing the canonical key."""
        return self._construct_s3_key(device_id, start_time, end_time)

    async def list_available_datasets(
        self,
        device_id: str,
        prefix: Optional[str] = None,
    ) -> list:
        """List available datasets for a device."""
        if prefix is None:
            prefix = f"datasets/{device_id}/"

        return await self._s3.list_objects(prefix)

    async def get_best_available_dataset_key(
        self,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[str]:
        """Return the best available dataset key for a range when exact key is missing."""
        return await self._find_best_available_key(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
        )

    def dataset_key_covers_range(
        self,
        key: Optional[str],
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        if not key:
            return False
        parsed = self._parse_date_window_from_key(key)
        if not parsed:
            return False
        key_start, key_end = parsed
        return key_start <= start_time.date() and key_end >= end_time.date()

    async def _find_best_available_key(
        self,
        device_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[str]:
        """Pick best available dataset key when exact range key is missing."""
        candidates = await self.list_available_datasets(device_id=device_id)
        if not candidates:
            return None

        def score(item: dict) -> tuple:
            # lower score is better:
            # 1) covering requested range (0 preferred)
            # 2) absolute gap to requested end date (smaller preferred)
            # 3) newer file (later preferred)
            key = item.get("key", "")
            parsed = self._parse_date_window_from_key(key)
            if parsed is None:
                cover_penalty = 1
                gap_days = 10**9
            else:
                key_start, key_end = parsed
                covers = key_start <= start_time.date() and key_end >= end_time.date()
                cover_penalty = 0 if covers else 1
                gap_days = abs((key_end - end_time.date()).days)

            last_modified = item.get("last_modified", "")
            return (cover_penalty, gap_days, -self._safe_ts(last_modified))

        best = min(candidates, key=score)
        return best.get("key")

    @staticmethod
    def _parse_date_window_from_key(key: str):
        m = re.search(r"(\d{8})_(\d{8})\.parquet$", key)
        if not m:
            return None
        try:
            start = datetime.strptime(m.group(1), "%Y%m%d").date()
            end = datetime.strptime(m.group(2), "%Y%m%d").date()
            return start, end
        except Exception:
            return None

    @staticmethod
    def _safe_ts(value: str) -> float:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
