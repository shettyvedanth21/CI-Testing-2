"""Rule evaluation engine for real-time telemetry processing."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.alert_rate_limiter import get_alert_rate_limiter
from app.models.rule import CooldownMode, Rule, RuleType
from app.schemas.rule import EvaluationResult, TelemetryPayload
from app.schemas.telemetry import TelemetryIn
from app.services.rule import RuleService, AlertService
from app.services.device_metadata import DeviceMetadataService
from app.services.notification_outbox import NotificationContent, NotificationOutboxService
from app.repositories.rule import RuleRepository, AlertRepository
from app.config import settings
from app.utils.threshold_properties import resolve_threshold_property_value
from app.utils.timezone import format_platform_datetime
from services.shared.tenant_context import TenantContext

logger = logging.getLogger(__name__)


class RuleEvaluator:

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._rule_service = RuleService(session, ctx)
        self._alert_service = AlertService(session, ctx)
        self._rule_repository = RuleRepository(session, ctx)
        self._alert_repository = AlertRepository(session, ctx)
        self._device_metadata_service = DeviceMetadataService(ctx)
        self._notification_outbox_service = NotificationOutboxService(session, ctx)

    def _require_rule_tenant(self, rule: Rule) -> str:
        tenant_id = self._ctx.require_tenant()
        if rule.tenant_id != tenant_id:
            raise ValueError("Rule tenant does not match evaluator tenant scope.")
        return tenant_id

    @staticmethod
    def _get_platform_tz() -> ZoneInfo:
        return ZoneInfo(settings.PLATFORM_TIMEZONE)

    async def evaluate_telemetry(
        self,
        telemetry: TelemetryPayload,
    ) -> tuple[int, int, List[EvaluationResult]]:

        device_id = telemetry.device_id
        now = datetime.now(timezone.utc)

        limiter = get_alert_rate_limiter()
        if await limiter.is_rate_limited(device_id, now):
            logger.warning(
                "rule_evaluation_skipped_alert_storm",
                extra={"device_id": device_id, "window_seconds": 60, "threshold": 50},
            )
            return 0, 0, []

        rules = await self._rule_service.get_active_rules_for_device(
            device_id=device_id,
        )

        if not rules:
            logger.debug(
                "No active rules for device",
                extra={"device_id": device_id},
            )
            return 0, 0, []

        triggered_rules: List[EvaluationResult] = []

        for rule in rules:
            self._require_rule_tenant(rule)
            result = await self._evaluate_single_rule(rule, telemetry)

            if result.triggered:
                acquired = await self._rule_repository.try_acquire_trigger_slot(
                    rule_id=str(rule.rule_id),
                    device_id=device_id,
                    cooldown_mode=rule.cooldown_mode,
                    cooldown_seconds=rule.effective_cooldown_seconds(),
                )
                if not acquired:
                    continue
                # Keep the in-session entity aligned with the atomic DB update.
                rule.last_triggered_at = now
                if rule.cooldown_mode == CooldownMode.NO_REPEAT.value:
                    rule.triggered_once = True

                triggered_rules.append(result)

                created_alert = await self._alert_service.create_alert(
                    rule=rule,
                    device_id=device_id,
                    actual_value=result.actual_value,
                    severity=self._determine_severity(rule, result.actual_value),
                )
                await limiter.record_alert(device_id, now)

                await self._enqueue_notifications(rule, device_id, result, alert_id=str(created_alert.alert_id))

        await self._session.commit()

        logger.info(
            "Rule evaluation completed",
            extra={
                "device_id": device_id,
                "rules_evaluated": len(rules),
                "rules_triggered": len(triggered_rules),
            },
        )

        return len(rules), len(triggered_rules), triggered_rules

    async def _evaluate_single_rule(
        self,
        rule: Rule,
        telemetry: TelemetryPayload,
    ) -> EvaluationResult:
        if rule.rule_type == RuleType.TIME_BASED.value:
            triggered, actual_value = self._evaluate_time_based_rule(rule, telemetry)
            condition = "running_in_window"
            threshold = 1.0
        elif rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            triggered, actual_value = self._evaluate_continuous_idle_rule(rule, telemetry)
            condition = ">="
            threshold = float(rule.duration_minutes or 0)
        else:
            actual_value = self._extract_property_value(telemetry, rule.property or "")
            triggered = self._evaluate_condition(
                actual_value=actual_value,
                threshold=rule.threshold if rule.threshold is not None else 0.0,
                operator=rule.condition or "=",
            )
            condition = rule.condition or "="
            threshold = rule.threshold if rule.threshold is not None else 0.0

        message = None
        if triggered:
            if rule.rule_type == RuleType.TIME_BASED.value:
                message = (
                    f"Device running during restricted window "
                    f"{rule.time_window_start}-{rule.time_window_end} {settings.PLATFORM_TIMEZONE}"
                )
            elif rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
                message = (
                    f"Device idle continuously for {actual_value:.2f} minutes "
                    f"(threshold: {rule.duration_minutes} minutes)"
                )
            else:
                message = (
                    f"{rule.property} is {actual_value} "
                    f"(threshold: {rule.condition} {rule.threshold})"
                )

        return EvaluationResult(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            triggered=triggered,
            actual_value=actual_value,
            threshold=threshold,
            condition=condition,
            message=message,
        )

    def _evaluate_time_based_rule(
        self,
        rule: Rule,
        telemetry: TelemetryPayload,
    ) -> tuple[bool, float]:
        if not rule.time_window_start or not rule.time_window_end:
            return False, 0.0

        if not self._is_running_signal(telemetry):
            return False, 0.0

        if self._is_timestamp_in_window(telemetry.timestamp, rule.time_window_start, rule.time_window_end):
            return True, 1.0

        return False, 0.0

    def _evaluate_continuous_idle_rule(
        self,
        rule: Rule,
        telemetry: TelemetryPayload,
    ) -> tuple[bool, float]:
        if rule.duration_minutes is None:
            return False, 0.0

        streak_duration_sec = max(int(telemetry.idle_streak_duration_sec or 0), 0)
        streak_duration_minutes = streak_duration_sec / 60.0
        if telemetry.projected_load_state != "idle":
            return False, streak_duration_minutes

        return streak_duration_sec >= int(rule.duration_minutes) * 60, streak_duration_minutes

    def _is_running_signal(self, telemetry: TelemetryPayload) -> bool:
        dynamic_fields = telemetry.get_dynamic_fields()

        power = dynamic_fields.get("power")
        if power is None:
            power = dynamic_fields.get("active_power")
        if power is not None:
            return power > 0

        current = dynamic_fields.get("current")
        if current is None:
            return False

        voltage = dynamic_fields.get("voltage")
        if voltage is None:
            return current > 0

        return current > 0 and voltage > 0

    def _is_timestamp_in_window(self, timestamp: datetime, start_hhmm: str, end_hhmm: str) -> bool:
        local_tz = self._get_platform_tz()
        ts = (
            timestamp.astimezone(local_tz)
            if timestamp.tzinfo
            else timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(local_tz)
        )
        current_minutes = ts.hour * 60 + ts.minute

        start_h, start_m = (int(v) for v in start_hhmm.split(":"))
        end_h, end_m = (int(v) for v in end_hhmm.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def _extract_property_value(
        self,
        telemetry: TelemetryPayload,
        property_name: str,
    ) -> float:
        dynamic_fields = telemetry.get_dynamic_fields()
        return resolve_threshold_property_value(dynamic_fields, property_name)

    def _evaluate_condition(
        self,
        actual_value: float,
        threshold: float,
        operator: str,
    ) -> bool:

        operators = {
            ">": lambda a, t: a > t,
            "<": lambda a, t: a < t,
            "==": lambda a, t: a == t,
            "=": lambda a, t: a == t,
            "!=": lambda a, t: a != t,
            ">=": lambda a, t: a >= t,
            "<=": lambda a, t: a <= t,
        }

        if operator not in operators:
            raise ValueError(f"Unknown operator: {operator}")

        return operators[operator](actual_value, threshold)

    def _determine_severity(self, rule: Rule, actual_value: float) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return "medium"
        if rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            return "medium"

        if not rule.threshold or rule.threshold == 0:
            deviation = abs(actual_value)
        else:
            deviation = abs((actual_value - rule.threshold) / rule.threshold)

        if deviation > 0.5:
            return "critical"
        elif deviation > 0.25:
            return "high"
        elif deviation > 0.1:
            return "medium"
        else:
            return "low"

    async def _enqueue_notifications(
        self,
        rule: Rule,
        device_id: str,
        result: EvaluationResult,
        alert_id: Optional[str] = None,
    ) -> None:

        if not rule.notification_channels:
            return

        triggered_at = datetime.now(timezone.utc)
        device_name = None
        device_location = None
        if "email" in set(rule.notification_channels):
            try:
                metadata = await self._device_metadata_service.get_device_metadata(device_id)
                device_name = metadata.device_name
                device_location = metadata.device_location
            except Exception as exc:
                logger.warning(
                    "device_metadata_lookup_failed",
                    extra={"device_id": device_id, "error": str(exc)},
                )

        property_label = self._humanize_property_label(rule)
        condition_label = self._humanize_condition_label(rule)
        actual_value_label = self._format_actual_value(rule, result.actual_value)
        triggered_at_label = format_platform_datetime(triggered_at)

        message = (
            f"Rule Name: {rule.rule_name}\n"
            f"Device Name: {device_name or device_id}\n"
            f"Device ID: {device_id}\n"
            f"Device Location: {device_location or 'Not specified'}\n"
            f"Property: {property_label}\n"
            f"Condition: {condition_label}\n"
            f"Actual Value: {actual_value_label}\n"
            f"Triggered Time: {triggered_at_label}"
        )

        alert_context = {
            "rule_name": rule.rule_name,
            "device_name": device_name or device_id,
            "device_id": device_id,
            "device_location": device_location or "Not specified",
            "property_label": property_label,
            "condition_label": condition_label,
            "property": property_label,
            "condition": condition_label,
            "actual_value": actual_value_label,
            "triggered_at": triggered_at_label,
        }
        await self._notification_outbox_service.enqueue_alert_notifications(
            rule=rule,
            device_id=device_id,
            alert_id=alert_id or "",
            content=NotificationContent(
                subject=f"Alert: {rule.rule_name}",
                message=message,
                alert_context=alert_context,
            ),
        )

    @staticmethod
    def _describe_rule_condition(rule: Rule) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return "running in restricted window"
        if rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            return f"idle continuously for {rule.duration_minutes} minute(s)"
        return f"{rule.property} {rule.condition} {rule.threshold}"

    @staticmethod
    def _humanize_property_label(rule: Rule) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return "Power Status"
        if rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            return "Idle Duration"
        raw_property = str(rule.property or "value").replace("_", " ").strip()
        return raw_property.title() if raw_property else "Value"

    @staticmethod
    def _humanize_condition_label(rule: Rule) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return f"running between {rule.time_window_start}-{rule.time_window_end} {settings.PLATFORM_TIMEZONE}"
        if rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            return f"greater than or equal to {rule.duration_minutes} minute(s)"
        operator_map = {
            ">": "greater than",
            "<": "less than",
            ">=": "greater than or equal to",
            "<=": "less than or equal to",
            "=": "equal to",
            "==": "equal to",
            "!=": "not equal to",
        }
        operator = operator_map.get(str(rule.condition or "").strip(), str(rule.condition or "").strip() or "equal to")
        return f"{operator} {rule.threshold}"

    @staticmethod
    def _format_actual_value(rule: Rule, actual_value: float) -> str:
        if rule.rule_type == RuleType.TIME_BASED.value:
            return "Running"
        if rule.rule_type == RuleType.CONTINUOUS_IDLE_DURATION.value:
            return f"{actual_value:.2f} minute(s)"
        return f"{actual_value:.3f}".rstrip("0").rstrip(".")

    async def evaluate(
        self,
        telemetry: TelemetryIn,
    ) -> List[Rule]:

        device_id = telemetry.device_id
        metric = telemetry.metric
        value = telemetry.value

        rules = await self._rule_repository.get_active_rules_for_device(device_id)

        matched_rules: List[Rule] = []

        for rule in rules:

            if rule.property != metric:
                continue

            if self._evaluate_condition(value, rule.threshold, rule.condition):
                matched_rules.append(rule)

        logger.debug(
            "Simple evaluation completed",
            extra={
                "device_id": device_id,
                "metric": metric,
                "value": value,
                "rules_evaluated": len(rules),
                "rules_matched": len(matched_rules),
            },
        )

        return matched_rules
