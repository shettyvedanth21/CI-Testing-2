from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.services.idle_running import IdleRunningService
from app.services.shift import ShiftService


def test_shift_running_sample_uses_inverted_polarity_normalization():
    sample = {
        "timestamp": "2026-04-05T12:00:00+00:00",
        "power": -1500.0,
        "current": -6.0,
        "voltage": 230.0,
        "power_factor": -0.9,
        "polarity_mode": "inverted",
        "energy_flow_mode": "consumption_only",
    }

    assert ShiftService._is_running_sample(sample) is True


def test_idle_running_detect_device_state_uses_normalized_magnitude_thresholds():
    assert IdleRunningService.detect_device_state(current=2.0, voltage=230.0, threshold=5.0) == "idle"
    assert IdleRunningService.detect_device_state(current=8.0, voltage=230.0, threshold=5.0) == "running"


def test_idle_running_does_not_use_phase_diagnostics_as_business_current_voltage():
    mapped = IdleRunningService.map_telemetry(
        {
            "timestamp": "2026-04-05T12:00:00+00:00",
            "current_l1": 100.0,
            "current_l2": 101.0,
            "current_l3": 102.0,
            "voltage_l1": 230.0,
            "voltage_l2": 231.0,
            "voltage_l3": 229.0,
        }
    )

    assert mapped.current is None
    assert mapped.voltage is None
    assert mapped.current_field is None
    assert mapped.voltage_field is None


def test_idle_running_keeps_authoritative_aggregate_over_phase_diagnostics():
    mapped = IdleRunningService.map_telemetry(
        {
            "timestamp": "2026-04-05T12:00:00+00:00",
            "current": 10.0,
            "voltage": 230.0,
            "current_l1": 1000.0,
            "voltage_l1": 9999.0,
        }
    )

    assert mapped.current == 10.0
    assert mapped.voltage == 230.0
    assert mapped.current_field == "current"
    assert mapped.voltage_field == "voltage"
