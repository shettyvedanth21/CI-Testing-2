from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import types

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.shared.tenant_context import TenantContext


WASTE_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "waste-analysis-service"
if str(WASTE_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(WASTE_SERVICE_ROOT))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service")
os.environ.setdefault("REPORTING_SERVICE_URL", "http://reporting-service")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")

src_handlers_pkg = types.ModuleType("src.handlers")
src_handlers_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "handlers")]
sys.modules["src.handlers"] = src_handlers_pkg

src_tasks_pkg = types.ModuleType("src.tasks")
src_tasks_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "tasks")]
sys.modules["src.tasks"] = src_tasks_pkg

src_repositories_pkg = types.ModuleType("src.repositories")
src_repositories_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "repositories")]
sys.modules["src.repositories"] = src_repositories_pkg

src_schemas_pkg = types.ModuleType("src.schemas")
src_schemas_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "schemas")]
sys.modules["src.schemas"] = src_schemas_pkg

src_models_pkg = types.ModuleType("src.models")
src_models_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "models")]
sys.modules["src.models"] = src_models_pkg

src_storage_pkg = types.ModuleType("src.storage")
src_storage_pkg.__path__ = [str(WASTE_SERVICE_ROOT / "src" / "storage")]
sys.modules["src.storage"] = src_storage_pkg

pdf_builder_stub = types.ModuleType("src.pdf.builder")
pdf_builder_stub.generate_waste_pdf = lambda *args, **kwargs: ("waste-report.pdf", b"")  # noqa: E731
sys.modules["src.pdf.builder"] = pdf_builder_stub

fake_waste_task = types.ModuleType("src.tasks.waste_task")


async def _noop(*args, **kwargs):  # noqa: ANN001
    return None


fake_waste_task.run_waste_analysis = _noop
sys.modules["src.tasks.waste_task"] = fake_waste_task

fake_database = types.ModuleType("src.database")


async def _get_db():
    yield None


fake_database.get_db = _get_db
fake_database.engine = None
fake_database.SessionLocal = None
sys.modules["src.database"] = fake_database


def load_waste_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


waste_models_module = load_waste_module(
    "src.models.waste_jobs",
    WASTE_SERVICE_ROOT / "src" / "models" / "waste_jobs.py",
)
for model_name in ("Base", "WasteAnalysisJob", "WasteDeviceSummary", "WasteStatus"):
    setattr(src_models_pkg, model_name, getattr(waste_models_module, model_name))

waste_repository_module = load_waste_module(
    "src.repositories.waste_repository",
    WASTE_SERVICE_ROOT / "src" / "repositories" / "waste_repository.py",
)
src_repositories_pkg.WasteRepository = waste_repository_module.WasteRepository

waste_schemas_module = load_waste_module(
    "src.schemas.waste",
    WASTE_SERVICE_ROOT / "src" / "schemas" / "waste.py",
)
for schema_name in (
    "WasteAnalysisRunRequest",
    "WasteAnalysisRunResponse",
    "WasteDownloadResponse",
    "WasteHistoryItem",
    "WasteHistoryResponse",
    "WasteStatusResponse",
):
    setattr(src_schemas_pkg, schema_name, getattr(waste_schemas_module, schema_name))

waste_analysis_handler = load_waste_module(
    "src.handlers.waste_analysis",
    WASTE_SERVICE_ROOT / "src" / "handlers" / "waste_analysis.py",
)  # noqa: E402
from src.models.waste_jobs import Base  # noqa: E402
from src.repositories.waste_repository import WasteRepository  # noqa: E402


def _request(tenant_id: str | None, role: str = "org_admin"):
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_context=TenantContext(
                tenant_id=tenant_id,
                user_id="user-1",
                role=role,
                plant_ids=[],
                is_super_admin=False,
            )
        )
    )

async def _run_with_session(assertions):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await assertions(session)
    finally:
        await engine.dispose()


def test_waste_history_is_isolated_per_tenant():
    async def assertions(waste_session):
        repo_a = WasteRepository(
            waste_session,
            TenantContext(
                tenant_id="SH00000001",
                user_id="user-a",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        repo_b = WasteRepository(
            waste_session,
            TenantContext(
                tenant_id="SH00000002",
                user_id="user-b",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            ),
        )

        await repo_a.create_job(
            job_id="job-a",
            job_name="Waste Org A",
            scope="selected",
            device_ids=["DEVICE-A"],
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 1),
            granularity="daily",
        )
        await repo_b.create_job(
            job_id="job-b",
            job_name="Waste Org B",
            scope="selected",
            device_ids=["DEVICE-B"],
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 1),
            granularity="daily",
        )

        history_a = await waste_analysis_handler.get_history(request=_request("SH00000001"), limit=20, offset=0, db=waste_session)
        history_b = await waste_analysis_handler.get_history(request=_request("SH00000002"), limit=20, offset=0, db=waste_session)

        assert [item.job_id for item in history_a.items] == ["job-a"]
        assert [item.job_id for item in history_b.items] == ["job-b"]

    asyncio.run(_run_with_session(assertions))


def test_same_tenant_waste_history_still_works():
    async def assertions(waste_session):
        repo = WasteRepository(
            waste_session,
            TenantContext(
                tenant_id="SH00000001",
                user_id="user-a",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            ),
        )

        await repo.create_job(
            job_id="job-a1",
            job_name="Waste Run 1",
            scope="selected",
            device_ids=["DEVICE-A"],
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 1),
            granularity="daily",
        )
        await repo.create_job(
            job_id="job-a2",
            job_name="Waste Run 2",
            scope="selected",
            device_ids=["DEVICE-A"],
            start_date=date(2026, 4, 2),
            end_date=date(2026, 4, 2),
            granularity="daily",
        )

        history = await waste_analysis_handler.get_history(request=_request("SH00000001"), limit=20, offset=0, db=waste_session)

        assert [item.job_id for item in history.items] == ["job-a2", "job-a1"]

    asyncio.run(_run_with_session(assertions))


def test_waste_history_requires_tenant_scope():
    async def assertions(waste_session):
        with pytest.raises(HTTPException) as exc:
            await waste_analysis_handler.get_history(
                request=_request(None, role="internal_service"),
                limit=20,
                offset=0,
                db=waste_session,
            )

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "TENANT_SCOPE_REQUIRED"

    asyncio.run(_run_with_session(assertions))
