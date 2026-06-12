"""Worker-side notification execution service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule
from app.notifications.adapter import NotificationAdapter
from app.repositories.rule import RuleRepository
from app.services.notification_delivery import NotificationDeliveryAuditService
from services.shared.tenant_context import TenantContext


class NotificationExecutor:
    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._audit_service = NotificationDeliveryAuditService(session, ctx)
        self._adapter = NotificationAdapter(audit_service=self._audit_service)
        self._rule_repo = RuleRepository(session, ctx)

    async def execute_outbox_delivery(
        self,
        *,
        channel: str,
        rule_id: str | None,
        device_id: str,
        subject: str,
        message: str,
        recipient: str,
        alert_id: str | None,
        event_type: str,
        ledger_log_id: str | None,
        payload_json: dict[str, Any],
    ):
        rule = await self._load_rule(rule_id)
        return await self._adapter.dispatch_alert(
            channel=channel,
            subject=subject,
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=event_type,
            alert_id=alert_id,
            alert_context=dict(payload_json.get("alert_context") or {}),
            resolved_recipients=[recipient] if recipient else [],
            resolution_metadata=dict(payload_json.get("resolution_metadata") or {}),
            attempt_log_ids={recipient: ledger_log_id} if recipient and ledger_log_id else {},
            defer_failure_finalization=True,
        )

    async def _load_rule(self, rule_id: str | None) -> Rule:
        if not rule_id:
            raise ValueError("Outbox delivery requires rule_id")
        rule = await self._rule_repo.get_by_id(rule_id)
        if rule is None:
            raise ValueError(f"Rule {rule_id} not found for notification execution")
        return rule
