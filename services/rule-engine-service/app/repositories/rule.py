"""Rule repository layer - data access abstraction."""

import asyncio
from typing import Optional, List, Dict, Any
import logging

from datetime import datetime, timezone, timedelta
from uuid import UUID

from sqlalchemy import select, func, and_, or_, false, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import Rule, RuleScope, RuleStatus, CooldownMode, Alert, ActivityEvent, RuleTriggerState
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext

logger = logging.getLogger(__name__)


class RuleRepository(TenantScopedRepository[Rule]):
    """Repository for Rule entity operations.
    
    Implements repository pattern for clean separation between
    data access and business logic layers.
    """
    
    model = Rule

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
    ):
        super().__init__(session, ctx)

    @staticmethod
    def _coerce_utc_timestamp(value: Optional[datetime]) -> Optional[datetime]:
        """Treat naive database timestamps as UTC for cooldown comparisons."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    
    @staticmethod
    def _rule_visible_to_scope(rule: Rule, accessible_device_ids: Optional[list[str]] = None) -> bool:
        if accessible_device_ids is None:
            return True
        if not accessible_device_ids:
            return False
        allowed = set(accessible_device_ids)
        if rule.scope == RuleScope.ALL_DEVICES.value:
            if not rule.device_ids:
                return False
            return set(rule.device_ids).issubset(allowed)
        return bool(rule.device_ids) and set(rule.device_ids).issubset(allowed)

    async def create(self, rule: Rule) -> Rule:
        """Create a new rule in the database."""
        rule.tenant_id = self._tenant_id
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def list_active_rules(self) -> list[Rule]:
        """Return active, non-deleted rules for duplicate detection."""
        statement = select(Rule).where(
            Rule.status == RuleStatus.ACTIVE,
            Rule.deleted_at.is_(None),
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return list(result.scalars().all())
    
    async def get_by_id(
        self, 
        rule_id: str,
        accessible_device_ids: Optional[list[str]] = None,
        *_: object,
        **__: object,
    ) -> Optional[Rule]:
        """Get rule by ID with optional tenant filtering."""
        query = self._apply_tenant_scope_select(select(Rule).where(Rule.rule_id == rule_id))
        
        result = await self._session.execute(query)
        rule = result.scalar_one_or_none()
        if rule is None or not self._rule_visible_to_scope(rule, accessible_device_ids):
            return None
        return rule
    
    async def get_active_rules_for_device(
        self,
        device_id: str,
        *_: object,
        **__: object,
    ) -> List[Rule]:
        """Get all active rules that apply to a specific device.
        
        Args:
            device_id: Device identifier
            tenant_id: Optional tenant ID for multi-tenancy
            
        Returns:
            List of active rules applicable to the device
        """
        dialect_name = getattr(getattr(self._session.bind, "dialect", None), "name", "")
        if dialect_name == "sqlite":
            query = select(Rule).where(
                and_(
                    Rule.status == RuleStatus.ACTIVE,
                    Rule.deleted_at.is_(None),
                )
            ).limit(200)
            query = self._apply_tenant_scope_select(query)
            result = await self._session.execute(query)
            rules = [rule for rule in result.scalars().all() if rule.applies_to_device(device_id)]
        else:
            query = select(Rule).where(
                and_(
                    Rule.status == RuleStatus.ACTIVE,
                    Rule.deleted_at.is_(None),
                    or_(
                        Rule.scope == "all_devices",
                        (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                    )
                )
            ).limit(200)
            query = self._apply_tenant_scope_select(query)
            result = await self._session.execute(query)
            rules = list(result.scalars().all())
        logger.debug("rules_fetched", extra={"device_id": device_id, "count": len(rules)})
        return rules
    
    async def list_rules(
        self,
        status: Optional[RuleStatus] = None,
        device_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> tuple[List[Rule], int]:
        """List rules with filtering and pagination.
        
        Returns:
            Tuple of (rules list, total count)
        """
        # Build base query
        query = select(Rule).where(Rule.deleted_at.is_(None))
        
        query = self._apply_tenant_scope_select(query)
        
        if status:
            query = query.where(Rule.status == status)
        
        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                return [], 0

        result = await self._session.execute(query)
        rules = list(result.scalars().all())
        filtered_rules = [
            rule
            for rule in rules
            if self._rule_visible_to_scope(rule, accessible_device_ids)
            and (device_id is None or rule.applies_to_device(device_id))
        ]

        total = len(filtered_rules)
        offset = (page - 1) * page_size
        paged_rules = filtered_rules[offset : offset + page_size]

        return paged_rules, total
    
    async def update(self, rule: Rule) -> Rule:
        """Update an existing rule."""
        await self._session.flush()
        await self._session.refresh(rule)
        return rule
    
    async def update_last_triggered(self, rule_id: str) -> None:
        """Update the last_triggered_at timestamp for a rule."""
        rule = await self.get_by_id(rule_id)
        if rule:
            # IMPORTANT: store timezone-aware timestamp
            rule.last_triggered_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def try_acquire_trigger_slot(
        self,
        *,
        rule_id: str,
        device_id: str,
        cooldown_mode: str,
        cooldown_seconds: int,
    ) -> bool:
        """
        Atomically reserve the right to emit an alert for a rule+device trigger.

        Cooldown/no-repeat is enforced per target device so one machine firing does
        not suppress alerts for another machine under the same rule.
        """
        now = datetime.now(timezone.utc)
        for attempt in range(4):
            try:
                async with self._session.begin_nested():
                    state_stmt = (
                        select(RuleTriggerState)
                        .where(
                            RuleTriggerState.rule_id == rule_id,
                            RuleTriggerState.device_id == device_id,
                        )
                        .with_for_update()
                    )
                    if self._tenant_id is not None:
                        state_stmt = state_stmt.where(RuleTriggerState.tenant_id == self._tenant_id)
                    result = await self._session.execute(state_stmt)
                    state = result.scalar_one_or_none()

                    if state is None:
                        state = RuleTriggerState(
                            tenant_id=self._tenant_id,
                            rule_id=rule_id,
                            device_id=device_id,
                            last_triggered_at=now,
                            triggered_once=(cooldown_mode == CooldownMode.NO_REPEAT.value),
                        )
                        self._session.add(state)
                        await self._session.flush()
                    elif cooldown_mode == CooldownMode.NO_REPEAT.value:
                        if state.triggered_once:
                            return False
                        state.triggered_once = True
                        state.last_triggered_at = now
                    else:
                        cooldown_seconds = max(int(cooldown_seconds), 0)
                        last_triggered_at = self._coerce_utc_timestamp(state.last_triggered_at)
                        if cooldown_seconds > 0 and last_triggered_at is not None:
                            cooldown_cutoff = now - timedelta(seconds=cooldown_seconds)
                            if last_triggered_at > cooldown_cutoff:
                                return False
                        state.last_triggered_at = now

                    metadata_stmt = (
                        update(Rule)
                        .where(
                            Rule.rule_id == rule_id,
                            Rule.deleted_at.is_(None),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    metadata_stmt = self._apply_tenant_scope_dml(metadata_stmt)
                    metadata_values = {"last_triggered_at": now}
                    if cooldown_mode == CooldownMode.NO_REPEAT.value:
                        metadata_values["triggered_once"] = True
                    await self._session.execute(metadata_stmt.values(**metadata_values))
                    return True
            except IntegrityError:
                await self._session.rollback()
                continue
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                if attempt == 3:
                    raise
                await self._session.rollback()
                await asyncio.sleep(0.05 * (attempt + 1))
        return False
    
    async def update_status(self, rule_id: str, status: RuleStatus) -> Optional[Rule]:
        """Update rule status."""
        rule = await self.get_by_id(rule_id)
        if rule:
            rule.status = status
            rule.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            await self._session.refresh(rule)
        return rule
    
    async def delete(self, rule: Rule, soft: bool = True) -> None:
        """Delete a rule (soft or hard delete)."""
        if soft:
            rule.deleted_at = datetime.now(timezone.utc)
            rule.status = RuleStatus.ARCHIVED
            await self._session.flush()
        else:
            await self._session.delete(rule)
            await self._session.flush()
    
    async def exists(self, rule_id: str) -> bool:
        """Check if a rule with given ID exists."""
        query = select(func.count(Rule.rule_id)).where(
            Rule.rule_id == rule_id,
            Rule.deleted_at.is_(None)
        )
        result = await self._session.execute(query)
        return result.scalar() > 0
    
    async def count_active_rules_for_device(self, device_id: str) -> int:
        """Count active rules for a specific device."""
        query = select(func.count(Rule.rule_id)).where(
            and_(
                Rule.status == RuleStatus.ACTIVE,
                Rule.deleted_at.is_(None),
                or_(
                    Rule.scope == "all_devices",
                    (func.json_contains(Rule.device_ids, func.json_quote(device_id)) == 1)
                )
            )
        )
        query = self._apply_tenant_scope_select(query)
        result = await self._session.execute(query)
        return result.scalar()


class AlertRepository(TenantScopedRepository[Alert]):
    """Repository for Alert entity operations."""

    model = Alert

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
    ):
        super().__init__(session, ctx)
    
    @staticmethod
    def _apply_alert_device_scope(
        query,
        accessible_device_ids: Optional[list[str]] = None,
    ):
        if accessible_device_ids is None:
            return query
        if not accessible_device_ids:
            return query.where(false())
        return query.where(Alert.device_id.in_(accessible_device_ids))

    async def create(self, alert: Alert) -> Alert:
        """Create a new alert in the database."""
        alert.tenant_id = self._tenant_id
        self._session.add(alert)
        await self._session.flush()
        await self._session.refresh(alert)
        return alert
    
    async def get_by_id(
        self, 
        alert_id: str | UUID,
        accessible_device_ids: Optional[list[str]] = None,
        *_: object,
        **__: object,
    ) -> Optional[Alert]:
        """Get alert by ID with optional tenant filtering."""
        alert_id_str = str(alert_id)
        query = self._apply_tenant_scope_select(select(Alert).where(Alert.alert_id == alert_id_str))
        query = self._apply_alert_device_scope(query, accessible_device_ids)
        
        result = await self._session.execute(query)
        return result.scalar_one_or_none()
    
    async def list_alerts(
        self,
        device_id: Optional[str] = None,
        rule_id: Optional[str | UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> tuple[List[Alert], int]:
        """List alerts with filtering and pagination."""
        query = select(Alert)
        count_query = select(func.count(Alert.alert_id))
        query = self._apply_tenant_scope_select(query)
        count_query = self._apply_tenant_scope_select(count_query)
        query = self._apply_alert_device_scope(query, accessible_device_ids)
        count_query = self._apply_alert_device_scope(count_query, accessible_device_ids)
        
        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                query = query.where(false())
                count_query = count_query.where(false())
            query = query.where(Alert.device_id == device_id)
            count_query = count_query.where(Alert.device_id == device_id)
        
        if rule_id:
            rule_id_str = str(rule_id)
            query = query.where(Alert.rule_id == rule_id_str)
            count_query = count_query.where(Alert.rule_id == rule_id_str)
        
        if status:
            query = query.where(Alert.status == status)
            count_query = count_query.where(Alert.status == status)
        
        # Get total count
        count_result = await self._session.execute(count_query)
        total = count_result.scalar()
        
        # Apply pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        query = query.order_by(Alert.created_at.desc())
        
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------
    # NEW – permanent, non-breaking extensions
    # ------------------------------------------------------------------

    async def acknowledge_alert(
        self,
        alert_id: str,
        acknowledged_by: Optional[str] = None,
    ) -> Optional[Alert]:
        """
        Mark an alert as acknowledged.
        """
        alert = await self.get_by_id(alert_id)

        if not alert:
            return None

        alert.status = "acknowledged"
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.now(timezone.utc)

        await self._session.flush()
        await self._session.refresh(alert)

        return alert

    async def resolve_alert(
        self,
        alert_id: str,
    ) -> Optional[Alert]:
        """
        Mark an alert as resolved.
        """
        alert = await self.get_by_id(alert_id)

        if not alert:
            return None

        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)

        await self._session.flush()
        await self._session.refresh(alert)

        return alert

    async def count_by_status(
        self,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> Dict[str, int]:
        """Count alerts grouped by status."""
        query = self._apply_tenant_scope_select(
            select(Alert.status, func.count(Alert.alert_id)).group_by(Alert.status)
        )
        query = self._apply_alert_device_scope(query, accessible_device_ids)
        result = await self._session.execute(query)
        rows = result.all()
        return {str(status): int(count) for status, count in rows}

    async def latest_for_rule_device(
        self,
        *,
        rule_id: str,
        device_id: str,
    ) -> Optional[Alert]:
        """Return latest alert for a rule+device pair."""
        query = (
            select(Alert)
            .where(
                Alert.rule_id == rule_id,
                Alert.device_id == device_id,
            )
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(self._apply_tenant_scope_select(query))
        return result.scalar_one_or_none()


class ActivityEventRepository(TenantScopedRepository[ActivityEvent]):
    """Repository for activity event operations."""

    model = ActivityEvent

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
    ):
        super().__init__(session, ctx)

    async def create(
        self,
        *,
        event_type: str,
        title: str,
        message: str,
        device_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            tenant_id=self._tenant_id,
            device_id=device_id,
            rule_id=rule_id,
            alert_id=alert_id,
            event_type=event_type,
            title=title,
            message=message,
            metadata_json=metadata_json or {},
            is_read=False,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def list_events(
        self,
        *,
        device_id: Optional[str] = None,
        event_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> tuple[List[ActivityEvent], int]:
        query = select(ActivityEvent)
        count_query = select(func.count(ActivityEvent.event_id))

        query = self._apply_tenant_scope_select(query)
        count_query = self._apply_tenant_scope_select(count_query)
        query = self._apply_event_device_scope(query, accessible_device_ids)
        count_query = self._apply_event_device_scope(count_query, accessible_device_ids)

        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                query = query.where(false())
                count_query = count_query.where(false())
            query = query.where(ActivityEvent.device_id == device_id)
            count_query = count_query.where(ActivityEvent.device_id == device_id)

        if event_type:
            query = query.where(ActivityEvent.event_type == event_type)
            count_query = count_query.where(ActivityEvent.event_type == event_type)

        total_result = await self._session.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(ActivityEvent.created_at.desc()).offset(offset).limit(page_size)

        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def unread_count(
        self,
        *,
        device_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> int:
        query = select(func.count(ActivityEvent.event_id)).where(ActivityEvent.is_read.is_(False))
        query = self._apply_tenant_scope_select(query)
        query = self._apply_event_device_scope(query, accessible_device_ids)
        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                query = query.where(false())
            query = query.where(ActivityEvent.device_id == device_id)

        result = await self._session.execute(query)
        return result.scalar() or 0

    async def mark_all_read(
        self,
        *,
        device_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> int:
        query = select(ActivityEvent).where(ActivityEvent.is_read.is_(False))
        query = self._apply_tenant_scope_select(query)
        query = self._apply_event_device_scope(query, accessible_device_ids)
        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                query = query.where(false())
            query = query.where(ActivityEvent.device_id == device_id)

        rows = (await self._session.execute(query)).scalars().all()
        now = datetime.now(timezone.utc)
        for event in rows:
            event.is_read = True
            event.read_at = now

        await self._session.flush()
        return len(rows)

    async def clear_history(
        self,
        *,
        device_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> int:
        query = select(ActivityEvent)
        query = self._apply_tenant_scope_select(query)
        query = self._apply_event_device_scope(query, accessible_device_ids)
        if device_id:
            if accessible_device_ids is not None and device_id not in accessible_device_ids:
                query = query.where(false())
            query = query.where(ActivityEvent.device_id == device_id)

        rows = (await self._session.execute(query)).scalars().all()
        count = len(rows)
        for event in rows:
            await self._session.delete(event)
        await self._session.flush()
        return count

    async def count_by_event_types(
        self,
        event_types: List[str],
        accessible_device_ids: Optional[list[str]] = None,
        **_: object,
    ) -> Dict[str, int]:
        """Count activity events grouped by event type."""
        if not event_types:
            return {}

        query = (
            select(ActivityEvent.event_type, func.count(ActivityEvent.event_id))
            .where(ActivityEvent.event_type.in_(event_types))
            .group_by(ActivityEvent.event_type)
        )
        query = self._apply_tenant_scope_select(query)
        query = self._apply_event_device_scope(query, accessible_device_ids)

        result = await self._session.execute(query)
        rows = result.all()
        return {str(event_type): int(count) for event_type, count in rows}
    @staticmethod
    def _apply_event_device_scope(
        query,
        accessible_device_ids: Optional[list[str]] = None,
    ):
        if accessible_device_ids is None:
            return query
        if not accessible_device_ids:
            return query.where(false())
        return query.where(ActivityEvent.device_id.in_(accessible_device_ids))
