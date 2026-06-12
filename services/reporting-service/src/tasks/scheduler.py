import asyncio
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
from src.database import AsyncSessionLocal
from src.queue import ReportJob, get_report_queue
from src.repositories.report_repository import ReportRepository
from src.repositories.scheduled_repository import ScheduledRepository
from src.services.retention import apply_report_retention
from src.tasks.notification_stub import notify_report_ready
from src.services.tenant_scope import build_service_tenant_context
from src.utils.downloads import build_report_download_path

logger = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None

FREQUENCY_OFFSETS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}

RETRY_INTERVAL = timedelta(minutes=30)
MAX_RETRIES = 3
PROCESSING_STALE_AFTER = timedelta(seconds=max(settings.REPORT_JOB_TIMEOUT_SECONDS + 300, 900))


def _status_value(status: object) -> str:
    return status.value if hasattr(status, "value") else str(status)


async def check_due_schedules() -> None:
    logger.info("Checking for due schedules...")
    
    async with AsyncSessionLocal() as db:
        scheduled_repo = ScheduledRepository(db)
        report_repo = ReportRepository(db)
        
        due_schedules = await scheduled_repo.claim_due_schedules(
            stale_after=PROCESSING_STALE_AFTER,
        )
        
        if not due_schedules:
            logger.info("No due schedules found")
            return
        
        logger.info(f"Found {len(due_schedules)} due schedules")
        
        for schedule in due_schedules:
            await process_schedule(schedule, scheduled_repo, report_repo)


async def process_schedule(
    schedule,
    scheduled_repo: ScheduledRepository,
    report_repo: ReportRepository
) -> None:
    schedule_id = schedule.schedule_id
    tenant_id = schedule.tenant_id
    tenant_ctx = build_service_tenant_context(tenant_id)
    frequency = schedule.frequency.value if hasattr(schedule.frequency, 'value') else str(schedule.frequency)
    report_type = schedule.report_type.value if hasattr(schedule.report_type, 'value') else str(schedule.report_type)
    params_template = schedule.params_template or {}
    
    logger.info(f"Processing schedule {schedule_id}, type={report_type}, freq={frequency}")
    
    await scheduled_repo.update_schedule(
        schedule_id,
        last_run_at=datetime.utcnow(),
    )
    
    try:
        params = build_params_from_template(params_template, frequency, tenant_id, schedule_id)
        
        report_id = str(uuid4())
        
        report_repo = ReportRepository(report_repo.db, ctx=tenant_ctx)
        await report_repo.create_report(
            report_id=report_id,
            tenant_id=tenant_id,
            report_type=report_type,
            params=params
        )
        
        logger.info(f"Created report {report_id} for schedule {schedule_id}")
        
        if report_type not in {"consumption", "comparison"}:
            logger.warning(f"Unsupported scheduled report type {report_type}, skipping {schedule_id}")
            await scheduled_repo.update_schedule(
                schedule_id,
                last_status="skipped",
                next_run_at=datetime.utcnow() + FREQUENCY_OFFSETS.get(frequency, timedelta(days=1)),
                retry_count=0,
                processing_started_at=None,
            )
            return

        await get_report_queue().enqueue(
            ReportJob(
                report_id=report_id,
                tenant_id=tenant_id,
                report_type=report_type,
            )
        )
        
        await wait_for_report_completion(report_id, tenant_id=tenant_id, max_wait=600)
        
        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db, ctx=tenant_ctx)
            report = await repo.get_report(report_id, tenant_id)
        
        if report and _status_value(report.status) == "completed" and report.s3_key:
            download_url = build_report_download_path(report_id)
            
            logger.info(
                f"SCHEDULED_REPORT_READY: schedule_id={schedule_id}, "
                f"report_id={report_id}, url={download_url}"
            )
            
            await notify_report_ready(
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                report_id=report_id,
                download_url=download_url,
                frequency=frequency
            )
            
            next_run = datetime.utcnow() + FREQUENCY_OFFSETS.get(frequency, timedelta(days=1))
            
            await scheduled_repo.update_schedule(
                schedule_id,
                last_status="completed",
                last_result_url=download_url,
                next_run_at=next_run,
                retry_count=0,
                processing_started_at=None,
            )
        else:
            raise Exception("Report did not complete successfully")
            
    except Exception as e:
        logger.error(f"Schedule {schedule_id} failed: {str(e)}")
        
        current_schedule = await scheduled_repo.get_schedule(schedule_id)
        current_retry_count = current_schedule.retry_count if current_schedule else schedule.retry_count
        new_retry_count = (current_retry_count or 0) + 1
        
        if new_retry_count >= MAX_RETRIES:
            logger.warning(f"SCHEDULED_REPORT_DISABLED: schedule_id={schedule_id} after 3 failures")
            
            await scheduled_repo.update_schedule(
                schedule_id,
                last_status="failed",
                is_active=False,
                processing_started_at=None,
                retry_count=new_retry_count,
            )
        else:
            next_retry = datetime.utcnow() + RETRY_INTERVAL
            
            await scheduled_repo.update_schedule(
                schedule_id,
                last_status="failed",
                retry_count=new_retry_count,
                next_run_at=next_retry,
                processing_started_at=None,
            )


def build_params_from_template(params_template: dict, frequency: str, tenant_id: str, schedule_id: str) -> dict:
    today = datetime.utcnow().date()
    
    if frequency == "daily":
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
    elif frequency == "weekly":
        start_date = today - timedelta(days=7)
        end_date = today - timedelta(days=1)
    else:
        start_date = today - timedelta(days=30)
        end_date = today - timedelta(days=1)
    
    params = {
        "tenant_id": tenant_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "scheduled_report_id": schedule_id,
        "trigger_source": "scheduler",
    }
    
    if "device_ids" in params_template:
        params["device_ids"] = params_template["device_ids"]
    
    if "group_by" in params_template:
        params["group_by"] = params_template["group_by"]
    
    return params


async def wait_for_report_completion(report_id: str, tenant_id: str, max_wait: int = 300) -> None:
    poll_interval = 2
    attempts = max(1, max_wait // poll_interval)
    tenant_ctx = build_service_tenant_context(tenant_id)
    for _ in range(attempts):
        await asyncio.sleep(2)
        
        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db, ctx=tenant_ctx)
            report = await repo.get_report(report_id, tenant_id)
            
            if report and _status_value(report.status) in {"completed", "failed"}:
                return
    
    raise Exception(f"Report {report_id} did not complete within timeout")


async def run_report_retention() -> None:
    if not settings.REPORT_RETENTION_ENABLED:
        return
    await apply_report_retention()


def start_scheduler() -> AsyncIOScheduler:
    global scheduler
    
    if scheduler is not None:
        return scheduler
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_due_schedules,
        trigger=IntervalTrigger(minutes=5),
        id="check_due_schedules",
        name="Check due scheduled reports",
        replace_existing=True
    )
    if settings.REPORT_RETENTION_ENABLED:
        scheduler.add_job(
            run_report_retention,
            trigger=IntervalTrigger(seconds=max(300, int(settings.REPORT_RETENTION_INTERVAL_SECONDS))),
            id="report_retention",
            name="Apply report retention",
            replace_existing=True,
        )
    
    logger.info("APScheduler initialized with check_due_schedules job (every 5 minutes)")
    
    return scheduler


def stop_scheduler() -> None:
    global scheduler
    
    if scheduler:
        scheduler.shutdown()
        scheduler = None
        logger.info("APScheduler stopped")
