from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "certify_release_contracts.py"
SPEC = importlib.util.spec_from_file_location("certify_release_contracts", MODULE_PATH)
assert SPEC and SPEC.loader
certify_release_contracts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = certify_release_contracts
SPEC.loader.exec_module(certify_release_contracts)


def _args(**overrides):
    defaults = {
        "mode": "thorough",
        "skip_live_browser": False,
        "skip_compose_build": False,
        "allow_blocked": False,
        "strict_release_gate": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_strict_release_gate_requires_full_mode_and_live_prerequisites():
    args = _args(mode="quick", skip_live_browser=True, allow_blocked=True)

    failures = certify_release_contracts.strict_gate_prerequisite_failures(
        args,
        live_browser_status=(False, "missing live browser env"),
        live_stack_status=(False, "live stack auth failed"),
    )

    assert "Strict release gate requires --mode thorough." in failures
    assert "Strict release gate cannot skip the live browser certification step." in failures
    assert "Strict release gate cannot be combined with --allow-blocked." in failures
    assert "missing live browser env" in failures
    assert "live stack auth failed" in failures


def test_non_strict_gate_allows_missing_live_prerequisites():
    args = _args(strict_release_gate=False, mode="quick", skip_live_browser=True, allow_blocked=True)

    failures = certify_release_contracts.strict_gate_prerequisite_failures(
        args,
        live_browser_status=(False, "missing live browser env"),
        live_stack_status=(False, "live stack auth failed"),
    )

    assert failures == []


def test_blocked_is_not_allowed_in_strict_release_gate():
    assert certify_release_contracts.blocked_is_allowed(_args(strict_release_gate=False, allow_blocked=True)) is True
    assert certify_release_contracts.blocked_is_allowed(_args(strict_release_gate=True, allow_blocked=True)) is False


def test_prepare_certification_env_applies_seed_output():
    env = certify_release_contracts.prepare_certification_env(
        {
            "strict_env": {
                "VALIDATE_PRIMARY_TENANT_ID": "SH00000001",
                "VALIDATE_SECONDARY_TENANT_ID": "SH00000002",
                "CERTIFY_TENANT_ID": "SH00000001",
                "CERTIFY_PM_EMAIL": "pm@example.com",
                "CERTIFY_PM_PASSWORD": "secret",
            }
        }
    )

    assert env["VALIDATE_PRIMARY_TENANT_ID"] == "SH00000001"
    assert env["VALIDATE_SECONDARY_TENANT_ID"] == "SH00000002"
    assert env["CERTIFY_TENANT_ID"] == "SH00000001"
    assert env["CERTIFY_PM_EMAIL"] == "pm@example.com"
    assert env["CERTIFY_PM_PASSWORD"] == "secret"
    assert "VALIDATE_CERTIFICATION_SEED_JSON" in env


def test_prepare_certification_env_without_seed_leaves_live_browser_org_scope_unset(monkeypatch):
    monkeypatch.delenv("CERTIFY_TENANT_ID", raising=False)
    env = certify_release_contracts.prepare_certification_env()

    assert "CERTIFY_TENANT_ID" not in env


def test_resolve_certification_python_prefers_env_override_with_required_deps(monkeypatch):
    monkeypatch.setenv("CERTIFY_PYTHON", "/tmp/cert-python")
    monkeypatch.setattr(certify_release_contracts, "REPO_ROOT", Path("/tmp/repo"))
    monkeypatch.setattr(certify_release_contracts.shutil, "which", lambda name: "/usr/bin/python3")

    def fake_run(command, **kwargs):  # noqa: ANN001
        executable = command[0]
        return SimpleNamespace(returncode=0 if executable == "/tmp/cert-python" else 1)

    monkeypatch.setattr(certify_release_contracts.subprocess, "run", fake_run)

    assert certify_release_contracts.resolve_certification_python() == "/tmp/cert-python"


def test_resolve_certification_python_falls_back_to_shell_python_when_runtime_lacks_deps(monkeypatch):
    monkeypatch.delenv("CERTIFY_PYTHON", raising=False)
    monkeypatch.setattr(certify_release_contracts, "REPO_ROOT", Path("/tmp/repo"))
    monkeypatch.setattr(certify_release_contracts.shutil, "which", lambda name: "/usr/bin/python3")
    monkeypatch.setattr(certify_release_contracts.sys, "executable", "/opt/homebrew/bin/python3.14")

    def fake_run(command, **kwargs):  # noqa: ANN001
        executable = command[0]
        return SimpleNamespace(returncode=0 if executable == "/usr/bin/python3" else 1)

    monkeypatch.setattr(certify_release_contracts.subprocess, "run", fake_run)

    assert certify_release_contracts.resolve_certification_python() == "/usr/bin/python3"
