from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = REPO_ROOT / "services"
for path in (
    REPO_ROOT,
    SERVICES_ROOT,
    REPO_ROOT / "services" / "waste-analysis-service",
):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _load_module(module_name: str, relative_path: str):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_app_module(import_name: str, *, service_root: str):
    target_path = REPO_ROOT / service_root
    existing = sys.modules.get(import_name)
    if existing is not None and Path(getattr(existing, "__file__", "")).is_relative_to(target_path):
        return existing

    if service_root is not None:
        service_path = str(REPO_ROOT / service_root)
        if service_path in sys.path:
            sys.path.remove(service_path)
        sys.path.insert(0, service_path)
        for module_name_to_clear in list(sys.modules):
            if module_name_to_clear == "app" or module_name_to_clear.startswith("app."):
                sys.modules.pop(module_name_to_clear, None)

    return importlib.import_module(import_name)


def _assert_string_length(column, expected_length: int) -> None:
    assert getattr(column.type, "length", None) == expected_length


def test_device_service_tenant_columns_use_sh_length() -> None:
    device_models = _load_app_module(
        "app.models.device",
        service_root="services/device-service",
    )

    _assert_string_length(device_models.Device.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceShift.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.ParameterHealthConfig.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DevicePerformanceTrend.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceProperty.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceDashboardWidget.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceDashboardWidgetSetting.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.IdleRunningLog.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.MaintenanceLog.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceLiveState.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.WasteSiteConfig.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DashboardSnapshot.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.HardwareUnit.__table__.c.tenant_id, 10)
    _assert_string_length(device_models.DeviceHardwareInstallation.__table__.c.tenant_id, 10)


def test_reporting_service_tenant_columns_use_sh_length() -> None:
    energy_reports = _load_module(
        "reporting_energy_reports_hard_cut",
        "services/reporting-service/src/models/energy_reports.py",
    )
    scheduled_reports = _load_module(
        "reporting_scheduled_reports_hard_cut",
        "services/reporting-service/src/models/scheduled_reports.py",
    )
    settings = _load_module(
        "reporting_settings_hard_cut",
        "services/reporting-service/src/models/settings.py",
    )
    tenant_tariffs = _load_module(
        "reporting_tenant_tariffs_hard_cut",
        "services/reporting-service/src/models/tenant_tariffs.py",
    )

    _assert_string_length(energy_reports.EnergyReport.__table__.c.tenant_id, 10)
    _assert_string_length(scheduled_reports.ScheduledReport.__table__.c.tenant_id, 10)
    _assert_string_length(settings.TariffConfig.__table__.c.tenant_id, 10)
    _assert_string_length(settings.NotificationChannel.__table__.c.tenant_id, 10)
    _assert_string_length(tenant_tariffs.TenantTariff.__table__.c.tenant_id, 10)


def test_rule_engine_tenant_columns_use_sh_length() -> None:
    rule_models = _load_app_module(
        "app.models.rule",
        service_root="services/rule-engine-service",
    )

    _assert_string_length(rule_models.Rule.__table__.c.tenant_id, 10)
    _assert_string_length(rule_models.Alert.__table__.c.tenant_id, 10)
    _assert_string_length(rule_models.ActivityEvent.__table__.c.tenant_id, 10)


def test_waste_analysis_tenant_columns_use_sh_length() -> None:
    waste_models = _load_module(
        "waste_analysis_models_hard_cut",
        "services/waste-analysis-service/src/models/waste_jobs.py",
    )

    _assert_string_length(waste_models.WasteAnalysisJob.__table__.c.tenant_id, 10)
