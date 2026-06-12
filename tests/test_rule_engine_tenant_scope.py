from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


RULE_ENGINE_ROOT = Path(__file__).resolve().parents[1] / "services" / "rule-engine-service"
SERVICES_ROOT = Path(__file__).resolve().parents[1] / "services"
REPO_ROOT = SERVICES_ROOT.parent
for existing in list(sys.path):
    try:
        existing_path = Path(existing).resolve()
    except Exception:
        continue
    if existing_path.parent == SERVICES_ROOT.resolve() and existing_path != RULE_ENGINE_ROOT.resolve():
        sys.path.remove(existing)
for module_name, module in list(sys.modules.items()):
    if module_name == "app" or module_name.startswith("app."):
        module_file = Path(getattr(module, "__file__", "") or "")
        if str(module_file) and RULE_ENGINE_ROOT.resolve() not in module_file.resolve().parents:
            sys.modules.pop(module_name, None)
for path in (REPO_ROOT, SERVICES_ROOT, RULE_ENGINE_ROOT):
    path_str = str(path)
    if path_str in sys.path:
        sys.path.remove(path_str)
    sys.path.insert(0, path_str)

os.environ.setdefault("REPORTING_SERVICE_URL", "http://reporting-service")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///dummy.db")
os.environ.setdefault("QUEUE_BACKEND", "memory")

from app.api.v1 import alerts as alerts_api
from app.api.v1 import rules as rules_api
from app.database import Base
from app.repositories.rule import ActivityEventRepository, AlertRepository, RuleRepository
from app.schemas.rule import (
    NotificationChannel,
    RuleCreate,
    RuleScope,
    RuleStatus,
    TelemetryPayload,
    RuleUpdate,
)
from app.services.evaluator import RuleEvaluator
from app.services.rule import AlertService, RuleService
from services.shared.tenant_context import TenantContext


async def _accessible_device_ids_for_tests(ctx: TenantContext) -> list[str] | None:
    if ctx.role == "plant_manager":
        return ["DEV-1"]
    return None


@asynccontextmanager
async def rule_session_ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_json_contains(dbapi_connection, _connection_record):
        def _json_contains(document: str | None, candidate: str | None) -> int:
            if document is None or candidate is None:
                return 0
            try:
                container = json.loads(document)
                needle = json.loads(candidate)
            except Exception:
                return 0
            if isinstance(container, list):
                return int(needle in container)
            if isinstance(container, dict):
                return int(needle in container.values())
            return int(container == needle)

        dbapi_connection.create_function("json_contains", 2, _json_contains)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@asynccontextmanager
async def rule_file_session_ctx(tmp_path: Path):
    db_path = tmp_path / "rule-engine-concurrency.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_json_contains_file(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

        def _json_contains(document: str | None, candidate: str | None) -> int:
            if document is None or candidate is None:
                return 0
            try:
                container = json.loads(document)
                needle = json.loads(candidate)
            except Exception:
                return 0
            if isinstance(container, list):
                return int(needle in container)
            if isinstance(container, dict):
                return int(needle in container.values())
            return int(container == needle)

        dbapi_connection.create_function("json_contains", 2, _json_contains)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


def _tenant_request(tenant_id: str | None, role: str = "internal_service"):
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_context=TenantContext(
                tenant_id=tenant_id,
                user_id="svc:test",
                role=role,
                plant_ids=[],
                is_super_admin=False,
            )
        ),
        headers={},
        query_params={},
    )


def _rule_create_payload(name: str, tenant_id: str | None = None) -> RuleCreate:
    return RuleCreate(
        tenant_id=tenant_id,
        rule_name=name,
        description="tenant-safe rule",
        scope="selected_devices",
        device_ids=["DEV-1"],
        property="power",
        condition=">",
        threshold=5.0,
        notification_channels=[NotificationChannel.EMAIL],
    )


