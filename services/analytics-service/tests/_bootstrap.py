from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def bootstrap_test_imports() -> None:
    here = Path(__file__).resolve()
    search_roots = [here] + list(here.parents)
    repo_root = None
    for candidate in search_roots:
        if (candidate / "services").exists():
            repo_root = candidate
            break
    if repo_root is None:
        repo_root = here.parents[min(3, len(here.parents) - 1)]

    for path in (repo_root, repo_root / "services", repo_root / "services/analytics-service"):
        resolved = str(path)
        if resolved not in sys.path:
            sys.path.insert(0, resolved)

    if "aioboto3" not in sys.modules:
        fake_aioboto3 = types.ModuleType("aioboto3")

        class _FakeSession:
            def __init__(self, *args, **kwargs):
                pass

        fake_aioboto3.Session = _FakeSession
        sys.modules["aioboto3"] = fake_aioboto3

    shared_tenant_context = importlib.import_module("shared.tenant_context")
    services_pkg = sys.modules.setdefault("services", types.ModuleType("services"))
    if not hasattr(services_pkg, "__path__"):
        services_pkg.__path__ = [str(repo_root / "services")]

    services_shared_pkg = sys.modules.setdefault("services.shared", types.ModuleType("services.shared"))
    if not hasattr(services_shared_pkg, "__path__"):
        services_shared_pkg.__path__ = [str(repo_root / "services" / "shared")]

    services_pkg.shared = services_shared_pkg
    services_shared_pkg.tenant_context = shared_tenant_context
    sys.modules["services.shared.tenant_context"] = shared_tenant_context

    shared_job_context = importlib.import_module("shared.job_context")
    services_shared_pkg.job_context = shared_job_context
    sys.modules["services.shared.job_context"] = shared_job_context

    shared_startup_contract = importlib.import_module("shared.startup_contract")
    services_shared_pkg.startup_contract = shared_startup_contract
    sys.modules["services.shared.startup_contract"] = shared_startup_contract

    shared_telemetry_coverage = importlib.import_module("shared.telemetry_coverage")
    services_shared_pkg.telemetry_coverage = shared_telemetry_coverage
    sys.modules["services.shared.telemetry_coverage"] = shared_telemetry_coverage
