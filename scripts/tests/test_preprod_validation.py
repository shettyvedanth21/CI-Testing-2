from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "preprod_validation.py"
SPEC = importlib.util.spec_from_file_location("preprod_validation", MODULE_PATH)
assert SPEC and SPEC.loader
preprod_validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preprod_validation
SPEC.loader.exec_module(preprod_validation)


def _config(tmp_path: Path, *, mode: str = "full-validation") -> preprod_validation.RunnerConfig:
    return preprod_validation.RunnerConfig(
        mode=mode,
        stop_on_first_defect=False,
        artifacts_dir=tmp_path / "artifacts",
        cert_python=sys.executable,
        auth_url="http://localhost:8090",
        device_url="http://localhost:8000",
        data_url="http://localhost:8081",
        rule_url="http://localhost:8002",
        reporting_url="http://localhost:8085",
        analytics_url="http://localhost:8003",
        waste_url="http://localhost:8087",
        energy_url="http://localhost:8010",
        ui_url="http://localhost:3000",
        super_admin_email="manash.ray@cittagent.com",
        super_admin_password="Shivex@2706",
        super_admin_full_name="Shivex Super-Admin",
        live_org_admin_email="vedanth.shetty@cittagent.com",
        live_org_admin_password="zaqmlp123",
        seed_password="Validate123!",
        http_timeout=10.0,
        reset_stack=False,
    )


def test_classify_failure_marks_import_errors_as_harness_issue(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path))
    try:
        assert (
            runner._classify_failure(
                "python -m pytest tests/test_example.py",
                "ImportError: cannot import name 'op' from 'alembic'",
            )
            == "validation harness issue"
        )
    finally:
        runner.close()


def test_classify_failure_marks_connectivity_as_environment_issue(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path))
    try:
        assert (
            runner._classify_failure(
                "health-verification",
                "http://localhost:8090/health did not become healthy within 120s (Connection refused)",
            )
            == "environment/data issue"
        )
    finally:
        runner.close()


def test_recommendation_requires_full_run_for_go(tmp_path: Path) -> None:
    quick_runner = preprod_validation.PreprodValidationRunner(_config(tmp_path / "quick", mode="current-live"))
    full_runner = preprod_validation.PreprodValidationRunner(_config(tmp_path / "full", mode="full-validation"))
    try:
        for runner in (quick_runner, full_runner):
            for item_id, item in runner.checklist.items():
                if item_id == "final_go_no_go":
                    continue
                runner.mark_pass(item_id, f"{item.title} passed.")

        quick = quick_runner._recommendation()
        full = full_runner._recommendation()

        assert quick["decision"] == "NO-GO"
        assert "Quick gate does not execute the full release checklist." == quick["reason"]
        assert full["decision"] == "GO"
    finally:
        quick_runner.close()
        full_runner.close()


def test_build_report_leaves_release_decision_not_executed_for_quick_gate_without_failures(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    try:
        for item_id, item in runner.checklist.items():
            if item_id == "final_go_no_go":
                continue
            runner.mark_pass(item_id, f"{item.title} passed.")

        report = runner.build_report()
        final_gate = next(item for item in report["validation_results"] if item["item_id"] == "final_go_no_go")

        assert report["production_recommendation"]["decision"] == "NO-GO"
        assert final_gate["status"] == "NOT_EXECUTED"
        assert final_gate["evidence_summary"] == "Release GO / NO-GO remains reserved for full validation mode."
    finally:
        runner.close()


def test_build_report_includes_required_sections(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path))
    try:
        runner.mark_pass("fresh_reset_sanity", "Fresh reset sanity passed.")
        report = runner.build_report()

        assert set(report) >= {
            "validation_setup",
            "findings",
            "fixes_applied",
            "validation_results",
            "logs_review",
            "production_recommendation",
            "follow_ups",
            "commands",
            "generated_at",
        }
        assert any(item["item_id"] == "fresh_reset_sanity" for item in report["validation_results"])
    finally:
        runner.close()


