from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "ci_broad_validation.py"

spec = importlib.util.spec_from_file_location("ci_broad_validation", MODULE_PATH)
assert spec is not None and spec.loader is not None
ci_broad_validation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci_broad_validation
spec.loader.exec_module(ci_broad_validation)


def test_suite_manifest_matches_ci_md() -> None:
    ci_names = ci_broad_validation.parse_ci_suite_names()
    manifest_names = [suite.name for suite in ci_broad_validation.SUITES]
    assert manifest_names == ci_names
    assert len(manifest_names) == 22


def test_suite_matrix_categories_cover_all_suites_without_overlap() -> None:
    categories = {
        "python": ci_broad_validation.suites_for_category("python"),
        "browser": ci_broad_validation.suites_for_category("browser"),
        "stateful": ci_broad_validation.suites_for_category("stateful"),
    }
    all_names = [suite.name for suites in categories.values() for suite in suites]
    assert sorted(all_names) == sorted(suite.name for suite in ci_broad_validation.SUITES)
    assert len(all_names) == len(set(all_names))
    assert [suite.name for suite in categories["browser"]] == ["Premium Feature Gating"]
    assert [suite.name for suite in categories["stateful"]] == ["Database Integrity And Concurrency"]


def test_each_suite_has_at_least_one_command() -> None:
    for suite in ci_broad_validation.SUITES:
        assert suite.commands
        for command in suite.commands:
            assert command.argv
            assert command.label


def test_command_env_keeps_inherited_service_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_HOST", "host.docker.internal")
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6379/2")
    monkeypatch.setenv("PYTHONPATH", "/existing/pythonpath")

    cwd = REPO_ROOT / "services" / "data-service"
    env = ci_broad_validation.command_env({}, cwd=cwd)

    assert env["MYSQL_HOST"] == "host.docker.internal"
    assert env["REDIS_URL"] == "redis://cache.internal:6379/2"
    assert env["PYTHONPATH"].split(os.pathsep)[:3] == [
        str(cwd),
        str(REPO_ROOT / "services"),
        str(REPO_ROOT),
    ]
    assert env["PYTHONPATH"].split(os.pathsep)[-1] == "/existing/pythonpath"


def test_command_env_allows_command_specific_override(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_HOST", "host.docker.internal")

    cwd = REPO_ROOT / "services" / "reporting-service"
    env = ci_broad_validation.command_env({"MYSQL_HOST": "127.0.0.1"}, cwd=cwd)

    assert env["MYSQL_HOST"] == "127.0.0.1"


def test_hotfix_regression_tests_are_wired_into_ci_tiers() -> None:
    suites_by_name = {suite.name: suite for suite in ci_broad_validation.SUITES}

    analytics_commands = [
        " ".join(command.argv) for command in suites_by_name["Analytics Job Flow"].commands
    ]
    assert any("tests/unit/test_main_startup_metrics.py" in command for command in analytics_commands)

    waste_commands = [
        " ".join(command.argv) for command in suites_by_name["Waste Analysis"].commands
    ]
    assert any("tests/test_waste_worker_queue_migration_api.py" in command for command in waste_commands)
    assert any("tests/test_migration_guard_invocation.py" in command for command in waste_commands)

    browser_commands = [
        " ".join(command.argv) for command in suites_by_name["Premium Feature Gating"].commands
    ]
    assert any("tests/unit/machineDetailKpiState.test.ts" in command for command in browser_commands)
    assert any("tests/e2e/machine-dashboard-bootstrap-recovery.spec.js" in command for command in browser_commands)

    stateful_commands = suites_by_name["Database Integrity And Concurrency"].commands
    assert any(
        "tests/test_migration_guard_bootstrap.py" in " ".join(command.argv)
        and command.env.get("MYSQL_ROOT_PASSWORD") == "root"
        for command in stateful_commands
    )
