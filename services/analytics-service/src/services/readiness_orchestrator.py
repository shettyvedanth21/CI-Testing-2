"""Shared exact-range readiness orchestration for analytics jobs."""

from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import aiohttp
import structlog
from redis.asyncio import Redis

from src.config.settings import get_settings
from src.infrastructure.s3_client import S3Client
from src.services.dataset_service import DatasetService
from services.shared.tenant_context import build_tenant_scoped_internal_headers

logger = structlog.get_logger()

_readiness_semaphore: Optional[asyncio.Semaphore] = None
_redis_client: Optional[Redis] = None
_local_export_suppression: Dict[str, float] = {}
_suppression_lock = asyncio.Lock()


def _get_readiness_semaphore() -> asyncio.Semaphore:
    global _readiness_semaphore
    if _readiness_semaphore is None:
        settings = get_settings()
        cpu = max(1, int(os.cpu_count() or 1))
        safe_upper = max(2, cpu * 4)
        effective = max(1, min(int(settings.data_readiness_max_concurrency), safe_upper))
        _readiness_semaphore = asyncio.Semaphore(effective)
        logger.info(
            "data_readiness_concurrency_resolved",
            configured=int(settings.data_readiness_max_concurrency),
            effective=effective,
            cpu_count=cpu,
        )
    return _readiness_semaphore


def _suppression_key(device_id: str, start_time: datetime, end_time: datetime) -> str:
    return (
        f"analytics:readiness:export:{device_id}:"
        f"{start_time.strftime('%Y%m%d%H%M')}:{end_time.strftime('%Y%m%d%H%M')}"
    )


async def _get_redis() -> Optional[Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    settings = get_settings()
    try:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await _redis_client.ping()
        return _redis_client
    except Exception:
        _redis_client = None
        return None


async def _mark_export_triggered_once(suppression_key: str, cooldown_seconds: int) -> Tuple[bool, str]:
    redis_client = await _get_redis()
    if redis_client:
        try:
            ok = await redis_client.set(
                suppression_key,
                datetime.now(timezone.utc).isoformat(),
                ex=max(1, cooldown_seconds),
                nx=True,
            )
            return bool(ok), "redis"
        except Exception:
            pass

    now_ts = datetime.now(timezone.utc).timestamp()
    async with _suppression_lock:
        expires = _local_export_suppression.get(suppression_key, 0.0)
        if expires > now_ts:
            return False, "memory"
        _local_export_suppression[suppression_key] = now_ts + max(1, cooldown_seconds)
        return True, "memory"


async def _trigger_export_with_retries(
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    tenant_id: str,
) -> Tuple[Optional[dict], Optional[str]]:
    settings = get_settings()
    url = f"{settings.data_export_service_url}/api/v1/exports/run"
    try:
        headers = build_tenant_scoped_internal_headers("analytics-service", tenant_id)
    except ValueError:
        return None, "tenant_scope_required"
    payload = {
        "device_id": device_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }
    retries = max(1, int(settings.data_readiness_trigger_retries))
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    body_text = await resp.text()
                    if resp.status >= 400:
                        msg = body_text.lower()
                        if "device" in msg and "not found" in msg:
                            return None, "device_not_found"
                        if "no telemetry" in msg or "no data" in msg:
                            return None, "no_telemetry_in_range"
                        if attempt == retries:
                            return None, "export_trigger_failed"
                        await asyncio.sleep(min(2.0 * attempt, 6.0) + random.uniform(0.0, 0.35))
                        continue
                    try:
                        return (await resp.json()), None
                    except Exception:
                        return {"status": "accepted"}, None
        except Exception:
            if attempt == retries:
                return None, "export_trigger_failed"
            await asyncio.sleep(min(2.0 * attempt, 6.0) + random.uniform(0.0, 0.35))
    return None, "export_trigger_failed"


def _reason_from_status_payload(status_payload: dict) -> Optional[str]:
    export_status = str(status_payload.get("status", "")).lower()
    if export_status != "failed":
        return None
    msg = str(status_payload.get("error_message") or status_payload.get("message") or "").lower()
    if "device" in msg and "not found" in msg:
        return "device_not_found"
    if "no telemetry" in msg or "no data" in msg:
        return "no_telemetry_in_range"
    return "export_failed"


def dataset_window_from_key(dataset_key: Optional[str]) -> Optional[Dict[str, str]]:
    if not dataset_key:
        return None
    match = re.search(r"(\d{8})_(\d{8})", dataset_key)
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=timezone.utc)
    end = end.replace(hour=23, minute=59, second=59)
    return {"start_time": start.isoformat(), "end_time": end.isoformat()}


