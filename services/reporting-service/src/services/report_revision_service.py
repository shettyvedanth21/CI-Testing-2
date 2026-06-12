from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.queue import ReportJob, get_report_queue
from src.repositories.report_repository import ReportRepository
from src.services.tariff_resolver import resolve_tariff
from src.services.tenant_scope import build_service_tenant_context


def _report_effective_at(params: dict | None, fallback: datetime | None) -> datetime | None:
    payload = params or {}
    for key in ("end_date", "period_a_end", "period_b_end"):
        raw = payload.get(key)
        if isinstance(raw, str):
            try:
                parsed = date.fromisoformat(raw)
                return datetime.combine(parsed, time.max)
            except Exception:
                continue
    return fallback


async def create_corrected_report_revision(
    *,
    db: AsyncSession,
    report_id: str,
    tenant_id: str,
    revision_reason: str,
    generated_from_reconciliation_run_id: str | None,
) -> dict:
    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    prior = await repo.get_report(report_id, tenant_id=tenant_id)
    if prior is None:
        raise ValueError("report not found")

    effective_at = _report_effective_at(prior.params or {}, prior.completed_at or prior.created_at)
    resolved_tariff = await resolve_tariff(db, tenant_id, effective_at=effective_at)
    new_report_id = str(uuid4())
    revision = await repo.create_revision_report(
        new_report_id=new_report_id,
        supersedes_report_id=report_id,
        revision_reason=revision_reason,
        tenant_id=tenant_id,
        params=dict(prior.params or {}),
        generated_from_reconciliation_run_id=generated_from_reconciliation_run_id,
        tariff_version_id=resolved_tariff.version_id,
    )
    await get_report_queue().enqueue(
        ReportJob(
            report_id=revision.report_id,
            tenant_id=tenant_id,
            report_type=prior.report_type.value if hasattr(prior.report_type, "value") else str(prior.report_type),
        )
    )
    return {
        "source_report_id": report_id,
        "new_report_id": revision.report_id,
        "status": "queued",
        "tariff_version_id": resolved_tariff.version_id,
    }