def test_make_config_maps_full_reset_to_full_validation_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preprod_validation, "ARTIFACTS_ROOT", tmp_path)
    config = preprod_validation.make_config(argparse.Namespace(mode="full-reset", stop_on_first_defect=True))

    assert config.mode == "full-validation"
    assert config.reset_stack is True
    assert config.stop_on_first_defect is True


def test_full_reset_purges_standalone_simulators_before_compose_reset(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str]):
        calls.append((name, command))
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(tmp_path / f"{name}.stdout"),
            stderr_path=str(tmp_path / f"{name}.stderr"),
        )

    try:
        runner.config.reset_stack = True
        runner.run_command = _fake_run_command  # type: ignore[method-assign]

        runner.reset_stack_if_requested()

        assert calls == [
            ("simulator-purge", ["./scripts/simulatorctl.sh", "purge"]),
            ("docker-compose-down", ["docker", "compose", "down", "-v", "--remove-orphans"]),
            ("docker-compose-up", ["docker", "compose", "up", "-d", "--build"]),
        ]
        assert runner.reset_steps_performed == [
            "./scripts/simulatorctl.sh purge",
            "docker compose down -v --remove-orphans",
            "docker compose up -d --build",
        ]
    finally:
        runner.close()


def test_full_validation_runs_hardware_and_error_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="full-validation"))
    calls: list[str] = []

    def _fake_run_command(name: str, command: list[str], env=None, **_kwargs):  # noqa: ANN001
        calls.append(name)
        stdout_path = tmp_path / f"{name}.stdout"
        stderr_path = tmp_path / f"{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        assert "Hardware lifecycle regression tests" in calls
        assert "Hardware integrity regression tests" in calls
        assert "Hardware error-handling regression tests" in calls
        assert runner.checklist["hardware_lifecycle"].status == "PASS"
        assert runner.checklist["hardware_integrity"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_entitlement_gating_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        entitlement_calls = {name: command for name, command in calls}
        assert "tests/test_feature_entitlement_gate_contract.py" in " ".join(
            entitlement_calls["Entitlement contract regression tests"]
        )
        assert "test_shared_middleware_rejects_stale_tenant_entitlements_version" in " ".join(
            entitlement_calls["Auth entitlement revocation regression tests"]
        )
        assert "test_org_admin_cannot_read_waste_config_without_waste_analysis_entitlement" in " ".join(
            entitlement_calls["Device entitlement config regression tests"]
        )
        assert runner.checklist["role_scoping"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_platform_maintenance_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        maintenance_calls = {name: command for name, command in calls}
        assert "tests/test_platform_maintenance_status.py" in " ".join(
            maintenance_calls["Platform maintenance status regression tests"]
        )
        assert "test_platform_maintenance_update_can_switch_to_broadcast_all" in " ".join(
            maintenance_calls["Platform maintenance admin regression tests"]
        )
        assert "test_current_banner_query_honors_selected_vs_broadcast_targeting" in " ".join(
            maintenance_calls["Platform maintenance delivery regression tests"]
        )
        assert runner.checklist["logs_runtime_stability"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_simulatorctl_startup_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        simulator_calls = [command for name, command in calls if name == "Simulatorctl startup regression tests"]
        assert len(simulator_calls) == 1
        joined = " ".join(simulator_calls[0])
        assert "scripts/tests/test_simulatorctl.py" in joined
        assert "tools/device-simulator/tests/test_credential_bootstrap.py" in joined
        assert "tools/device-simulator/tests/test_provisioning_bundle.py" in joined
        assert "test_send_device_heartbeat_uses_signed_internal_headers" in joined
        assert runner.checklist["device_onboarding"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
        assert runner.checklist["logs_runtime_stability"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_device_onboarding_id_allocation_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        allocation_calls = [
            command for name, command in calls if name == "Device onboarding ID allocation regression tests"
        ]
        assert len(allocation_calls) == 1
        joined = " ".join(allocation_calls[0])
        assert "test_generated_device_id_repairs_stale_sequence_after_existing_conflicts" in joined
        assert "test_device_id_sequences_increment_per_prefix" in joined
        assert runner.checklist["device_onboarding"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_deploy_recovery_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        deploy_calls = [command for name, command in calls if name == "Deploy recovery regression tests"]
        assert len(deploy_calls) == 1
        joined = " ".join(deploy_calls[0])
        assert "npm exec -- tsx --test" in joined
        assert "tests/unit/deployRecovery.test.ts" in joined
        assert "tests/unit/authBootstrap.test.ts" in joined
        assert "tests/unit/apiFetch.auth-recovery.test.ts" in joined
        assert runner.checklist["error_handling"].status == "PASS"
        assert runner.checklist["logs_runtime_stability"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_machine_activity_history_resilience_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        activity_calls = [command for name, command in calls if name == "Machine activity-history resilience regression tests"]
        assert len(activity_calls) == 1
        joined = " ".join(activity_calls[0])
        assert "npm exec -- tsx --test" in joined
        assert "tests/unit/activityHistoryResilience.test.ts" in joined
        assert runner.checklist["machine_detail_page"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_machine_dashboard_latency_guard_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        backend_calls = [command for name, command in calls if name == "Machine dashboard bootstrap latency guard regression tests"]
        frontend_calls = [command for name, command in calls if name == "Machine detail bootstrap frontend contract tests"]
        assert len(backend_calls) == 1
        assert len(frontend_calls) == 1
        assert "services/device-service/tests/test_dashboard_bootstrap_latency_guard.py" in " ".join(backend_calls[0])
        assert "services/device-service/tests/test_dashboard_tariff_cache.py" in " ".join(backend_calls[0])
        assert "npm exec -- tsx --test" in " ".join(frontend_calls[0])
        assert "tests/unit/machineDetailLoadContract.test.ts" in " ".join(frontend_calls[0])
        assert runner.checklist["machine_detail_page"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
    finally:
        runner.close()


def test_targeted_suites_include_analytics_long_running_truthfulness_regressions(tmp_path: Path) -> None:
    runner = preprod_validation.PreprodValidationRunner(_config(tmp_path, mode="quick-gate"))
    calls: list[tuple[str, list[str]]] = []

    def _fake_run_command(name: str, command: list[str], env=None, cwd=None, **_kwargs):  # noqa: ANN001
        calls.append((name, command))
        stdout_path = tmp_path / f"{len(calls)}-{name}.stdout"
        stderr_path = tmp_path / f"{len(calls)}-{name}.stderr"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return preprod_validation.CommandResult(
            name=name,
            command=" ".join(command),
            status="PASS",
            returncode=0,
            duration_seconds=0.0,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    try:
        runner.run_command = _fake_run_command  # type: ignore[method-assign]
        runner.run_isolation_and_targeted_suites()

        backend_calls = [command for name, command in calls if name == "Analytics long-running truthfulness backend regression tests"]
        frontend_calls = [command for name, command in calls if name == "Analytics long-running truthfulness frontend regression tests"]
        assert len(backend_calls) == 1
        assert len(frontend_calls) == 1
        assert "services/analytics-service/tests/unit/test_job_status_estimator.py" in " ".join(backend_calls[0])
        assert "services/analytics-service/tests/unit/test_job_status_route_payload.py" in " ".join(backend_calls[0])
        assert "npm exec -- tsx --test" in " ".join(frontend_calls[0])
        assert "tests/unit/analyticsAsyncProgressTruthfulness.test.ts" in " ".join(frontend_calls[0])
        assert runner.checklist["analytics"].status == "PASS"
        assert runner.checklist["error_handling"].status == "PASS"
    finally:
        runner.close()
