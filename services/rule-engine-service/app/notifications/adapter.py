"""Notification adapter layer for multi-channel alerting."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from email.utils import formatdate, make_msgid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.models.rule import NotificationDeliveryStatus, Rule
from app.repositories.notification_settings import NotificationSettingsRepository
from app.services.notification_delivery import NotificationDeliveryAuditService
from app.shared_http import get_twilio_http_client
from app.utils.recipients import normalize_phone_recipient
from app.utils.timezone import format_platform_datetime, platform_tz_label

logger = logging.getLogger(__name__)


@dataclass
class NotificationRecipientResult:
    recipient: str
    channel: str
    provider_name: str
    status: str
    attempted_at: datetime
    provider_message_id: Optional[str] = None
    accepted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    billable_units: int = 0
    audit_log_id: Optional[str] = None
    metadata_json: Optional[dict[str, Any]] = None


@dataclass
class NotificationDispatchResult:
    channel: str
    provider_name: str
    recipient_results: list[NotificationRecipientResult] = field(default_factory=list)
    forced_success: Optional[bool] = None

    @property
    def overall_success(self) -> bool:
        if self.forced_success is not None:
            return self.forced_success
        return bool(self.recipient_results) and all(
            result.status in {NotificationDeliveryStatus.PROVIDER_ACCEPTED.value, NotificationDeliveryStatus.DELIVERED.value}
            for result in self.recipient_results
        )


class NotificationChannel(ABC):
    """Abstract base class for notification channels."""

    channel_name: str
    provider_name: str

    @abstractmethod
    async def resolve_recipients(self, rule: Rule) -> tuple[list[str], dict[str, int | str]]:
        """Resolve recipients for a rule without sending notifications."""

    @abstractmethod
    async def dispatch_alert(
        self,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        **kwargs: Any,
    ) -> NotificationDispatchResult:
        """Dispatch a notification and return per-recipient delivery outcomes."""

    async def send(
        self,
        message: str,
        rule: Rule,
        device_id: str,
        **kwargs: Any,
    ) -> bool:
        result = await self.dispatch_alert(
            subject=f"Alert: {rule.rule_name}",
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=kwargs.pop("alert_type", "threshold_alert"),
            alert_id=kwargs.pop("alert_id", None),
            **kwargs,
        )
        return result.overall_success

    async def send_alert(
        self,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        result = await self.dispatch_alert(
            subject=subject,
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=alert_type,
            alert_id=alert_id,
            **kwargs,
        )
        return result.overall_success

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if channel is healthy and available."""


