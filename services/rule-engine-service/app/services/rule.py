"""Rule service layer - business logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Any
from uuid import UUID
import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import (
    Rule,
    RuleStatus,
    RuleScope,
    Alert,
    RuleType,
    CooldownMode,
)
from app.repositories.rule import RuleRepository, AlertRepository, ActivityEventRepository
from app.schemas.rule import (
    RuleCreate,
    RuleUpdate,
    RuleStatus as RuleStatusEnum,
    RuleType as RuleTypeSchema,
)
from app.utils.timezone import platform_tz_label
from app.utils.cooldown import normalize_cooldown_values
from services.shared.tenant_context import TenantContext

logger = logging.getLogger(__name__)


CHANNEL_ENTITLEMENT_KEYS: dict[str, str] = {
    "sms": "notification_sms",
    "whatsapp": "notification_whatsapp",
}


@dataclass(frozen=True)
class RuleDuplicateSignature:
    """Normalized active-rule signature used for server-side duplicate protection."""

    rule_name: str
    description: Optional[str]
    scope: str
    device_ids: tuple[str, ...]
    property: Optional[str]
    condition: Optional[str]
    threshold: Optional[float]
    rule_type: str
    cooldown_mode: str
    cooldown_unit: Optional[str]
    cooldown_minutes: int
    cooldown_seconds: int
    time_window_start: Optional[str]
    time_window_end: Optional[str]
    timezone: Optional[str]
    time_condition: Optional[str]
    duration_minutes: Optional[int]
    notification_channels: tuple[str, ...]
    notification_recipients: tuple[tuple[str, str], ...]


class DuplicateRuleError(ValueError):
    """Raised when an identical active rule already exists for the tenant."""

    def __init__(self, existing_rule_id: str, rule_name: str):
        self.existing_rule_id = existing_rule_id
        super().__init__(f"An identical active rule already exists for '{rule_name}'.")


class RuleService:
    """Service layer for rule management business logic."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._repository = RuleRepository(session, ctx)
        self._activity_service = ActivityEventService(session, ctx)

    @staticmethod
    def _ordered_unique_device_ids(device_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for device_id in device_ids:
            if device_id not in seen:
                seen.add(device_id)
                ordered.append(device_id)
        return ordered

    @staticmethod
    def _normalize_notification_recipients(
        recipients: list[dict],
        notification_channels: list[str],
    ) -> list[dict[str, str]]:
        allowed_channels = set(notification_channels)
        seen: set[tuple[str, str]] = set()
        normalized: list[dict[str, str]] = []
        for recipient in recipients:
            raw_channel = recipient.get("channel")
            channel = getattr(raw_channel, "value", raw_channel)
            channel = str(channel or "").strip()
            value = str(recipient.get("value") or "").strip()
            if not channel or not value:
                raise ValueError("Each notification recipient must include channel and value.")
            if channel not in allowed_channels:
                raise ValueError(
                    f"Recipient target channel '{channel}' must also be enabled in notification_channels."
                )
            key = (channel, value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"channel": channel, "value": value})
        return normalized

    @staticmethod
    def _normalize_optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        return float(value)

    @classmethod
    def _build_duplicate_signature(
        cls,
        *,
        rule_name: str,
        description: Optional[str],
        scope: str,
        device_ids: list[str],
        property: Optional[str],
        condition: Optional[str],
        threshold: Optional[float],
        rule_type: str,
        cooldown_mode: str,
        cooldown_unit: Optional[str],
        cooldown_minutes: Optional[int],
        cooldown_seconds: Optional[int],
        time_window_start: Optional[str],
        time_window_end: Optional[str],
        timezone: Optional[str],
        time_condition: Optional[str],
        duration_minutes: Optional[int],
        notification_channels: list[str],
        notification_recipients: list[dict[str, str]],
    ) -> RuleDuplicateSignature:
        normalized_recipients = sorted(
            (
                str(row.get("channel") or "").strip(),
                str(row.get("value") or "").strip().lower(),
            )
            for row in notification_recipients
            if str(row.get("channel") or "").strip() and str(row.get("value") or "").strip()
        )
        return RuleDuplicateSignature(
            rule_name=str(rule_name).strip(),
            description=cls._normalize_optional_text(description),
            scope=str(scope).strip(),
            device_ids=tuple(cls._ordered_unique_device_ids(list(device_ids or []))),
            property=cls._normalize_optional_text(property),
            condition=cls._normalize_optional_text(condition),
            threshold=cls._normalize_optional_float(threshold),
            rule_type=str(rule_type).strip(),
            cooldown_mode=str(cooldown_mode).strip(),
            cooldown_unit=cls._normalize_optional_text(cooldown_unit),
            cooldown_minutes=int(cooldown_minutes or 0),
            cooldown_seconds=int(cooldown_seconds or 0),
            time_window_start=cls._normalize_optional_text(time_window_start),
            time_window_end=cls._normalize_optional_text(time_window_end),
            timezone=cls._normalize_optional_text(timezone),
            time_condition=cls._normalize_optional_text(time_condition),
            duration_minutes=None if duration_minutes is None else int(duration_minutes),
            notification_channels=tuple(sorted(str(channel).strip() for channel in notification_channels if str(channel).strip())),
            notification_recipients=tuple(normalized_recipients),
        )

    @classmethod
    def _build_duplicate_signature_for_rule(cls, rule: Rule) -> RuleDuplicateSignature:
        return cls._build_duplicate_signature(
            rule_name=rule.rule_name,
            description=rule.description,
            scope=str(rule.scope.value) if hasattr(rule.scope, "value") else str(rule.scope),
            device_ids=list(rule.device_ids or []),
            property=rule.property,
            condition=str(rule.condition.value) if hasattr(rule.condition, "value") else rule.condition,
            threshold=rule.threshold,
            rule_type=str(rule.rule_type.value) if hasattr(rule.rule_type, "value") else str(rule.rule_type),
            cooldown_mode=str(rule.cooldown_mode.value) if hasattr(rule.cooldown_mode, "value") else str(rule.cooldown_mode),
            cooldown_unit=str(rule.cooldown_unit.value) if hasattr(rule.cooldown_unit, "value") else rule.cooldown_unit,
            cooldown_minutes=rule.cooldown_minutes,
            cooldown_seconds=rule.cooldown_seconds,
            time_window_start=rule.time_window_start,
            time_window_end=rule.time_window_end,
            timezone=rule.timezone,
            time_condition=str(rule.time_condition.value) if hasattr(rule.time_condition, "value") else rule.time_condition,
            duration_minutes=rule.duration_minutes,
            notification_channels=list(rule.notification_channels or []),
            notification_recipients=list(rule.notification_recipients or []),
        )

    async def _ensure_no_duplicate_active_rule(
        self,
        signature: RuleDuplicateSignature,
        *,
        excluding_rule_id: str | None = None,
    ) -> None:
        for existing_rule in await self._repository.list_active_rules():
            if excluding_rule_id is not None and str(existing_rule.rule_id) == str(excluding_rule_id):
                continue
            if self._build_duplicate_signature_for_rule(existing_rule) == signature:
                raise DuplicateRuleError(str(existing_rule.rule_id), existing_rule.rule_name)

    @staticmethod
    def _is_manageable_by_scope(rule: Rule, accessible_device_ids: Optional[list[str]]) -> bool:
        if accessible_device_ids is None:
            return True
        allowed = set(accessible_device_ids)
        if not allowed:
            return False
        if rule.scope == RuleScope.ALL_DEVICES.value:
            return bool(rule.device_ids) and set(rule.device_ids).issubset(allowed)
        return set(rule.device_ids or []).issubset(allowed)

    def _normalize_rule_scope(
        self,
        *,
        scope: str,
        device_ids: list[str],
        accessible_device_ids: Optional[list[str]],
    ) -> tuple[str, list[str]]:
        normalized_device_ids = self._ordered_unique_device_ids(device_ids)

        if accessible_device_ids is None:
            return scope, normalized_device_ids

        allowed = set(accessible_device_ids)
        if not allowed:
            raise PermissionError("No accessible devices are available for rule management.")

        if scope == RuleScope.ALL_DEVICES.value:
            return scope, self._ordered_unique_device_ids(accessible_device_ids)

        out_of_scope = [device_id for device_id in normalized_device_ids if device_id not in allowed]
        if out_of_scope:
            raise PermissionError(
                "Selected devices must belong to your assigned plants."
            )

        return scope, normalized_device_ids

    def _ensure_notification_channel_entitlements(self, notification_channels: list[str]) -> None:
        entitlements = self._ctx.entitlements
        for channel in notification_channels:
            entitlement_key = CHANNEL_ENTITLEMENT_KEYS.get(channel)
            if entitlement_key is None:
                continue
            if entitlements is not None and entitlements.has_premium_grant(entitlement_key):
                continue
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FEATURE_DISABLED",
                    "message": f"{channel.title()} alerts are not enabled for this organisation.",
                    "channel": channel,
                    "required_entitlement": entitlement_key,
                },
            )

    async def create_rule(
        self,
        rule_data: RuleCreate,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> Rule:

        if not rule_data.notification_channels:
            raise ValueError("At least one notification channel is required")

        if rule_data.rule_type == RuleTypeSchema.THRESHOLD:
            if rule_data.property is None or rule_data.condition is None or rule_data.threshold is None:
                raise ValueError("property, condition and threshold are required for threshold rules")
        elif rule_data.rule_type == RuleTypeSchema.TIME_BASED:
            if not rule_data.time_window_start or not rule_data.time_window_end:
                raise ValueError("time_window_start and time_window_end are required for time-based rules")
        elif rule_data.rule_type == RuleTypeSchema.CONTINUOUS_IDLE_DURATION:
            if rule_data.duration_minutes is None:
                raise ValueError("duration_minutes is required for continuous idle duration rules")

        cooldown_mode, cooldown_unit, cooldown_minutes, cooldown_seconds = normalize_cooldown_values(
            cooldown_mode=rule_data.cooldown_mode.value,
            cooldown_unit=rule_data.cooldown_unit.value if rule_data.cooldown_unit else None,
            cooldown_minutes=rule_data.cooldown_minutes,
            cooldown_seconds=rule_data.cooldown_seconds,
        )
        notification_channels = [ch.value for ch in rule_data.notification_channels]
        self._ensure_notification_channel_entitlements(notification_channels)
        notification_recipients = self._normalize_notification_recipients(
            recipients=[recipient.model_dump(mode="json") for recipient in rule_data.notification_recipients],
            notification_channels=notification_channels,
        )
        scope_value, normalized_device_ids = self._normalize_rule_scope(
            scope=rule_data.scope.value,
            device_ids=list(rule_data.device_ids or []),
            accessible_device_ids=accessible_device_ids,
        )
        await self._ensure_no_duplicate_active_rule(
            self._build_duplicate_signature(
                rule_name=rule_data.rule_name,
                description=rule_data.description,
                scope=scope_value,
                device_ids=normalized_device_ids,
                property=rule_data.property,
                condition=rule_data.condition.value if rule_data.condition else None,
                threshold=rule_data.threshold,
                rule_type=rule_data.rule_type.value,
                cooldown_mode=cooldown_mode,
                cooldown_unit=cooldown_unit,
                cooldown_minutes=cooldown_minutes,
                cooldown_seconds=cooldown_seconds,
                time_window_start=rule_data.time_window_start,
                time_window_end=rule_data.time_window_end,
                timezone=rule_data.timezone,
                time_condition=rule_data.time_condition.value if rule_data.time_condition else None,
                duration_minutes=rule_data.duration_minutes,
                notification_channels=notification_channels,
                notification_recipients=notification_recipients,
            )
        )

        rule = Rule(
            tenant_id=rule_data.tenant_id,
            rule_name=rule_data.rule_name,
            description=rule_data.description,

            # store STRING in DB
            scope=scope_value,
            device_ids=normalized_device_ids,
            property=rule_data.property,

            # store STRING in DB
            condition=rule_data.condition.value if rule_data.condition else None,

            threshold=rule_data.threshold,
            rule_type=rule_data.rule_type.value,
            cooldown_mode=cooldown_mode,
            cooldown_unit=cooldown_unit,
            time_window_start=rule_data.time_window_start,
            time_window_end=rule_data.time_window_end,
            timezone=rule_data.timezone,
            time_condition=rule_data.time_condition.value if rule_data.time_condition else None,
            duration_minutes=rule_data.duration_minutes,
            triggered_once=False,

            # store STRING in DB
            status=RuleStatus.ACTIVE.value,

            notification_channels=notification_channels,
            notification_recipients=notification_recipients,
            cooldown_minutes=cooldown_minutes,
            cooldown_seconds=cooldown_seconds,
        )

        created_rule = await self._repository.create(rule)
        await self._session.commit()

        logger.info(
            "Rule created successfully",
            extra={
                "rule_id": str(created_rule.rule_id),
                "rule_name": created_rule.rule_name,
                "scope": created_rule.scope,
                "device_count": len(created_rule.device_ids),
            }
        )

        try:
            await self._activity_service.create_for_rule(
                rule=created_rule,
                event_type="rule_created",
                title="Rule Created",
                message=f"Rule '{created_rule.rule_name}' created for property '{created_rule.property}'.",
                metadata_json={
                    "property": created_rule.property,
                    "condition": created_rule.condition,
                    "threshold": created_rule.threshold,
                    "scope": created_rule.scope,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_created activity event", extra={"error": str(exc)})

        return created_rule

    async def get_rule(
        self,
        rule_id: UUID,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> Optional[Rule]:
        return await self._repository.get_by_id(str(rule_id), accessible_device_ids=accessible_device_ids)

    async def list_rules(
        self,
        status: Optional[RuleStatusEnum] = None,
        device_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> tuple[List[Rule], int]:

        status_value = status.value if status else None

        return await self._repository.list_rules(
            status=status_value,
            device_id=device_id,
            page=page,
            page_size=page_size,
            accessible_device_ids=accessible_device_ids,
        )

    async def update_rule(
        self,
        rule_id: UUID,
        rule_data: RuleUpdate,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> Optional[Rule]:
        rule = await self._repository.get_by_id(str(rule_id), accessible_device_ids=accessible_device_ids)
        if not rule:
            logger.warning(
                "Attempted to update non-existent rule",
                extra={"rule_id": str(rule_id)}
            )
            return None

        if not self._is_manageable_by_scope(rule, accessible_device_ids):
            raise PermissionError("You can only modify rules that target devices in your assigned plants.")

        update_data = rule_data.model_dump(exclude_unset=True)
        cooldown_keys = {
            "cooldown_mode",
            "cooldown_unit",
            "cooldown_minutes",
            "cooldown_seconds",
        }
        cooldown_update_requested = any(key in update_data for key in cooldown_keys)
        current_cooldown_mode = rule.cooldown_mode
        current_cooldown_unit = getattr(rule, "cooldown_unit", None)
        current_cooldown_minutes = rule.cooldown_minutes
        current_cooldown_seconds = getattr(rule, "cooldown_seconds", None)

        for field, value in update_data.items():

            if field == "scope" and value:
                value = value.value

            elif field == "condition" and value:
                value = value.value

            elif field == "notification_channels" and value:
                value = [ch.value for ch in value]
            elif field == "notification_recipients" and value is not None:
                value = [
                    recipient.model_dump(mode="json") if hasattr(recipient, "model_dump") else dict(recipient)
                    for recipient in value
                ]
            elif field == "rule_type" and value:
                value = value.value
            elif field == "cooldown_mode" and value:
                value = value.value
            elif field == "cooldown_unit" and value:
                value = value.value
            elif field == "time_condition" and value:
                value = value.value

            setattr(rule, field, value)

        rule.scope, rule.device_ids = self._normalize_rule_scope(
            scope=rule.scope,
            device_ids=list(rule.device_ids or []),
            accessible_device_ids=accessible_device_ids,
        )
        self._ensure_notification_channel_entitlements(list(rule.notification_channels or []))
        rule.notification_recipients = self._normalize_notification_recipients(
            recipients=list(rule.notification_recipients or []),
            notification_channels=list(rule.notification_channels or []),
        )

        if cooldown_update_requested:
            cooldown_mode, cooldown_unit, cooldown_minutes, cooldown_seconds = normalize_cooldown_values(
                cooldown_mode=update_data.get("cooldown_mode"),
                cooldown_unit=update_data.get("cooldown_unit"),
                cooldown_minutes=update_data.get("cooldown_minutes"),
                cooldown_seconds=update_data.get("cooldown_seconds"),
                existing_mode=current_cooldown_mode,
                existing_unit=current_cooldown_unit,
                existing_minutes=current_cooldown_minutes,
                existing_seconds=current_cooldown_seconds,
            )
            rule.cooldown_mode = cooldown_mode
            rule.cooldown_unit = cooldown_unit
            rule.cooldown_minutes = cooldown_minutes
            rule.cooldown_seconds = cooldown_seconds

        if rule.scope == RuleScope.SELECTED_DEVICES.value and not rule.device_ids:
            raise ValueError("device_ids is required when scope is 'selected_devices'")

        if rule.rule_type == RuleType.TIME_BASED.value:
            if not rule.time_window_start or not rule.time_window_end:
                raise ValueError("time_window_start and time_window_end are required for time-based rules")
            rule.property = None
            rule.condition = None
            rule.threshold = None
            rule.duration_minutes = None
            if not rule.time_condition:
                rule.time_condition = "running_in_window"
        elif rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            if rule.duration_minutes is None:
                raise ValueError("duration_minutes is required for continuous idle duration rules")
            rule.property = None
            rule.condition = None
            rule.threshold = None
            rule.time_window_start = None
            rule.time_window_end = None
            rule.time_condition = None
        else:
            if rule.property is None or rule.condition is None or rule.threshold is None:
                raise ValueError("property, condition and threshold are required for threshold rules")
            rule.time_window_start = None
            rule.time_window_end = None
            rule.time_condition = None
            rule.duration_minutes = None

        # Manual reset for no-repeat when core condition is edited
        if rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
            core_keys = {
                "rule_type",
                "property",
                "condition",
                "threshold",
                "scope",
                "device_ids",
                "time_window_start",
                "time_window_end",
                "timezone",
                "time_condition",
                "duration_minutes",
            }
            if any(k in update_data for k in core_keys):
                rule.triggered_once = False

        if rule.status == RuleStatus.ACTIVE.value:
            await self._ensure_no_duplicate_active_rule(
                self._build_duplicate_signature_for_rule(rule),
                excluding_rule_id=str(rule.rule_id),
            )

        updated_rule = await self._repository.update(rule)
        await self._session.commit()

        logger.info(
            "Rule updated successfully",
            extra={"rule_id": str(updated_rule.rule_id)}
        )

        try:
            await self._activity_service.create_for_rule(
                rule=updated_rule,
                event_type="rule_updated",
                title="Rule Updated",
                message=f"Rule '{updated_rule.rule_name}' was updated.",
                metadata_json={
                    "property": updated_rule.property,
                    "condition": updated_rule.condition,
                    "threshold": updated_rule.threshold,
                    "scope": updated_rule.scope,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_updated activity event", extra={"error": str(exc)})

        return updated_rule

    async def update_rule_status(
        self,
        rule_id: UUID,
        status: RuleStatusEnum,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> Optional[Rule]:
        existing_rule = await self._repository.get_by_id(
            str(rule_id),
            accessible_device_ids=accessible_device_ids,
        )
        if not existing_rule:
            return None
        if not self._is_manageable_by_scope(existing_rule, accessible_device_ids):
            raise PermissionError("You can only modify rules that target devices in your assigned plants.")

        rule = await self._repository.update_status(
            str(rule_id),
            status.value
        )

        if rule:
            # Manual reset: pause -> active unlocks no-repeat rules
            if status.value == RuleStatus.ACTIVE.value and rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
                rule.triggered_once = False
            await self._session.commit()
            logger.info(
                "Rule status updated",
                extra={
                    "rule_id": str(rule_id),
                    "new_status": status.value,
                }
            )
            try:
                await self._activity_service.create_for_rule(
                    rule=rule,
                    event_type="rule_status_changed",
                    title="Rule Status Changed",
                    message=f"Rule '{rule.rule_name}' status changed to '{status.value}'.",
                    metadata_json={"status": status.value},
                )
            except Exception as exc:
                logger.warning("Failed to persist rule_status_changed activity event", extra={"error": str(exc)})
        return rule

    async def delete_rule(
        self,
        rule_id: UUID,
        soft: bool = True,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> bool:
        rule = await self._repository.get_by_id(str(rule_id), accessible_device_ids=accessible_device_ids)
        if not rule:
            return False
        if not self._is_manageable_by_scope(rule, accessible_device_ids):
            raise PermissionError("You can only modify rules that target devices in your assigned plants.")

        await self._repository.delete(rule, soft=soft)
        await self._session.commit()

        logger.info(
            "Rule deleted successfully",
            extra={
                "rule_id": str(rule_id),
                "soft_delete": soft,
            }
        )

        try:
            await self._activity_service.create_for_rule(
                rule=rule,
                event_type="rule_deleted" if not soft else "rule_archived",
                title="Rule Deleted" if not soft else "Rule Archived",
                message=f"Rule '{rule.rule_name}' was {'deleted' if not soft else 'archived'}.",
                metadata_json={"soft_delete": soft},
            )
        except Exception as exc:
            logger.warning("Failed to persist rule delete activity event", extra={"error": str(exc)})

        return True

    async def get_active_rules_for_device(
        self,
        device_id: str,
    ) -> List[Rule]:
        return await self._repository.get_active_rules_for_device(device_id)


class AlertService:
    """Service layer for alert management."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._repository = AlertRepository(session, ctx)
        self._activity_service = ActivityEventService(session, ctx)

    async def create_alert(
        self,
        rule: Rule,
        device_id: str,
        actual_value: float,
        severity: str = "medium",
    ) -> Alert:
        tenant_id = self._ctx.require_tenant()
        if rule.tenant_id != tenant_id:
            raise ValueError("Rule tenant does not match alert service tenant scope.")

        is_time_based = rule.rule_type == RuleType.TIME_BASED.value
        is_continuous_idle = rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value
        if is_time_based:
            threshold_value = 1.0
            condition_text = f"running in window {rule.time_window_start}-{rule.time_window_end} {platform_tz_label()}"
        elif is_continuous_idle:
            threshold_value = float(rule.duration_minutes or 0)
            condition_text = f"idle continuously for {rule.duration_minutes} minute(s)"
        else:
            threshold_value = rule.threshold if rule.threshold is not None else 0.0
            condition_text = f"{rule.property} {rule.condition} {rule.threshold}"

        message = (
            f"Rule '{rule.rule_name}' triggered for device {device_id}: "
            f"{condition_text} "
            f"(actual: {actual_value})"
        )

        alert = Alert(
            tenant_id=tenant_id,
            rule_id=rule.rule_id,
            device_id=device_id,
            severity=severity,
            message=message,
            actual_value=actual_value,
            threshold_value=threshold_value,
            status="open",
        )

        created_alert = await self._repository.create(alert)

        logger.info(
            "Alert created",
            extra={
                "alert_id": str(created_alert.alert_id),
                "rule_id": str(rule.rule_id),
                "device_id": device_id,
            }
        )

        try:
            await self._activity_service.create_event(
                event_type="rule_triggered",
                title="Rule Triggered",
                message=(
                    f"Rule '{rule.rule_name}' triggered: {rule.property} {rule.condition} "
                    f"{rule.threshold} (actual: {actual_value})."
                ),
                device_id=device_id,
                rule_id=str(rule.rule_id),
                alert_id=str(created_alert.alert_id),
                metadata_json={
                    "property": rule.property,
                    "condition": rule.condition,
                    "threshold": rule.threshold,
                    "actual_value": actual_value,
                    "severity": severity,
                },
            )
        except Exception as exc:
            logger.warning("Failed to persist rule_triggered activity event", extra={"error": str(exc)})

        return created_alert


class ActivityEventService:
    """Service layer for activity events."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._repository = ActivityEventRepository(session, ctx)

    async def create_event(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        device_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        metadata_json: Optional[dict] = None,
    ):
        event = await self._repository.create(
            event_type=event_type,
            title=title,
            message=message,
            device_id=device_id,
            rule_id=rule_id,
            alert_id=alert_id,
            metadata_json=metadata_json,
        )
        await self._session.commit()
        return event

    async def create_for_rule(
        self,
        *,
        rule: Rule,
        event_type: str,
        title: str,
        message: str,
        metadata_json: Optional[dict] = None,
    ) -> None:
        target_devices: List[Optional[str]] = list(rule.device_ids or [])
        if rule.scope == RuleScope.ALL_DEVICES.value and not target_devices:
            target_devices = [None]
        elif not target_devices:
            target_devices = [None]

        for device_id in target_devices:
            await self._repository.create(
                event_type=event_type,
                title=title,
                message=message,
                device_id=device_id,
                rule_id=str(rule.rule_id),
                metadata_json=metadata_json or {},
            )
        await self._session.commit()