@pytest.mark.asyncio
async def test_org_a_rules_do_not_appear_in_org_b():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rules_api, "_resolve_accessible_device_ids", _accessible_device_ids_for_tests)
    async with rule_session_ctx() as session:
        ctx_a = _tenant_request("SH00000001", role="plant_manager")
        ctx_b = _tenant_request("SH00000002", role="plant_manager")

        try:
            created = await rules_api.create_rule(
                rule_data=_rule_create_payload("Org A Rule"),
                request=ctx_a,
                db=session,
            )

            list_a = await rules_api.list_rules(
                request=ctx_a,
                status=None,
                device_id=None,
                page=1,
                page_size=20,
                db=session,
            )
            list_b = await rules_api.list_rules(
                request=ctx_b,
                status=None,
                device_id=None,
                page=1,
                page_size=20,
                db=session,
            )

            assert len(list_a.data) == 1
            assert list_a.data[0].rule_name == "Org A Rule"
            assert list_a.data[0].tenant_id == "SH00000001"
            assert list_b.data == []

            with pytest.raises(HTTPException) as exc:
                await rules_api.get_rule(UUID(str(created.data.rule_id)), request=ctx_b, db=session)
            assert exc.value.status_code == 404
        finally:
            monkeypatch.undo()


@pytest.mark.asyncio
async def test_get_update_delete_cannot_cross_tenant_boundaries():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rules_api, "_resolve_accessible_device_ids", _accessible_device_ids_for_tests)
    async with rule_session_ctx() as session:
        request_a = _tenant_request("SH00000001", role="plant_manager")
        request_b = _tenant_request("SH00000002", role="plant_manager")

        try:
            created = await rules_api.create_rule(
                rule_data=_rule_create_payload("Cross-Tenant Guard"),
                request=request_a,
                db=session,
            )
            rule_id = UUID(str(created.data.rule_id))

            with pytest.raises(HTTPException) as get_exc:
                await rules_api.get_rule(rule_id, request=request_b, db=session)
            assert get_exc.value.status_code == 404

            with pytest.raises(HTTPException) as update_exc:
                await rules_api.update_rule(
                    rule_id=rule_id,
                    rule_data=RuleUpdate(rule_name="Should Fail"),
                    request=request_b,
                    db=session,
                )
            assert update_exc.value.status_code == 404

            with pytest.raises(HTTPException) as delete_exc:
                await rules_api.delete_rule(rule_id=rule_id, request=request_b, db=session)
            assert delete_exc.value.status_code == 404

            remaining = await rules_api.get_rule(rule_id, request=request_a, db=session)
            assert remaining.data.rule_name == "Cross-Tenant Guard"
        finally:
            monkeypatch.undo()


@pytest.mark.asyncio
async def test_tenantless_internal_requests_fail_closed_for_rule_routes():
    async with rule_session_ctx() as session:
        request = _tenant_request(None, role="internal_service")

        with pytest.raises(HTTPException) as list_exc:
            await rules_api.list_rules(
                request=request,
                status=None,
                device_id=None,
                page=1,
                page_size=20,
                db=session,
            )
        assert list_exc.value.status_code == 403
        assert list_exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"

        with pytest.raises(HTTPException) as alert_exc:
            await alerts_api.list_alerts(
                request=request,
                device_id=None,
                rule_id=None,
                status=None,
                page=1,
                page_size=20,
                db=session,
            )
        assert alert_exc.value.status_code == 403
        assert alert_exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


@pytest.mark.asyncio
async def test_same_tenant_rule_update_and_delete_still_work():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rules_api, "_resolve_accessible_device_ids", _accessible_device_ids_for_tests)
    async with rule_session_ctx() as session:
        request = _tenant_request("SH00000001", role="plant_manager")

        try:
            created = await rules_api.create_rule(
                rule_data=_rule_create_payload("Original Name"),
                request=request,
                db=session,
            )
            rule_id = UUID(str(created.data.rule_id))

            updated = await rules_api.update_rule(
                rule_id=rule_id,
                rule_data=RuleUpdate(rule_name="Updated Name"),
                request=request,
                db=session,
            )
            assert updated.data.rule_name == "Updated Name"
            assert updated.data.tenant_id == "SH00000001"

            status_updated = await rules_api.update_rule_status(
                rule_id=rule_id,
                status_update=SimpleNamespace(status=RuleStatus.PAUSED),
                request=request,
                db=session,
            )
            assert status_updated.status == RuleStatus.PAUSED

            deleted = await rules_api.delete_rule(rule_id=rule_id, request=request, soft=True, db=session)
            assert deleted.rule_id == rule_id

            archived = await rules_api.get_rule(rule_id, request=request, db=session)
            assert archived.data.status == RuleStatus.ARCHIVED
            visible_rules = await rules_api.list_rules(
                request=request,
                status=None,
                device_id=None,
                page=1,
                page_size=20,
                db=session,
            )
            assert visible_rules.data == []
        finally:
            monkeypatch.undo()