class EmailAdapter(NotificationChannel):
    """Email notification adapter with SMTP support."""

    channel_name = "email"
    provider_name = "smtp"

    def __init__(self, audit_service: Optional[NotificationDeliveryAuditService] = None):
        self._audit_service = audit_service
        self._enabled = settings.EMAIL_ENABLED
        self._smtp_host = settings.EMAIL_SMTP_HOST
        self._smtp_port = settings.EMAIL_SMTP_PORT
        self._smtp_username = settings.EMAIL_SMTP_USERNAME
        self._smtp_password = settings.EMAIL_SMTP_PASSWORD
        self._from_address = settings.EMAIL_FROM_ADDRESS

    @staticmethod
    def _direct_rule_recipients(rule: Rule) -> list[str]:
        recipients: list[str] = []
        for row in list(getattr(rule, "notification_recipients", []) or []):
            if not isinstance(row, dict):
                continue
            channel = str(row.get("channel") or "").strip().lower()
            value = str(row.get("value") or "").strip().lower()
            if channel != "email" or not value:
                continue
            recipients.append(value)
        return sorted(set(recipients))

    async def _settings_recipients_for_rule(self, rule: Rule) -> list[str]:
        channels = {str(channel).strip().lower() for channel in list(rule.notification_channels or [])}
        if "email" not in channels or self._audit_service is None:
            return []
        repository = NotificationSettingsRepository(self._audit_service._session, self._audit_service._ctx)
        return sorted({recipient.strip().lower() for recipient in await repository.list_active_channel_values("email")})

    async def _resolve_recipients(self, rule: Rule) -> tuple[list[str], dict[str, int | str]]:
        direct_recipients = self._direct_rule_recipients(rule)
        settings_recipients = await self._settings_recipients_for_rule(rule)
        resolved = sorted(set(direct_recipients) | set(settings_recipients))
        return resolved, {
            "resolution_strategy": "merge_rule_and_tenant_settings",
            "direct_rule_recipient_count": len(direct_recipients),
            "tenant_settings_recipient_count": len(settings_recipients),
            "resolved_recipient_count": len(resolved),
        }

    async def resolve_recipients(self, rule: Rule) -> tuple[list[str], dict[str, int | str]]:
        return await self._resolve_recipients(rule)

    async def dispatch_alert(
        self,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        device_names: Optional[str] = None,
        alert_context: Optional[dict[str, Any]] = None,
        scope_label: Optional[str] = None,
        resolved_recipients: Optional[list[str]] = None,
        resolution_metadata: Optional[dict[str, int | str]] = None,
        attempt_log_ids: Optional[dict[str, Optional[str]]] = None,
        defer_failure_finalization: bool = False,
        **kwargs: Any,
    ) -> NotificationDispatchResult:
        if resolved_recipients is None or resolution_metadata is None:
            recipients, resolution_metadata = await self._resolve_recipients(rule)
        else:
            recipients = sorted(set(resolved_recipients))
        result = NotificationDispatchResult(channel=self.channel_name, provider_name=self.provider_name)
        metadata = {
            "subject": subject,
            "alert_type": alert_type,
            "device_id": device_id,
            **resolution_metadata,
        }
        if not recipients:
            await self._record_no_recipient_skip(
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                result=result,
                failure_code="NO_ACTIVE_RECIPIENTS",
                failure_message="No active recipients configured for email channel.",
            )
            logger.warning(
                "Email notification skipped because no active recipients were resolved",
                extra={"channel": "email", "rule_id": str(rule.rule_id) if rule.rule_id else None},
            )
            return result

        if not self._enabled:
            await self._record_skipped_batch(
                recipients=recipients,
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                failure_code="channel_disabled",
                failure_message="Email notifications are disabled.",
                result=result,
                attempt_log_ids=attempt_log_ids,
            )
            logger.info("Email notifications disabled", extra={"channel": "email"})
            result.forced_success = True
            return result

        if not self._smtp_username or not self._smtp_password:
            await self._record_skipped_batch(
                recipients=recipients,
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                failure_code="missing_configuration",
                failure_message="Email SMTP credentials are missing.",
                result=result,
                attempt_log_ids=attempt_log_ids,
            )
            logger.warning("Email not configured - SMTP credentials missing", extra={"channel": "email"})
            result.forced_success = False
            return result

        attempt_logs = attempt_log_ids or await self._create_attempt_logs(
            recipients=recipients,
            rule=rule,
            device_id=device_id,
            event_type=alert_type,
            alert_id=alert_id,
            metadata=metadata,
        )
        await self._mark_attempt_logs(attempt_logs=attempt_logs, metadata=metadata)

        messages: dict[str, str] = {}
        for recipient in recipients:
            msg = self._build_message(
                recipient=recipient,
                subject=subject,
                plain_message=message,
                rule=rule,
                device_id=device_id,
                alert_type=alert_type,
                device_names=device_names,
                alert_context=alert_context,
                scope_label=scope_label,
            )
            messages[recipient] = msg.as_string()

        try:
            outcomes = await asyncio.to_thread(
                self._smtp_send_sync,
                self._smtp_host,
                self._smtp_port,
                self._smtp_username,
                self._smtp_password,
                self._from_address,
                recipients,
                messages,
            )
        except Exception as exc:
            logger.error(
                "Failed to send email",
                extra={
                    "channel": "email",
                    "error": str(exc),
                    "rule_id": str(rule.rule_id) if rule.rule_id else None,
                    "device_id": device_id,
                },
            )
            for recipient in recipients:
                result.recipient_results.append(
                    await self._record_failed_result(
                        recipient=recipient,
                        attempt_log_id=attempt_logs.get(recipient),
                        failure_code=exc.__class__.__name__,
                        failure_message=str(exc),
                        metadata=metadata,
                        finalize=not defer_failure_finalization,
                    )
                )
            return result

        for recipient in recipients:
            success, failure_code, failure_message = outcomes.get(
                recipient, (False, "SMTP_SEND_ERROR", "No outcome recorded")
            )
            if success:
                result.recipient_results.append(
                    await self._record_provider_accepted_result(
                        recipient=recipient,
                        attempt_log_id=attempt_logs.get(recipient),
                        metadata=metadata,
                    )
                )
            else:
                result.recipient_results.append(
                    await self._record_failed_result(
                        recipient=recipient,
                        attempt_log_id=attempt_logs.get(recipient),
                        failure_code=failure_code,
                        failure_message=failure_message,
                        metadata=metadata,
                        finalize=not defer_failure_finalization,
                    )
                )

        logger.info(
            "Email sent",
            extra={
                "channel": "email",
                "to": recipients,
                "subject": subject,
                "rule_id": str(rule.rule_id) if rule.rule_id else None,
                "device_id": device_id,
                "alert_type": alert_type,
            },
        )
        return result

    def _build_message(
        self,
        *,
        recipient: str,
        subject: str,
        plain_message: str,
        rule: Rule,
        device_id: str,
        alert_type: str,
        device_names: Optional[str],
        alert_context: Optional[dict[str, Any]] = None,
        scope_label: Optional[str] = None,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from_address
        msg["To"] = recipient
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self._message_id_domain())

        if alert_type == "rule_created":
            html_content = self._format_rule_created_message(
                rule,
                device_id,
                plain_message,
                device_names,
                scope_label=scope_label,
            )
            plain_text_content = plain_message
        else:
            html_content = self._format_alert_message(rule, device_id, plain_message, alert_context)
            plain_text_content = self._format_alert_plain_text(rule, device_id, alert_context)

        msg.attach(MIMEText(plain_text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        return msg

    def _message_id_domain(self) -> Optional[str]:
        from_address = (self._from_address or "").strip()
        if "@" not in from_address:
            return None
        return from_address.split("@", 1)[1].strip().lower() or None

    @staticmethod
    def _normalize_smtp_refusal(refusal: Any) -> tuple[str, str]:
        if isinstance(refusal, tuple) and len(refusal) >= 2:
            code = str(refusal[0])
            message = refusal[1]
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            return code, str(message)
        return "smtp_refused", str(refusal)

    def _format_alert_message(
        self,
        rule: Rule,
        device_id: str,
        message: str,
        alert_context: Optional[dict[str, Any]] = None,
    ) -> str:
        context = self._build_alert_email_context(rule, device_id, alert_context)
        return f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #dc3545; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background: #f8f9fa; }}
        .summary {{ margin: 0 0 14px 0; color: #475569; font-size: 14px; }}
        .alert-box {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin: 10px 0; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td {{ padding: 8px 4px; vertical-align: top; border-bottom: 1px solid #f1f5f9; }}
        td.label {{ width: 38%; font-weight: bold; color: #334155; }}
        td.value {{ color: #0f172a; }}
        .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>Energy Alert</h2>
        </div>
        <div class="content">
            <p class="summary">A rule has triggered for one of your monitored devices. Details are below.</p>
            <div class="alert-box">
                <table>
                    <tr><td class="label">Rule Name</td><td class="value">{context["rule_name"]}</td></tr>
                    <tr><td class="label">Device Name</td><td class="value">{context["device_name"]}</td></tr>
                    <tr><td class="label">Device ID</td><td class="value">{context["device_id"]}</td></tr>
                    <tr><td class="label">Device Location</td><td class="value">{context["device_location"]}</td></tr>
                    <tr><td class="label">Property</td><td class="value">{context["property"]}</td></tr>
                    <tr><td class="label">Condition</td><td class="value">{context["condition"]}</td></tr>
                    <tr><td class="label">Actual Value</td><td class="value">{context["actual_value"]}</td></tr>
                    <tr><td class="label">Triggered Time</td><td class="value">{context["triggered_at"]}</td></tr>
                </table>
            </div>
        </div>
        <div class="footer">
            <p>This is an automated alert from Energy Platform.</p>
        </div>
    </div>
</body>
</html>
"""

    def _format_alert_plain_text(
        self,
        rule: Rule,
        device_id: str,
        alert_context: Optional[dict[str, Any]] = None,
    ) -> str:
        context = self._build_alert_email_context(rule, device_id, alert_context, escape_values=False)
        return "\n".join(
            [
                "Energy Alert",
                f"Rule Name: {context['rule_name']}",
                f"Device Name: {context['device_name']}",
                f"Device ID: {context['device_id']}",
                f"Device Location: {context['device_location']}",
                f"Property: {context['property']}",
                f"Condition: {context['condition']}",
                f"Actual Value: {context['actual_value']}",
                f"Triggered Time: {context['triggered_at']}",
                "",
                "This is an automated alert from Energy Platform.",
            ]
        )

    def _format_rule_created_message(
        self,
        rule: Rule,
        device_id: str,
        message: str,
        device_names: str | None = None,
        scope_label: str | None = None,
    ) -> str:
        status_value = rule.status.value if hasattr(rule.status, "value") else str(rule.status)
        condition_text, property_text = self._describe_rule(rule)
        normalized_scope = str(rule.scope.value) if hasattr(rule.scope, "value") else str(rule.scope or "")
        resolved_scope = scope_label or self._humanize_rule_scope(normalized_scope)

        if device_names and device_names.strip():
            devices_display = device_names.strip()
        elif normalized_scope == "all_devices":
            devices_display = "All accessible machines"
        elif device_id and device_id.strip():
            devices_display = device_id.strip()
        else:
            devices_display = "Not specified"

        return f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #28a745; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background: #f8f9fa; }}
        .info-box {{ background: white; border-left: 4px solid #28a745; padding: 15px; margin: 10px 0; }}
        .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        .label {{ font-weight: bold; color: #555; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>✅ Rule Created Successfully</h2>
        </div>
        <div class="content">
            <p>A new monitoring rule has been created in the Energy Platform.</p>
            <div class="info-box">
                <p><span class="label">Rule Name:</span> {rule.rule_name}</p>
                <p><span class="label">Rule ID:</span> {rule.rule_id}</p>
                <p><span class="label">Scope:</span> {resolved_scope}</p>
                <p><span class="label">Devices:</span> {devices_display}</p>
                <p><span class="label">Status:</span> {status_value}</p>
                <p><span class="label">Property:</span> {property_text}</p>
                <p><span class="label">Condition:</span> {condition_text}</p>
                <p><span class="label">Notification Channels:</span> {', '.join(rule.notification_channels) if rule.notification_channels else 'None'}</p>
                <p><span class="label">Created:</span> {format_platform_datetime(rule.created_at) if rule.created_at else format_platform_datetime(datetime.now(timezone.utc))}</p>
            </div>
            <p>{message}</p>
        </div>
        <div class="footer">
            <p>This is a confirmation email from Energy Platform</p>
        </div>
    </div>
</body>
</html>
"""

    @staticmethod
    def _describe_rule(rule: Rule) -> tuple[str, str]:
        if rule.rule_type == "time_based":
            return (
                f"running between {rule.time_window_start}-{rule.time_window_end} {platform_tz_label()}",
                "power status",
            )
        if rule.rule_type == "continuous_idle_duration":
            return (
                f"idle continuously for {rule.duration_minutes} minute(s)",
                "idle duration",
            )
        return (f"{rule.condition} {rule.threshold}", rule.property)

    @staticmethod
    def _humanize_rule_scope(scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if normalized == "all_devices":
            return "All Machines"
        if normalized == "selected_devices":
            return "Selected Machines"
        return "Scoped Machines"

    def _build_alert_email_context(
        self,
        rule: Rule,
        device_id: str,
        alert_context: Optional[dict[str, Any]] = None,
        *,
        escape_values: bool = True,
    ) -> dict[str, str]:
        context = alert_context or {}
        default_condition, default_property = self._describe_rule(rule)
        normalized = {
            "rule_name": str(context.get("rule_name") or rule.rule_name or "Unnamed Rule"),
            "device_name": str(context.get("device_name") or device_id or "Unknown Device"),
            "device_id": str(context.get("device_id") or device_id or "Unknown Device"),
            "device_location": str(context.get("device_location") or "Not specified"),
            "property": str(context.get("property") or default_property or "Value"),
            "condition": str(context.get("condition") or default_condition or "Condition met"),
            "actual_value": str(context.get("actual_value") or "N/A"),
            "triggered_at": str(context.get("triggered_at") or format_platform_datetime(datetime.now(timezone.utc))),
        }
        if not escape_values:
            return normalized
        return {key: escape(value) for key, value in normalized.items()}

    async def _record_skipped_batch(
        self,
        *,
        recipients: list[str],
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
        failure_code: str,
        failure_message: str,
        result: NotificationDispatchResult,
        attempt_log_ids: Optional[dict[str, Optional[str]]] = None,
    ) -> None:
        for recipient in recipients:
            audit_log_id = None
            existing_log_id = (attempt_log_ids or {}).get(recipient)
            if self._audit_service is not None and existing_log_id is not None:
                row = await self._audit_service.mark_skipped_log(
                    existing_log_id,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    metadata_json=metadata,
                )
                audit_log_id = row.id if row is not None else existing_log_id
            elif self._audit_service is not None:
                row = await self._audit_service.mark_skipped(
                    channel=self.channel_name,
                    raw_recipient=recipient,
                    provider_name=self.provider_name,
                    event_type=event_type,
                    rule_id=str(rule.rule_id) if rule.rule_id else None,
                    alert_id=alert_id,
                    device_id=device_id,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    metadata_json=metadata,
                )
                audit_log_id = row.id
            result.recipient_results.append(
                NotificationRecipientResult(
                    recipient=recipient,
                    channel=self.channel_name,
                    provider_name=self.provider_name,
                    status=NotificationDeliveryStatus.SKIPPED.value,
                    attempted_at=datetime.now(timezone.utc),
                    failure_code=failure_code,
                    failure_message=failure_message,
                    audit_log_id=audit_log_id,
                    metadata_json=metadata,
                )
            )

    async def _record_no_recipient_skip(
        self,
        *,
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
        failure_code: str,
        failure_message: str,
        result: NotificationDispatchResult,
    ) -> None:
        audit_log_id = None
        if self._audit_service is not None:
            row = await self._audit_service.mark_skipped(
                channel=self.channel_name,
                raw_recipient="",
                provider_name=self.provider_name,
                event_type=event_type,
                rule_id=str(rule.rule_id) if rule.rule_id else None,
                alert_id=alert_id,
                device_id=device_id,
                failure_code=failure_code,
                failure_message=failure_message,
                metadata_json=metadata,
            )
            audit_log_id = row.id
        result.recipient_results.append(
            NotificationRecipientResult(
                recipient="",
                channel=self.channel_name,
                provider_name=self.provider_name,
                status=NotificationDeliveryStatus.SKIPPED.value,
                attempted_at=datetime.now(timezone.utc),
                failure_code=failure_code,
                failure_message=failure_message,
                audit_log_id=audit_log_id,
                metadata_json=metadata,
            )
        )

    async def _create_attempt_logs(
        self,
        *,
        recipients: list[str],
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
    ) -> dict[str, Optional[str]]:
        if self._audit_service is None:
            return {recipient: None for recipient in recipients}
        attempt_logs: dict[str, Optional[str]] = {}
        for recipient in recipients:
            row = await self._audit_service.create_send_attempt(
                channel=self.channel_name,
                raw_recipient=recipient,
                provider_name=self.provider_name,
                event_type=event_type,
                rule_id=str(rule.rule_id) if rule.rule_id else None,
                alert_id=alert_id,
                device_id=device_id,
                metadata_json=metadata,
            )
            attempt_logs[recipient] = row.id
        return attempt_logs

    async def _record_provider_accepted_result(
        self,
        *,
        recipient: str,
        attempt_log_id: Optional[str],
        metadata: dict[str, Any],
        provider_message_id: Optional[str] = None,
    ) -> NotificationRecipientResult:
        accepted_at = datetime.now(timezone.utc)
        if self._audit_service is not None and attempt_log_id is not None:
            await self._audit_service.mark_provider_accepted(
                attempt_log_id,
                provider_message_id=provider_message_id,
                accepted_at=accepted_at,
                metadata_json=metadata,
            )
        return NotificationRecipientResult(
            recipient=recipient,
            channel=self.channel_name,
            provider_name=self.provider_name,
            status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
            attempted_at=accepted_at,
            accepted_at=accepted_at,
            provider_message_id=provider_message_id,
            billable_units=1,
            audit_log_id=attempt_log_id,
            metadata_json=metadata,
        )

    async def _mark_attempt_logs(
        self,
        *,
        attempt_logs: dict[str, Optional[str]],
        metadata: dict[str, Any],
    ) -> None:
        if self._audit_service is None:
            return
        for attempt_log_id in attempt_logs.values():
            if attempt_log_id is None:
                continue
            await self._audit_service.mark_attempted(
                attempt_log_id,
                attempted_at=datetime.now(timezone.utc),
                metadata_json=metadata,
            )

    async def _record_failed_result(
        self,
        *,
        recipient: str,
        attempt_log_id: Optional[str],
        failure_code: Optional[str],
        failure_message: Optional[str],
        metadata: dict[str, Any],
        finalize: bool = True,
    ) -> NotificationRecipientResult:
        failed_at = datetime.now(timezone.utc)
        if finalize and self._audit_service is not None and attempt_log_id is not None:
            await self._audit_service.mark_failed(
                attempt_log_id,
                failure_code=failure_code,
                failure_message=failure_message,
                failed_at=failed_at,
                metadata_json=metadata,
            )
        return NotificationRecipientResult(
            recipient=recipient,
            channel=self.channel_name,
            provider_name=self.provider_name,
            status=NotificationDeliveryStatus.FAILED.value,
            attempted_at=failed_at,
            failed_at=failed_at,
            failure_code=failure_code,
            failure_message=failure_message,
            audit_log_id=attempt_log_id,
            metadata_json=metadata,
        )

    @staticmethod
    def _smtp_send_sync(
        smtp_host: str,
        smtp_port: int,
        smtp_username: str | None,
        smtp_password: str | None,
        from_address: str,
        recipients: list[str],
        messages: dict[str, str],
    ) -> dict[str, tuple[bool, str | None, str | None]]:
        context = ssl.create_default_context()
        outcomes: dict[str, tuple[bool, str | None, str | None]] = {}
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_username, smtp_password)
            for recipient in recipients:
                msg_str = messages[recipient]
                refused = server.sendmail(from_address, [recipient], msg_str) or {}
                refusal = refused.get(recipient)
                if refusal is not None:
                    code, detail = EmailAdapter._normalize_smtp_refusal(refusal)
                    outcomes[recipient] = (False, code, detail)
                else:
                    outcomes[recipient] = (True, None, None)
        return outcomes

    @staticmethod
    def _smtp_health_check_sync(
        smtp_host: str, smtp_port: int, smtp_username: str | None, smtp_password: str | None
    ) -> bool:
        try:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
            return True
        except Exception:
            return False

    async def health_check(self) -> bool:
        if not self._enabled:
            return False
        return await asyncio.to_thread(
            self._smtp_health_check_sync,
            self._smtp_host,
            self._smtp_port,
            self._smtp_username,
            self._smtp_password,
        )


class _TwilioChannelAdapter(NotificationChannel, ABC):
    """Twilio-backed notification adapter for SMS and WhatsApp."""

    provider_name = "twilio"

    def __init__(
        self,
        *,
        channel_name: str,
        enabled: bool,
        account_sid: Optional[str],
        auth_token: Optional[str],
        from_number: Optional[str],
        prefix: str = "",
        audit_service: Optional[NotificationDeliveryAuditService] = None,
    ):
        self.channel_name = channel_name
        self._audit_service = audit_service
        self._enabled = enabled
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._prefix = prefix

    @staticmethod
    def _recipients_for_rule(rule: Rule, channel: str) -> list[str]:
        recipients: list[str] = []
        for row in list(getattr(rule, "notification_recipients", []) or []):
            if not isinstance(row, dict):
                continue
            row_channel = str(row.get("channel") or "").strip().lower()
            value = str(row.get("value") or "").strip()
            if row_channel != channel or not value:
                continue
            if channel == "sms":
                recipients.append(normalize_phone_recipient(value))
            elif channel == "whatsapp":
                normalized = normalize_phone_recipient(value)
                recipients.append(normalized if normalized.startswith("whatsapp:") else f"whatsapp:{normalized}")
        return sorted(set(recipients))

    def _configured(self) -> bool:
        return bool(self._account_sid and self._auth_token and self._from_number)

    async def resolve_recipients(self, rule: Rule) -> tuple[list[str], dict[str, int | str]]:
        recipients = self._recipients_for_rule(rule, self.channel_name)
        return recipients, {
            "resolution_strategy": "rule_recipients_only",
            "direct_rule_recipient_count": len(recipients),
            "resolved_recipient_count": len(recipients),
        }

    @staticmethod
    def _compact_whitespace(value: str) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _shorten(cls, value: str, limit: int) -> str:
        cleaned = cls._compact_whitespace(value)
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)].rstrip() + "…"

    @classmethod
    def _build_alert_text_context(
        cls,
        *,
        rule: Rule,
        device_id: str,
        alert_context: Optional[dict[str, Any]],
    ) -> dict[str, str]:
        context = alert_context or {}
        property_text = str(context.get("property_label") or context.get("property") or (rule.property or "Value"))
        condition_text = str(context.get("condition_label") or context.get("condition") or "Condition met")
        actual_value = str(context.get("actual_value") or "N/A")
        triggered_at = str(context.get("triggered_at") or format_platform_datetime(datetime.now(timezone.utc)))
        device_name = str(context.get("device_name") or device_id)
        device_location = str(context.get("device_location") or "Not specified")
        return {
            "rule_name": str(context.get("rule_name") or rule.rule_name or "Unnamed Rule"),
            "device_name": device_name,
            "device_id": str(context.get("device_id") or device_id),
            "device_location": device_location,
            "property": property_text,
            "condition": condition_text,
            "actual_value": actual_value,
            "triggered_at": triggered_at,
        }

    @classmethod
    def _format_sms_alert_message(cls, context: dict[str, str], max_len: int = 320) -> str:
        # Priority order is deterministic: preserve core business fields first.
        device_line = (
            f"Device: {context['device_name']} ({context['device_id']})"
            if context["device_name"] != context["device_id"]
            else f"Device: {context['device_id']}"
        )
        lines = [
            "Energy Alert",
            f"Rule: {context['rule_name']}",
            device_line,
            f"Location: {context['device_location']}",
            f"Condition: {context['condition']}",
            f"Actual: {context['actual_value']}",
            f"Time: {context['triggered_at']}",
        ]

        def _join(parts: list[str]) -> str:
            return "\n".join(parts)

        # Step 1: omit location when unavailable or when needed for size.
        if context["device_location"].strip().lower() == "not specified":
            lines = [line for line in lines if not line.startswith("Location:")]
        if len(_join(lines)) > max_len:
            lines = [line for line in lines if not line.startswith("Location:")]
        # Step 2: drop header if still long.
        if len(_join(lines)) > max_len and lines and lines[0] == "Energy Alert":
            lines = lines[1:]

        # Step 3: deterministic field shortening.
        for idx, prefix, limit in (
            (0 if lines and lines[0].startswith("Rule: ") else -1, "Rule: ", 48),
            (1 if len(lines) > 1 and lines[1].startswith("Device: ") else -1, "Device: ", 60),
        ):
            if idx >= 0:
                raw_value = lines[idx][len(prefix) :]
                lines[idx] = prefix + cls._shorten(raw_value, limit)

        for i, line in enumerate(lines):
            if line.startswith("Condition: "):
                lines[i] = "Condition: " + cls._shorten(line[len("Condition: ") :], 72)
            elif line.startswith("Actual: "):
                lines[i] = "Actual: " + cls._shorten(line[len("Actual: ") :], 40)
            elif line.startswith("Time: "):
                lines[i] = "Time: " + cls._shorten(line[len("Time: ") :], 42)

        msg = _join(lines)
        if len(msg) <= max_len:
            return msg
        # Step 4: compact to mandatory lines with tighter value limits.
        compact_lines = [
            f"Rule: {cls._shorten(context['rule_name'], 24)}",
            (
                f"Device: {cls._shorten(context['device_name'], 20)} ({cls._shorten(context['device_id'], 16)})"
                if context["device_name"] != context["device_id"]
                else f"Device: {cls._shorten(context['device_id'], 24)}"
            ),
            f"Condition: {cls._shorten(context['condition'], 28)}",
            f"Actual: {cls._shorten(context['actual_value'], 16)}",
            f"Time: {cls._shorten(context['triggered_at'], 20)}",
        ]
        msg = _join(compact_lines)
        if len(msg) <= max_len:
            return msg
        return msg[: max_len - 1].rstrip() + "…"

    @classmethod
    def _format_whatsapp_alert_message(cls, context: dict[str, str]) -> str:
        return "\n".join(
            [
                "Energy Alert",
                f"Rule Name: {context['rule_name']}",
                f"Device Name: {context['device_name']}",
                f"Device ID: {context['device_id']}",
                f"Device Location: {context['device_location']}",
                f"Property: {context['property']}",
                f"Condition: {context['condition']}",
                f"Actual Value: {context['actual_value']}",
                f"Triggered Time: {context['triggered_at']}",
            ]
        )

    def _format_channel_alert_body(
        self,
        *,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str,
        alert_context: Optional[dict[str, Any]],
    ) -> str:
        if alert_type != "threshold_alert":
            return f"{self._prefix}{message}".strip()
        context = self._build_alert_text_context(rule=rule, device_id=device_id, alert_context=alert_context)
        if self.channel_name == "sms":
            return self._format_sms_alert_message(context)
        if self.channel_name == "whatsapp":
            return self._format_whatsapp_alert_message(context)
        return f"{self._prefix}{message}".strip()

    async def dispatch_alert(
        self,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        alert_context: Optional[dict[str, Any]] = None,
        resolved_recipients: Optional[list[str]] = None,
        resolution_metadata: Optional[dict[str, int | str]] = None,
        attempt_log_ids: Optional[dict[str, Optional[str]]] = None,
        defer_failure_finalization: bool = False,
        **kwargs: Any,
    ) -> NotificationDispatchResult:
        if resolved_recipients is None or resolution_metadata is None:
            recipients, resolution_metadata = await self.resolve_recipients(rule)
        else:
            recipients = sorted(set(resolved_recipients))
        result = NotificationDispatchResult(channel=self.channel_name, provider_name=self.provider_name)
        metadata = {
            "subject": subject,
            "alert_type": alert_type,
            "device_id": device_id,
            **resolution_metadata,
        }
        if not recipients:
            await self._record_no_recipient_skip(
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                result=result,
                failure_code="NO_ACTIVE_RECIPIENTS",
                failure_message=f"No active recipients configured for {self.channel_name} channel.",
            )
            logger.warning(
                "%s notification skipped because no active recipients were resolved",
                self.channel_name.capitalize(),
                extra={"channel": self.channel_name, "rule_id": str(rule.rule_id) if rule.rule_id else None},
            )
            return result

        if not self._enabled:
            await self._record_skipped_batch(
                recipients=recipients,
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                failure_code="channel_disabled",
                failure_message=f"{self.channel_name} notifications are disabled.",
                result=result,
                attempt_log_ids=attempt_log_ids,
            )
            logger.info(
                "%s notifications disabled or not configured",
                self.channel_name.capitalize(),
                extra={"channel": self.channel_name},
            )
            result.forced_success = True
            return result

        if not self._configured():
            await self._record_skipped_batch(
                recipients=recipients,
                rule=rule,
                device_id=device_id,
                event_type=alert_type,
                alert_id=alert_id,
                metadata=metadata,
                failure_code="missing_configuration",
                failure_message=f"{self.channel_name} provider credentials are missing.",
                result=result,
                attempt_log_ids=attempt_log_ids,
            )
            logger.warning(
                "%s notifications enabled but not configured",
                self.channel_name.capitalize(),
                extra={"channel": self.channel_name},
            )
            result.forced_success = False
            return result

        body = self._format_channel_alert_body(
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=alert_type,
            alert_context=alert_context,
        )
        client = get_twilio_http_client()
        try:
            for recipient in recipients:
                if attempt_log_ids and recipient in attempt_log_ids:
                    attempt_log_id = attempt_log_ids[recipient]
                else:
                    attempt_log_id = await self._create_attempt_log(
                        recipient=recipient,
                        rule=rule,
                        device_id=device_id,
                        event_type=alert_type,
                        alert_id=alert_id,
                        metadata=metadata,
                    )
                if self._audit_service is not None and attempt_log_id is not None:
                    await self._audit_service.mark_attempted(
                        attempt_log_id,
                        attempted_at=datetime.now(timezone.utc),
                        metadata_json=metadata,
                    )
                to_value = recipient
                from_value = self._from_number or ""
                if self.channel_name == "whatsapp" and not from_value.startswith("whatsapp:"):
                    from_value = f"whatsapp:{from_value}"
                payload = {"From": from_value, "To": to_value, "Body": body}
                try:
                    response = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json",
                        data=payload,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                except Exception as exc:
                    result.recipient_results.append(
                        await self._record_failed_result(
                            recipient=recipient,
                            attempt_log_id=attempt_log_id,
                            failure_code=exc.__class__.__name__,
                            failure_message=str(exc),
                            metadata=metadata,
                            finalize=not defer_failure_finalization,
                        )
                    )
                    continue

                if response.status_code >= 400:
                    failure_code, failure_message = self._extract_twilio_failure(response)
                    logger.error(
                        "%s notification send failed",
                        self.channel_name.capitalize(),
                        extra={
                            "channel": self.channel_name,
                            "status_code": response.status_code,
                            "rule_id": str(rule.rule_id) if rule.rule_id else None,
                            "device_id": device_id,
                            "recipient": recipient,
                        },
                    )
                    result.recipient_results.append(
                        await self._record_failed_result(
                            recipient=recipient,
                            attempt_log_id=attempt_log_id,
                            failure_code=failure_code,
                            failure_message=failure_message,
                            metadata=metadata,
                            finalize=not defer_failure_finalization,
                        )
                    )
                    continue

                provider_message_id = self._extract_twilio_sid(response)
                result.recipient_results.append(
                    await self._record_provider_accepted_result(
                        recipient=recipient,
                        attempt_log_id=attempt_log_id,
                        metadata=metadata,
                        provider_message_id=provider_message_id,
                    )
                )

            logger.info(
                "%s sent",
                self.channel_name.capitalize(),
                extra={
                    "channel": self.channel_name,
                    "to": recipients,
                    "rule_id": str(rule.rule_id) if rule.rule_id else None,
                    "device_id": device_id,
                    "alert_type": alert_type,
                },
            )
            return result
        except Exception as exc:
            logger.error(
                "Failed to send %s notification",
                self.channel_name,
                extra={
                    "channel": self.channel_name,
                    "error": str(exc),
                    "rule_id": str(rule.rule_id) if rule.rule_id else None,
                    "device_id": device_id,
                },
            )
            if not result.recipient_results:
                for recipient in recipients:
                    result.recipient_results.append(
                        NotificationRecipientResult(
                            recipient=recipient,
                            channel=self.channel_name,
                            provider_name=self.provider_name,
                            status=NotificationDeliveryStatus.FAILED.value,
                            attempted_at=datetime.now(timezone.utc),
                            failed_at=datetime.now(timezone.utc),
                            failure_code=exc.__class__.__name__,
                            failure_message=str(exc),
                            metadata_json=metadata,
                        )
                    )
            return result

    @staticmethod
    def _extract_twilio_sid(response: Any) -> Optional[str]:
        json_loader = getattr(response, "json", None)
        if callable(json_loader):
            try:
                payload = json_loader()
                if isinstance(payload, dict):
                    return payload.get("sid")
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_twilio_failure(response: Any) -> tuple[str, str]:
        code = str(getattr(response, "status_code", "provider_error"))
        json_loader = getattr(response, "json", None)
        if callable(json_loader):
            try:
                payload = json_loader()
                if isinstance(payload, dict):
                    return str(payload.get("code") or code), str(payload.get("message") or payload)
            except Exception:
                pass
        text = getattr(response, "text", None)
        return code, str(text or "Provider rejected notification.")

    async def _record_skipped_batch(
        self,
        *,
        recipients: list[str],
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
        failure_code: str,
        failure_message: str,
        result: NotificationDispatchResult,
        attempt_log_ids: Optional[dict[str, Optional[str]]] = None,
    ) -> None:
        for recipient in recipients:
            audit_log_id = None
            existing_log_id = (attempt_log_ids or {}).get(recipient)
            if self._audit_service is not None and existing_log_id is not None:
                row = await self._audit_service.mark_skipped_log(
                    existing_log_id,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    metadata_json=metadata,
                )
                audit_log_id = row.id if row is not None else existing_log_id
            elif self._audit_service is not None:
                row = await self._audit_service.mark_skipped(
                    channel=self.channel_name,
                    raw_recipient=recipient,
                    provider_name=self.provider_name,
                    event_type=event_type,
                    rule_id=str(rule.rule_id) if rule.rule_id else None,
                    alert_id=alert_id,
                    device_id=device_id,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    metadata_json=metadata,
                )
                audit_log_id = row.id
            result.recipient_results.append(
                NotificationRecipientResult(
                    recipient=recipient,
                    channel=self.channel_name,
                    provider_name=self.provider_name,
                    status=NotificationDeliveryStatus.SKIPPED.value,
                    attempted_at=datetime.now(timezone.utc),
                    failure_code=failure_code,
                    failure_message=failure_message,
                    audit_log_id=audit_log_id,
                    metadata_json=metadata,
                )
            )

    async def _record_no_recipient_skip(
        self,
        *,
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
        failure_code: str,
        failure_message: str,
        result: NotificationDispatchResult,
    ) -> None:
        audit_log_id = None
        if self._audit_service is not None:
            row = await self._audit_service.mark_skipped(
                channel=self.channel_name,
                raw_recipient="",
                provider_name=self.provider_name,
                event_type=event_type,
                rule_id=str(rule.rule_id) if rule.rule_id else None,
                alert_id=alert_id,
                device_id=device_id,
                failure_code=failure_code,
                failure_message=failure_message,
                metadata_json=metadata,
            )
            audit_log_id = row.id
        result.recipient_results.append(
            NotificationRecipientResult(
                recipient="",
                channel=self.channel_name,
                provider_name=self.provider_name,
                status=NotificationDeliveryStatus.SKIPPED.value,
                attempted_at=datetime.now(timezone.utc),
                failure_code=failure_code,
                failure_message=failure_message,
                audit_log_id=audit_log_id,
                metadata_json=metadata,
            )
        )

    async def _create_attempt_log(
        self,
        *,
        recipient: str,
        rule: Rule,
        device_id: str,
        event_type: str,
        alert_id: Optional[str],
        metadata: dict[str, Any],
    ) -> Optional[str]:
        if self._audit_service is None:
            return None
        row = await self._audit_service.create_send_attempt(
            channel=self.channel_name,
            raw_recipient=recipient,
            provider_name=self.provider_name,
            event_type=event_type,
            rule_id=str(rule.rule_id) if rule.rule_id else None,
            alert_id=alert_id,
            device_id=device_id,
            metadata_json=metadata,
        )
        return row.id

    async def _record_provider_accepted_result(
        self,
        *,
        recipient: str,
        attempt_log_id: Optional[str],
        metadata: dict[str, Any],
        provider_message_id: Optional[str] = None,
    ) -> NotificationRecipientResult:
        accepted_at = datetime.now(timezone.utc)
        if self._audit_service is not None and attempt_log_id is not None:
            await self._audit_service.mark_provider_accepted(
                attempt_log_id,
                provider_message_id=provider_message_id,
                accepted_at=accepted_at,
                metadata_json=metadata,
            )
        return NotificationRecipientResult(
            recipient=recipient,
            channel=self.channel_name,
            provider_name=self.provider_name,
            status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
            attempted_at=accepted_at,
            accepted_at=accepted_at,
            provider_message_id=provider_message_id,
            billable_units=1,
            audit_log_id=attempt_log_id,
            metadata_json=metadata,
        )

    async def _record_failed_result(
        self,
        *,
        recipient: str,
        attempt_log_id: Optional[str],
        failure_code: Optional[str],
        failure_message: Optional[str],
        metadata: dict[str, Any],
        finalize: bool = True,
    ) -> NotificationRecipientResult:
        failed_at = datetime.now(timezone.utc)
        if finalize and self._audit_service is not None and attempt_log_id is not None:
            await self._audit_service.mark_failed(
                attempt_log_id,
                failure_code=failure_code,
                failure_message=failure_message,
                failed_at=failed_at,
                metadata_json=metadata,
            )
        return NotificationRecipientResult(
            recipient=recipient,
            channel=self.channel_name,
            provider_name=self.provider_name,
            status=NotificationDeliveryStatus.FAILED.value,
            attempted_at=failed_at,
            failed_at=failed_at,
            failure_code=failure_code,
            failure_message=failure_message,
            audit_log_id=attempt_log_id,
            metadata_json=metadata,
        )

    async def health_check(self) -> bool:
        return bool(self._enabled and self._configured())


class SmsAdapter(_TwilioChannelAdapter):
    def __init__(self, audit_service: Optional[NotificationDeliveryAuditService] = None):
        super().__init__(
            channel_name="sms",
            enabled=settings.SMS_ENABLED,
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN,
            from_number=settings.TWILIO_SMS_FROM_NUMBER,
            audit_service=audit_service,
        )


class WhatsAppAdapter(_TwilioChannelAdapter):
    def __init__(self, audit_service: Optional[NotificationDeliveryAuditService] = None):
        super().__init__(
            channel_name="whatsapp",
            enabled=settings.WHATSAPP_ENABLED,
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN,
            from_number=settings.TWILIO_WHATSAPP_FROM_NUMBER,
            prefix="WhatsApp: ",
            audit_service=audit_service,
        )


class NotificationAdapter:
    """Main notification adapter that routes to appropriate channels."""

    def __init__(self, audit_service: Optional[NotificationDeliveryAuditService] = None):
        self._adapters: Dict[str, NotificationChannel] = {
            "email": EmailAdapter(audit_service=audit_service),
            "sms": SmsAdapter(audit_service=audit_service),
            "whatsapp": WhatsAppAdapter(audit_service=audit_service),
        }

    async def dispatch(
        self,
        channel: str,
        message: str,
        rule: Rule,
        device_id: str,
        **kwargs: Any,
    ) -> NotificationDispatchResult:
        if channel not in self._adapters:
            raise ValueError(f"Unsupported notification channel: {channel}")
        adapter = self._adapters[channel]
        return await adapter.dispatch_alert(
            subject=f"Alert: {rule.rule_name}",
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=kwargs.pop("alert_type", "threshold_alert"),
            alert_id=kwargs.pop("alert_id", None),
            **kwargs,
        )

    async def resolve_recipients(
        self,
        channel: str,
        rule: Rule,
    ) -> tuple[list[str], dict[str, int | str]]:
        if channel not in self._adapters:
            raise ValueError(f"Unsupported notification channel: {channel}")
        adapter = self._adapters[channel]
        return await adapter.resolve_recipients(rule)

    def provider_name_for(self, channel: str) -> str:
        if channel not in self._adapters:
            raise ValueError(f"Unsupported notification channel: {channel}")
        return self._adapters[channel].provider_name

    async def send(
        self,
        channel: str,
        message: str,
        rule: Rule,
        device_id: str,
        **kwargs: Any,
    ) -> bool:
        result = await self.dispatch(channel=channel, message=message, rule=rule, device_id=device_id, **kwargs)
        return result.overall_success

    async def dispatch_alert(
        self,
        channel: str,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        **kwargs: Any,
    ) -> NotificationDispatchResult:
        if channel not in self._adapters:
            raise ValueError(f"Unsupported notification channel: {channel}")
        adapter = self._adapters[channel]
        return await adapter.dispatch_alert(
            subject=subject,
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=alert_type,
            alert_id=alert_id,
            **kwargs,
        )

    async def send_alert(
        self,
        channel: str,
        subject: str,
        message: str,
        rule: Rule,
        device_id: str,
        alert_type: str = "threshold_alert",
        alert_id: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        result = await self.dispatch_alert(
            channel=channel,
            subject=subject,
            message=message,
            rule=rule,
            device_id=device_id,
            alert_type=alert_type,
            alert_id=alert_id,
            **kwargs,
        )
        return result.overall_success

    async def health_check(self) -> Dict[str, bool]:
        results = {}
        for channel_name, adapter in self._adapters.items():
            try:
                results[channel_name] = await adapter.health_check()
            except Exception as exc:
                logger.error("Health check failed for %s", channel_name, extra={"error": str(exc)})
                results[channel_name] = False
        return results

    def get_supported_channels(self) -> list[str]:
        return list(self._adapters.keys())


notification_adapter = NotificationAdapter()
