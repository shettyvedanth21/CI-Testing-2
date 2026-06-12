from __future__ import annotations

from contextlib import asynccontextmanager
from contextlib import contextmanager
import importlib.util
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


REPORTING_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "reporting-service"
if str(REPORTING_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTING_SERVICE_ROOT))

import src.handlers.settings as settings_handler
import src.handlers.tariffs as tariffs_handler
from src.models.settings import Base, NotificationChannel, TariffConfig
from src.models.tenant_tariffs import Base as TariffBase
from src.repositories.settings_repository import SettingsRepository
from src.repositories.tariff_repository import TariffRepository
from src.services.tariff_resolver import resolve_tariff
from src.services.tenant_scope import build_service_tenant_context
from services.shared.tenant_context import TenantContext


def _load_migration_module():
    migration_path = REPORTING_SERVICE_ROOT / "alembic" / "versions" / "005_tenant_scope_reporting_settings.py"
    spec = importlib.util.spec_from_file_location("reporting_settings_tenant_migration", migration_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load reporting settings migration")
    module = importlib.util.module_from_spec(spec)
    with _installed_alembic_imports():
        spec.loader.exec_module(module)
    return module


@contextmanager
def _installed_alembic_imports():
    original_path = list(sys.path)
    service_roots = (Path(__file__).resolve().parents[1] / "services").resolve()
    sys.path = [
        path
        for path in sys.path
        if not (
            (Path(path).resolve().parent == service_roots and (Path(path) / "alembic").exists())
            or Path(path).resolve() == REPORTING_SERVICE_ROOT
        )
    ]
    module = sys.modules.get("alembic")
    module_file = Path(getattr(module, "__file__", "") or "")
    removed_module = None
    if module is not None and service_roots in module_file.parents:
        removed_module = sys.modules.pop("alembic", None)
    try:
        yield
    finally:
        if removed_module is not None:
            sys.modules["alembic"] = removed_module
        sys.path = original_path


class _FakeAlembicOp:
    def __init__(self, bind):
        self._bind = bind
        self.added_columns: list[tuple[str, str]] = []
        self.altered_columns: list[tuple[str, str]] = []
        self.created_unique_constraints: list[tuple[str, str]] = []
        self.created_indexes: list[tuple[str, str]] = []

    def get_bind(self):
        return self._bind

    def add_column(self, table_name, column):
        self.added_columns.append((table_name, column.name))

    def bulk_insert(self, table, rows):
        if rows:
            self._bind.execute(insert(table), rows)

    def alter_column(self, table_name, column_name, **_: object):
        self.altered_columns.append((table_name, column_name))

    def create_unique_constraint(self, name, table_name, columns):
        self.created_unique_constraints.append((table_name, name))

    def create_index(self, name, table_name, columns, unique=False):
        self.created_indexes.append((table_name, name))


@asynccontextmanager
async def settings_session_ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(TariffBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def _request_with_ctx(tenant_id: str | None, role: str = "internal_service"):
    state = SimpleNamespace(
        tenant_context=TenantContext(
            tenant_id=tenant_id,
            user_id="svc:test",
            role=role,
            plant_ids=[],
            is_super_admin=False,
        )
    )
    return SimpleNamespace(state=state, headers={}, query_params={})


@pytest.mark.asyncio
async def test_tariff_settings_api_reads_from_authoritative_tenant_tariffs():
    async with settings_session_ctx() as settings_session:
        repo_a = TariffRepository(settings_session, build_service_tenant_context("ORG-A"))
        repo_b = TariffRepository(settings_session, build_service_tenant_context("ORG-B"))

        await repo_a.upsert_tariff(
            {
                "tenant_id": "ORG-A",
                "energy_rate_per_kwh": Decimal("8.5000"),
                "demand_charge_per_kw": Decimal("2.5"),
                "currency": "INR",
            }
        )
        await repo_b.upsert_tariff(
            {
                "tenant_id": "ORG-B",
                "energy_rate_per_kwh": Decimal("9.7500"),
                "demand_charge_per_kw": Decimal("4.5"),
                "currency": "USD",
            }
        )

        payload_a = await settings_handler.get_tariff(request=_request_with_ctx("ORG-A"), db=settings_session)
        payload_b = await settings_handler.get_tariff(request=_request_with_ctx("ORG-B"), db=settings_session)
        stored_rows = (
            await settings_session.execute(select(TariffConfig).order_by(TariffConfig.tenant_id.asc()))
        ).scalars().all()

        assert payload_a["rate"] == 8.5
        assert payload_a["currency"] == "INR"
        assert payload_b["rate"] == 9.75
        assert payload_b["currency"] == "USD"
        assert stored_rows == []


@pytest.mark.asyncio
async def test_settings_tariff_update_preserves_existing_extended_tariff_fields():
    async with settings_session_ctx() as settings_session:
        repo = TariffRepository(settings_session, build_service_tenant_context("ORG-A"))
        await repo.upsert_tariff(
            {
                "tenant_id": "ORG-A",
                "energy_rate_per_kwh": Decimal("8.5000"),
                "demand_charge_per_kw": Decimal("6.75"),
                "reactive_penalty_rate": Decimal("1.25"),
                "fixed_monthly_charge": Decimal("99"),
                "power_factor_threshold": Decimal("0.85"),
                "currency": "INR",
            }
        )

        payload = settings_handler.TariffUpsertRequest(rate=Decimal("10.0000"), currency="USD")
        await settings_handler.upsert_tariff(payload=payload, request=_request_with_ctx("ORG-A"), db=settings_session)

        updated = await repo.get_tariff("ORG-A")
        assert updated is not None
        assert updated.energy_rate_per_kwh == 10.0
        assert updated.currency == "USD"
        assert updated.demand_charge_per_kw == 6.75
        assert updated.reactive_penalty_rate == 1.25
        assert updated.fixed_monthly_charge == 99.0
        assert updated.power_factor_threshold == 0.85


@pytest.mark.asyncio
async def test_notification_recipients_are_isolated_per_tenant():
    async with settings_session_ctx() as settings_session:
        repo_a = SettingsRepository(settings_session, build_service_tenant_context("ORG-A"))
        repo_b = SettingsRepository(settings_session, build_service_tenant_context("ORG-B"))

        await repo_a.add_email_channel("alerts-a@example.com")
        await repo_b.add_email_channel("alerts-b@example.com")

        channels_a = await repo_a.list_active_channels("email")
        channels_b = await repo_b.list_active_channels("email")

        assert [row.value for row in channels_a] == ["alerts-a@example.com"]
        assert [row.value for row in channels_b] == ["alerts-b@example.com"]


@pytest.mark.asyncio
async def test_settings_tariff_resolution_requires_tenant_scope():
    with pytest.raises(ValueError, match="Tenant scope is required"):
        await resolve_tariff(object(), None)


@pytest.mark.asyncio
async def test_settings_endpoints_fail_closed_without_tenant_scope():
    request = _request_with_ctx(None)

    with pytest.raises(HTTPException) as exc:
        await settings_handler.get_tariff(request=request, db=object())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"

    with pytest.raises(HTTPException) as notifications_exc:
        await settings_handler.get_notifications(request=request, db=object())

    assert notifications_exc.value.status_code == 403
    assert notifications_exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


@pytest.mark.asyncio
async def test_legacy_tariff_route_rejects_cross_tenant_access():
    request = _request_with_ctx("ORG-A", role="plant_manager")

    with pytest.raises(HTTPException) as exc:
        await tariffs_handler.get_tariff(tenant_id="ORG-B", request=request, db=object())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "TENANT_SCOPE_MISMATCH"


def test_reporting_settings_migration_backfills_global_rows_per_tenant():
    migration = _load_migration_module()

    tariffs = migration._build_tariff_backfill_rows(
        ["ORG-A", "ORG-B"],
        [
            {
                "id": 1,
                "rate": Decimal("8.5000"),
                "currency": "INR",
                "updated_at": datetime(2026, 4, 1, 10, 0, 0),
                "updated_by": "legacy-admin",
            },
            {
                "id": 2,
                "rate": Decimal("9.0000"),
                "currency": "USD",
                "updated_at": datetime(2026, 4, 2, 10, 0, 0),
                "updated_by": "ignored-row",
            },
        ],
    )
    channels = migration._build_notification_backfill_rows(
        ["ORG-A", "ORG-B"],
        [
            {
                "id": 1,
                "channel_type": "email",
                "value": "Ops@Example.com ",
                "is_active": False,
                "created_at": datetime(2026, 4, 1, 9, 0, 0),
            },
            {
                "id": 2,
                "channel_type": "email",
                "value": "ops@example.com",
                "is_active": True,
                "created_at": datetime(2026, 4, 2, 9, 0, 0),
            },
        ],
    )

    assert tariffs == [
        {
            "tenant_id": "ORG-A",
            "rate": Decimal("8.5000"),
            "currency": "INR",
            "updated_at": datetime(2026, 4, 1, 10, 0, 0),
            "updated_by": "legacy-admin",
        },
        {
            "tenant_id": "ORG-B",
            "rate": Decimal("8.5000"),
            "currency": "INR",
            "updated_at": datetime(2026, 4, 1, 10, 0, 0),
            "updated_by": "legacy-admin",
        },
    ]
    assert channels == [
        {
            "tenant_id": "ORG-A",
            "channel_type": "email",
            "value": "ops@example.com",
            "is_active": True,
            "created_at": datetime(2026, 4, 1, 9, 0, 0),
        },
        {
            "tenant_id": "ORG-B",
            "channel_type": "email",
            "value": "ops@example.com",
            "is_active": True,
            "created_at": datetime(2026, 4, 1, 9, 0, 0),
        },
    ]


def test_reporting_settings_migration_is_idempotent_for_partially_applied_live_schema():
    migration = _load_migration_module()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    Table(
        "organizations",
        metadata,
        Column("id", String(50), primary_key=True),
        Column("created_at", DateTime, nullable=False),
    )
    Table(
        "tariff_config",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("rate", Numeric(10, 4), nullable=False),
        Column("currency", String(10), nullable=False),
        Column("updated_at", DateTime, nullable=False),
        Column("updated_by", String(100), nullable=True),
        Column("tenant_id", String(50), nullable=False, unique=True),
    )
    Table(
        "notification_channels",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("channel_type", String(20), nullable=False),
        Column("value", String(255), nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("tenant_id", String(50), nullable=False),
    )

    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_notification_channels_tenant_type_value "
            "ON notification_channels (tenant_id, channel_type, value)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_notification_channels_tenant_channel_active "
            "ON notification_channels (tenant_id, channel_type, is_active)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_tariff_config_tenant_id ON tariff_config (tenant_id)"
        )
        conn.exec_driver_sql(
            """
            INSERT INTO organizations (id, created_at)
            VALUES
                ('ORG-A', '2026-04-01 00:00:00'),
                ('ORG-B', '2026-04-01 00:00:00')
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO tariff_config (rate, currency, updated_at, updated_by, tenant_id)
            VALUES (8.5, 'INR', '2026-04-01 10:00:00', 'svc', 'ORG-A')
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO notification_channels (channel_type, value, is_active, created_at, tenant_id)
            VALUES ('email', 'ops@example.com', 1, '2026-04-01 10:00:00', 'ORG-A')
            """
        )

        fake_op = _FakeAlembicOp(conn)
        original_op = migration.op
        migration.op = fake_op
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

        tariff_rows = conn.exec_driver_sql(
            "SELECT tenant_id, rate, currency FROM tariff_config ORDER BY tenant_id"
        ).fetchall()
        channel_rows = conn.exec_driver_sql(
            "SELECT tenant_id, channel_type, value FROM notification_channels ORDER BY tenant_id, value"
        ).fetchall()

    engine.dispose()

    assert fake_op.added_columns == []
    assert fake_op.altered_columns == []
    assert tariff_rows == [("ORG-A", 8.5, "INR")]
    assert channel_rows == [("ORG-A", "email", "ops@example.com")]


@pytest.mark.asyncio
async def test_cross_tenant_disable_does_not_remove_other_tenant_channel():
    async with settings_session_ctx() as settings_session:
        repo_a = SettingsRepository(settings_session, build_service_tenant_context("ORG-A"))
        repo_b = SettingsRepository(settings_session, build_service_tenant_context("ORG-B"))

        channel_a = await repo_a.add_email_channel("shared@example.com")
        await repo_b.add_email_channel("shared@example.com")

        deleted = await repo_a.disable_email_channel(channel_a.id)
        channels_a = await repo_a.list_active_channels("email")
        channels_b = await repo_b.list_active_channels("email")
        all_rows = (await settings_session.execute(select(NotificationChannel))).scalars().all()

        assert deleted is True
        assert channels_a == []
        assert [row.value for row in channels_b] == ["shared@example.com"]
        assert len(all_rows) == 2


@pytest.mark.asyncio
async def test_notification_settings_endpoint_returns_only_request_tenant_recipients():
    async with settings_session_ctx() as settings_session:
        repo_a = SettingsRepository(settings_session, build_service_tenant_context("ORG-A"))
        repo_b = SettingsRepository(settings_session, build_service_tenant_context("ORG-B"))

        await repo_a.add_email_channel("alerts-a@example.com")
        await repo_b.add_email_channel("alerts-b@example.com")

        payload_a = await settings_handler.get_notifications(request=_request_with_ctx("ORG-A"), db=settings_session)
        payload_b = await settings_handler.get_notifications(request=_request_with_ctx("ORG-B"), db=settings_session)

        assert payload_a["email"] == [
            {"id": payload_a["email"][0]["id"], "value": "alerts-a@example.com", "is_active": True}
        ]
        assert payload_b["email"] == [
            {"id": payload_b["email"][0]["id"], "value": "alerts-b@example.com", "is_active": True}
        ]