@pytest.mark.asyncio
async def test_alert_routes_are_tenant_scoped_by_repository_construction():
    async with rule_session_ctx() as session:
        ctx_a = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        ctx_b = TenantContext(
            tenant_id="SH00000002",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        rule_service = RuleService(session, ctx_a)
        rule = await rule_service.create_rule(_rule_create_payload("Alert Rule", tenant_id="SH00000001"))

        alert_service = AlertService(session, ctx_a)
        await alert_service.create_alert(rule=rule, device_id="DEV-1", actual_value=9.0)

        alerts_a = await alerts_api.list_alerts(
            request=_tenant_request("SH00000001"),
            device_id=None,
            rule_id=None,
            status=None,
            page=1,
            page_size=20,
            db=session,
        )
        alerts_b = await alerts_api.list_alerts(
            request=_tenant_request("SH00000002"),
            device_id=None,
            rule_id=None,
            status=None,
            page=1,
            page_size=20,
            db=session,
        )
        assert len(alerts_a.data) == 1
        assert alerts_b.data == []

        alert_repo_b = AlertRepository(session, ctx_b)
        alert = await alert_repo_b.get_by_id(str(alerts_a.data[0].alert_id))
        assert alert is None


@pytest.mark.asyncio
async def test_activity_counts_and_summary_are_tenant_specific():
    async with rule_session_ctx() as session:
        ctx_a = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        ctx_b = TenantContext(
            tenant_id="SH00000002",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        rule_a = await RuleService(session, ctx_a).create_rule(_rule_create_payload("Rule A", tenant_id="SH00000001"))
        rule_b = await RuleService(session, ctx_b).create_rule(_rule_create_payload("Rule B", tenant_id="SH00000002"))

        await AlertService(session, ctx_a).create_alert(rule=rule_a, device_id="DEV-A", actual_value=9.0)
        await AlertService(session, ctx_b).create_alert(rule=rule_b, device_id="DEV-B", actual_value=7.0)

        unread_a = await alerts_api.get_unread_event_count(
            request=_tenant_request("SH00000001"),
            device_id=None,
            db=session,
        )
        unread_b = await alerts_api.get_unread_event_count(
            request=_tenant_request("SH00000002"),
            device_id=None,
            db=session,
        )
        summary_a = await alerts_api.get_activity_summary(request=_tenant_request("SH00000001"), db=session)
        summary_b = await alerts_api.get_activity_summary(request=_tenant_request("SH00000002"), db=session)

        assert unread_a.data["count"] == 2
        assert unread_b.data["count"] == 2
        assert summary_a.data["active_alerts"] == 1
        assert summary_b.data["active_alerts"] == 1
        assert summary_a.data["alerts_triggered"] == 1
        assert summary_b.data["alerts_triggered"] == 1
        assert summary_a.data["rules_created"] == 1
        assert summary_b.data["rules_created"] == 1


@pytest.mark.asyncio
async def test_mark_all_read_and_clear_history_only_affect_requesting_tenant():
    async with rule_session_ctx() as session:
        ctx_a = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        ctx_b = TenantContext(
            tenant_id="SH00000002",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        rule_a = await RuleService(session, ctx_a).create_rule(_rule_create_payload("Rule A", tenant_id="SH00000001"))
        rule_b = await RuleService(session, ctx_b).create_rule(_rule_create_payload("Rule B", tenant_id="SH00000002"))
        await AlertService(session, ctx_a).create_alert(rule=rule_a, device_id="DEV-A", actual_value=9.0)
        await AlertService(session, ctx_b).create_alert(rule=rule_b, device_id="DEV-B", actual_value=7.0)

        mark_result = await alerts_api.mark_all_events_read(
            request=_tenant_request("SH00000001"),
            device_id=None,
            db=session,
        )
        unread_a = await alerts_api.get_unread_event_count(
            request=_tenant_request("SH00000001"),
            device_id=None,
            db=session,
        )
        unread_b = await alerts_api.get_unread_event_count(
            request=_tenant_request("SH00000002"),
            device_id=None,
            db=session,
        )

        assert mark_result.data["updated"] == 2
        assert unread_a.data["count"] == 0
        assert unread_b.data["count"] == 2

        clear_result = await alerts_api.clear_event_history(
            request=_tenant_request("SH00000001"),
            device_id=None,
            db=session,
        )
        events_a = await alerts_api.list_activity_events(
            request=_tenant_request("SH00000001"),
            device_id=None,
            event_type=None,
            page=1,
            page_size=20,
            db=session,
        )
        events_b = await alerts_api.list_activity_events(
            request=_tenant_request("SH00000002"),
            device_id=None,
            event_type=None,
            page=1,
            page_size=20,
            db=session,
        )

        assert clear_result.data["deleted"] == 2
        assert events_a.data == []
        assert len(events_b.data) == 2


@pytest.mark.asyncio
async def test_same_tenant_alert_lifecycle_and_activity_events_work():
    async with rule_session_ctx() as session:
        ctx = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        request = _tenant_request("SH00000001")

        rule = await RuleService(session, ctx).create_rule(_rule_create_payload("Lifecycle Rule", tenant_id="SH00000001"))
        await AlertService(session, ctx).create_alert(rule=rule, device_id="DEV-A", actual_value=9.0)

        alert_list = await alerts_api.list_alerts(
            request=request,
            device_id=None,
            rule_id=None,
            status=None,
            page=1,
            page_size=20,
            db=session,
        )
        alert_id = UUID(str(alert_list.data[0].alert_id))

        acknowledged = await alerts_api.acknowledge_alert(
            alert_id=alert_id,
            payload=alerts_api.AlertAcknowledgeRequest(acknowledged_by="operator-1"),
            request=request,
            db=session,
        )
        resolved = await alerts_api.resolve_alert(
            alert_id=alert_id,
            request=request,
            db=session,
        )
        summary = await alerts_api.get_activity_summary(request=request, db=session)
        events = await alerts_api.list_activity_events(
            request=request,
            device_id=None,
            event_type=None,
            page=1,
            page_size=20,
            db=session,
        )

        assert acknowledged.data.status == "acknowledged"
        assert acknowledged.data.acknowledged_by == "operator-1"
        assert resolved.data.status == "resolved"
        assert summary.data["active_alerts"] == 0
        assert summary.data["alerts_triggered"] == 1
        assert summary.data["alerts_cleared"] == 1
        event_types = {event.event_type for event in events.data}
        assert "rule_triggered" in event_types
        assert "alert_acknowledged" in event_types
        assert "alert_resolved" in event_types


@pytest.mark.asyncio
async def test_rule_evaluation_side_effects_stay_within_request_tenant() -> None:
    async with rule_session_ctx() as session:
        ctx_a = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        ctx_b = TenantContext(
            tenant_id="SH00000002",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        rule_a = await RuleService(session, ctx_a).create_rule(
            RuleCreate(
                tenant_id="SH00000001",
                rule_name="Eval Rule A",
                description="tenant-safe rule",
                scope=RuleScope.ALL_DEVICES,
                device_ids=[],
                property="power",
                condition=">",
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
            )
        )
        rule_b = await RuleService(session, ctx_b).create_rule(
            RuleCreate(
                tenant_id="SH00000002",
                rule_name="Eval Rule B",
                description="tenant-safe rule",
                scope=RuleScope.ALL_DEVICES,
                device_ids=[],
                property="power",
                condition=">",
                threshold=5.0,
                notification_channels=[NotificationChannel.EMAIL],
            )
        )

        evaluator = RuleEvaluator(session, ctx_a)
        total, triggered, results = await evaluator.evaluate_telemetry(
            TelemetryPayload(
                device_id="DEV-1",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                schema_version="v1",
                enrichment_status="success",
                power=12.0,
            )
        )

        alerts_a, _ = await AlertRepository(session, ctx_a).list_alerts()
        alerts_b, _ = await AlertRepository(session, ctx_b).list_alerts()
        events_a, _ = await ActivityEventRepository(session, ctx_a).list_events()
        events_b, _ = await ActivityEventRepository(session, ctx_b).list_events()
        rule_a_after = await RuleRepository(session, ctx_a).get_by_id(str(rule_a.rule_id))
        rule_b_after = await RuleRepository(session, ctx_b).get_by_id(str(rule_b.rule_id))

        assert total == 1
        assert triggered == 1
        assert len(results) == 1
        assert len(alerts_a) == 1
        assert alerts_a[0].tenant_id == "SH00000001"
        assert alerts_a[0].rule_id == str(rule_a.rule_id)
        assert alerts_b == []
        assert any(event.event_type == "rule_triggered" and event.tenant_id == "SH00000001" for event in events_a)
        assert all(event.tenant_id == "SH00000001" for event in events_a)
        assert all(event.rule_id != str(rule_b.rule_id) for event in events_a)
        assert all(event.tenant_id == "SH00000002" for event in events_b)
        assert all(event.event_type != "rule_triggered" for event in events_b)
        assert rule_a_after is not None and rule_a_after.last_triggered_at is not None
        assert rule_b_after is not None and rule_b_after.last_triggered_at is None


@pytest.mark.asyncio
async def test_alert_service_rejects_cross_tenant_rule_side_effects() -> None:
    async with rule_session_ctx() as session:
        ctx_a = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )
        ctx_b = TenantContext(
            tenant_id="SH00000002",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        rule_b = await RuleService(session, ctx_b).create_rule(_rule_create_payload("Eval Rule B", tenant_id="SH00000002"))

        with pytest.raises(ValueError, match="tenant scope"):
            await AlertService(session, ctx_a).create_alert(rule=rule_b, device_id="DEV-1", actual_value=11.0)

        alerts_a, _ = await AlertRepository(session, ctx_a).list_alerts()
        alerts_b, _ = await AlertRepository(session, ctx_b).list_alerts()
        events_a, _ = await ActivityEventRepository(session, ctx_a).list_events()
        assert alerts_a == []
        assert alerts_b == []
        assert all(event.event_type != "rule_triggered" for event in events_a)


@pytest.mark.asyncio
async def test_concurrent_threshold_evaluations_emit_single_alert(tmp_path: Path) -> None:
    async with rule_file_session_ctx(tmp_path) as session_factory:
        ctx = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        async with session_factory() as setup_session:
            rule = await RuleService(setup_session, ctx).create_rule(
                RuleCreate(
                    tenant_id="SH00000001",
                    rule_name="Concurrent Rule",
                    description="dedupe duplicate concurrent triggers",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["DEV-1"],
                    property="power",
                    condition=">",
                    threshold=5.0,
                    notification_channels=[NotificationChannel.EMAIL],
                    cooldown_minutes=5,
                )
            )
            await setup_session.commit()

        telemetry = TelemetryPayload(
            device_id="DEV-1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            schema_version="v1",
            enrichment_status="success",
            power=12.0,
        )

        async def run_eval() -> tuple[int, int, list]:
            async with session_factory() as session:
                evaluator = RuleEvaluator(session, ctx)
                return await evaluator.evaluate_telemetry(telemetry)

        first, second = await asyncio.gather(run_eval(), run_eval())

        async with session_factory() as verify_session:
            alerts, _ = await AlertRepository(verify_session, ctx).list_alerts(device_id="DEV-1")
            stored_rule = await RuleRepository(verify_session, ctx).get_by_id(str(rule.rule_id))

        assert sorted([first[1], second[1]]) == [0, 1]
        assert len(alerts) == 1
        assert stored_rule is not None
        assert stored_rule.last_triggered_at is not None


@pytest.mark.asyncio
async def test_interval_cooldown_blocks_second_evaluation_without_error(tmp_path: Path) -> None:
    async with rule_file_session_ctx(tmp_path) as session_factory:
        ctx = TenantContext(
            tenant_id="SH00000001",
            user_id="svc:test",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        async with session_factory() as setup_session:
            await RuleService(setup_session, ctx).create_rule(
                RuleCreate(
                    tenant_id="SH00000001",
                    rule_name="Cooldown Rule",
                    description="avoid 500s while interval cooldown is active",
                    scope=RuleScope.SELECTED_DEVICES,
                    device_ids=["DEV-1"],
                    property="power",
                    condition=">",
                    threshold=5.0,
                    notification_channels=[NotificationChannel.EMAIL],
                    cooldown_minutes=5,
                )
            )

        telemetry = TelemetryPayload(
            device_id="DEV-1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            schema_version="v1",
            enrichment_status="success",
            power=12.0,
        )

        async with session_factory() as session:
            evaluator = RuleEvaluator(session, ctx)
            first = await evaluator.evaluate_telemetry(telemetry)

        async with session_factory() as session:
            evaluator = RuleEvaluator(session, ctx)
            second = await evaluator.evaluate_telemetry(telemetry)

        assert first[1] == 1
        assert second[1] == 0
