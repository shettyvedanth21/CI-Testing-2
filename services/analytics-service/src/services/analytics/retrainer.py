"""Weekly retrainer that submits jobs through the existing queue."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

import structlog
from sqlalchemy import func, select

from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.models.database import AnalyticsJob
from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
from services.shared.job_context import BoundJobPayload

logger = structlog.get_logger()

DEVICE_SERVICE_URL = os.getenv(
    "DEVICE_SERVICE_URL",
    "http://device-service:8000/api/v1/devices",
)
RETRAINER_SOURCE = "weekly_retrainer"


class WeeklyRetrainer:
    def __init__(self, job_queue, dataset_service):
        self._job_queue = job_queue
        self._dataset_service = dataset_service
        self._status: dict = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_device_ids: List[str] = []
        self._last_run_completed_at: Optional[datetime] = None
        self._next_run: Optional[datetime] = None

    async def start(self, device_ids: List[str]) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._last_device_ids = list(device_ids)
        self._last_run_completed_at = await self._load_last_run_completed_at()
        now = datetime.now(timezone.utc)
        if self._last_run_completed_at is not None:
            self._next_run = self._last_run_completed_at + timedelta(days=7)
        else:
            self._next_run = now + timedelta(hours=1)
        self._task = asyncio.create_task(self._loop(), name="weekly-retrainer")
        logger.info(
            "weekly_retrainer_started",
            next_run=self._next_run.isoformat() if self._next_run else None,
            last_run_completed_at=(
                self._last_run_completed_at.isoformat() if self._last_run_completed_at else None
            ),
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            now = datetime.now(timezone.utc)
            if self._next_run is not None and now >= self._next_run:
                device_ids = await self._fetch_device_ids()
                await self._retrain_all(device_ids)
                cycle_completed_at = datetime.now(timezone.utc)
                self._last_run_completed_at = cycle_completed_at
                self._next_run = cycle_completed_at + timedelta(days=7)
                logger.info(
                    "weekly_retrainer_cycle_completed",
                    completed_at=cycle_completed_at.isoformat(),
                    next_run=self._next_run.isoformat(),
                    device_count=len(device_ids),
                )
            await asyncio.sleep(60)

    async def _load_last_run_completed_at(self) -> Optional[datetime]:
        async with async_session_maker() as session:
            result = await session.execute(
                select(AnalyticsJob.completed_at)
                .where(AnalyticsJob.status == JobStatus.COMPLETED.value)
                .where(
                    func.json_unquote(
                        func.json_extract(AnalyticsJob.parameters, "$.retrainer_source")
                    )
                    == RETRAINER_SOURCE
                )
                .order_by(AnalyticsJob.completed_at.desc())
                .limit(1)
            )
            last_completed = result.scalar_one_or_none()
            if last_completed is None:
                return None
            if last_completed.tzinfo is None:
                return last_completed.replace(tzinfo=timezone.utc)
            return last_completed.astimezone(timezone.utc)

    async def _fetch_device_ids(self) -> List[str]:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    DEVICE_SERVICE_URL,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        devices = (
                            data
                            if isinstance(data, list)
                            else data.get("devices", data.get("data", []))
                        )
                        ids = [
                            d.get("id") or d.get("device_id")
                            for d in devices
                            if d.get("id") or d.get("device_id")
                        ]
                        if ids:
                            self._last_device_ids = ids
                            logger.info("retrainer_fetched_devices", count=len(ids))
                        return ids
        except Exception as e:
            logger.warning(
                "retrainer_device_fetch_failed",
                error=str(e),
                fallback_count=len(self._last_device_ids),
            )
        return self._last_device_ids

    async def _retrain_all(self, device_ids: List[str]) -> None:
        if not device_ids:
            logger.warning("retrainer_no_devices_found")
            return

        for index, device_id in enumerate(device_ids):
            if index > 0:
                await asyncio.sleep(0.5)
            await self._retrain_device(device_id)

    async def _retrain_device(self, device_id: str) -> None:
        try:
            datasets = await self._dataset_service.list_available_datasets(device_id)
            if not datasets:
                logger.warning("retrain_skipped_no_data", device_id=device_id)
                self._status[device_id] = {
                    "status": "skipped",
                    "reason": "no_datasets",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                return

            dataset_key = datasets[0].get("key") if isinstance(datasets[0], dict) else None
            if not dataset_key:
                logger.warning("retrain_skipped_invalid_dataset", device_id=device_id)
                self._status[device_id] = {
                    "status": "skipped",
                    "reason": "invalid_dataset",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                return

            prediction_type = getattr(AnalyticsType, "FAILURE_PREDICTION", AnalyticsType.PREDICTION)
            submissions = [
                (AnalyticsType.ANOMALY, "isolation_forest"),
                (prediction_type, "random_forest"),
            ]
            now = datetime.now(timezone.utc)
            job_records: list[dict[str, str]] = []

            async with async_session_maker() as session:
                repo = MySQLResultRepository(session)
                for analysis_type, model_name in submissions:
                    job_id = str(uuid4())
                    request = AnalyticsRequest(
                        device_id=device_id,
                        analysis_type=analysis_type,
                        model_name=model_name,
                        parameters={
                            "sensitivity": "medium",
                            "lookback_days": 30,
                            "retrainer_source": RETRAINER_SOURCE,
                        },
                        dataset_key=dataset_key,
                    )
                    await repo.create_job(
                        job_id=job_id,
                        device_id=device_id,
                        analysis_type=analysis_type.value,
                        model_name=model_name,
                        date_range_start=now,
                        date_range_end=now,
                        parameters=request.parameters,
                        job_kind="single",
                    )
                    await repo.update_job_queue_metadata(
                        job_id=job_id,
                        attempt=1,
                        queue_enqueued_at=now,
                        queue_dispatched_at=now,
                    )
                    payload = BoundJobPayload(
                        job_type="weekly_retrainer",
                        tenant_id=None,
                        device_id=device_id,
                        initiated_by_user_id="weekly_retrainer",
                        initiated_by_role="super_admin",
                        payload=request.model_dump(mode="json"),
                    )
                    payload.validate()
                    await self._job_queue.submit_job(
                        job_id=job_id,
                        raw_payload=json.dumps(payload.__dict__, separators=(",", ":"), sort_keys=True, default=str),
                        attempt=1,
                    )
                    job_records.append(
                        {
                            "job_id": job_id,
                            "analysis_type": analysis_type.value,
                            "model_name": model_name,
                        }
                    )
                    logger.info(
                        "retrain_job_submitted",
                        device_id=device_id,
                        job_id=job_id,
                        analysis_type=analysis_type.value,
                        model_name=model_name,
                    )

            self._status[device_id] = {
                "status": "submitted",
                "jobs": job_records,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("retrain_failed", device_id=device_id, error=str(e))
            self._status[device_id] = {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_status(self) -> dict:
        return {
            "devices": self._status,
            "last_run_completed_at": (
                self._last_run_completed_at.isoformat() if self._last_run_completed_at else None
            ),
            "next_run": self._next_run.isoformat() if self._next_run else None,
        }