async def wait_for_dataset_key(
    device_id: str,
    expected_key: str,
    data_export_service_url: str,
    s3_client: S3Client,
    tenant_id: str,
    timeout_seconds_override: Optional[int] = None,
) -> Tuple[Optional[str], str, float]:
    settings = get_settings()
    try:
        headers = build_tenant_scoped_internal_headers("analytics-service", tenant_id)
    except ValueError:
        return None, "tenant_scope_required", 0.0
    delay = max(1, int(settings.data_readiness_initial_delay_seconds))
    timeout_seconds = max(
        delay,
        int(timeout_seconds_override if timeout_seconds_override is not None else settings.data_readiness_wait_timeout_seconds),
    )
    max_attempts = max(int(timeout_seconds / delay) + 1, int(settings.data_readiness_poll_attempts))

    start = datetime.now(timezone.utc)
    for _ in range(max_attempts):
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        if elapsed > timeout_seconds:
            return None, "export_timeout", round(elapsed, 2)

        if await s3_client.object_exists(expected_key):
            return expected_key, "ready_exact", round(elapsed, 2)

        status_retries = max(1, int(settings.data_readiness_status_retries))
        for attempt in range(1, status_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{data_export_service_url}/api/v1/exports/status/{device_id}",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            if attempt < status_retries:
                                await asyncio.sleep(0.3 + random.uniform(0.0, 0.2))
                                continue
                            break
                        status_payload = await resp.json()
                        reason = _reason_from_status_payload(status_payload)
                        if reason:
                            return None, reason, round(elapsed, 2)
                        s3_key = status_payload.get("s3_key")
                        if isinstance(s3_key, str) and s3_key:
                            if await s3_client.object_exists(s3_key):
                                return s3_key, "ready_export_status", round(elapsed, 2)
                        break
            except Exception:
                if attempt < status_retries:
                    await asyncio.sleep(0.3 + random.uniform(0.0, 0.2))
                    continue
                break

        await asyncio.sleep(delay)

    if await s3_client.object_exists(expected_key):
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return expected_key, "ready_exact_late", round(elapsed, 2)
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    return None, "export_timeout", round(elapsed, 2)


async def ensure_device_ready(
    s3_client: S3Client,
    dataset_service: DatasetService,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    tenant_id: Optional[str] = None,
) -> Tuple[str, Optional[str], Dict[str, object]]:
    settings = get_settings()
    expected_key = dataset_service.construct_expected_s3_key(device_id, start_time, end_time)
    strict_range = bool(settings.ml_require_exact_dataset_range and settings.app_env.lower() != "test")
    cooldown = max(5, int(settings.data_readiness_export_cooldown_seconds))

    logger.info(
        "data_readiness_check_started",
        device_id=device_id,
        expected_key=expected_key,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
    )

    async with _get_readiness_semaphore():
        if await s3_client.object_exists(expected_key):
            return device_id, expected_key, {
                "ready": True,
                "reason": "exact_key_present",
                "export_attempted": False,
                "export_suppressed": False,
                "wait_seconds": 0.0,
            }

        fallback_key = await dataset_service.get_best_available_dataset_key(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
        )
        if fallback_key and ((not strict_range) or dataset_service.dataset_key_covers_range(fallback_key, start_time, end_time)):
            return device_id, fallback_key, {
                "ready": True,
                "reason": "fallback_dataset",
                "export_attempted": False,
                "export_suppressed": False,
                "wait_seconds": 0.0,
            }

        should_attempt_export = bool(settings.ml_data_readiness_gate_enabled or strict_range)
        if not should_attempt_export:
            return device_id, None, {
                "ready": False,
                "reason": "dataset_not_ready",
                "export_attempted": False,
                "export_suppressed": False,
                "wait_seconds": 0.0,
            }
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            return device_id, None, {
                "ready": False,
                "reason": "tenant_scope_required",
                "export_attempted": False,
                "export_suppressed": False,
                "wait_seconds": 0.0,
            }

        key = _suppression_key(device_id, start_time, end_time)
        should_trigger, suppression_backend = await _mark_export_triggered_once(key, cooldown)

        if should_trigger:
            export_response, export_error = await _trigger_export_with_retries(
                device_id,
                start_time,
                end_time,
                normalized_tenant_id,
            )
            if export_error in {"device_not_found", "no_telemetry_in_range"}:
                return device_id, None, {
                    "ready": False,
                    "reason": export_error,
                    "export_attempted": True,
                    "export_suppressed": False,
                    "suppression_backend": suppression_backend,
                    "wait_seconds": 0.0,
                }
            if export_error:
                logger.warning(
                    "data_readiness_export_trigger_non_terminal_error",
                    device_id=device_id,
                    expected_key=expected_key,
                    reason=export_error,
                )
            logger.info(
                "data_readiness_export_triggered",
                device_id=device_id,
                expected_key=expected_key,
                export_response=export_response or {},
            )
        else:
            logger.info(
                "data_readiness_export_suppressed",
                device_id=device_id,
                expected_key=expected_key,
                suppression_backend=suppression_backend,
            )

        resolved_key, wait_reason, wait_seconds = await wait_for_dataset_key(
            device_id=device_id,
            expected_key=expected_key,
            data_export_service_url=settings.data_export_service_url,
            s3_client=s3_client,
            tenant_id=normalized_tenant_id,
        )
        if not resolved_key and wait_reason == "export_timeout":
            # One additional bounded grace cycle for slow export completion.
            # Keep it deterministic and non-hammering (single re-trigger + extended wait).
            retry_export_response, retry_export_error = await _trigger_export_with_retries(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                tenant_id=normalized_tenant_id,
            )
            if retry_export_error in {"device_not_found", "no_telemetry_in_range"}:
                return device_id, None, {
                    "ready": False,
                    "reason": retry_export_error,
                    "export_attempted": True,
                    "export_suppressed": False,
                    "suppression_backend": suppression_backend,
                    "wait_seconds": wait_seconds,
                }
            if retry_export_error:
                logger.warning(
                    "data_readiness_export_retrigger_non_terminal_error",
                    device_id=device_id,
                    expected_key=expected_key,
                    reason=retry_export_error,
                )
            else:
                logger.info(
                    "data_readiness_export_retriggered",
                    device_id=device_id,
                    expected_key=expected_key,
                    export_response=retry_export_response or {},
                )

            extended_timeout = max(
                int(settings.data_readiness_wait_timeout_seconds),
                int(settings.data_readiness_extended_wait_timeout_seconds),
            )
            resolved_key, wait_reason, extra_wait_seconds = await wait_for_dataset_key(
                device_id=device_id,
                expected_key=expected_key,
                data_export_service_url=settings.data_export_service_url,
                s3_client=s3_client,
                tenant_id=normalized_tenant_id,
                timeout_seconds_override=extended_timeout,
            )
            wait_seconds = round(float(wait_seconds) + float(extra_wait_seconds), 2)

        if resolved_key:
            return device_id, resolved_key, {
                "ready": True,
                "reason": wait_reason,
                "export_attempted": should_trigger,
                "export_suppressed": not should_trigger,
                "export_trigger_error": export_error if should_trigger else None,
                "suppression_backend": suppression_backend,
                "wait_seconds": wait_seconds,
            }
        return device_id, None, {
            "ready": False,
            "reason": wait_reason,
            "export_attempted": should_trigger,
            "export_suppressed": not should_trigger,
            "export_trigger_error": export_error if should_trigger else None,
            "suppression_backend": suppression_backend,
            "wait_seconds": wait_seconds,
        }
