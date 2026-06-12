import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.services.waste_engine import compute_device_waste


def _synthetic_rows(n: int = 60) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(minutes=n)
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        out.append(
            {
                "timestamp": ts.isoformat(),
                "power": 2500.0 + (i % 5) * 50.0,  # W
                "current": 12.0 + (i % 4) * 0.4,
                "voltage": 230.0,
                "power_factor": 0.95,
            }
        )
    return out


@pytest.mark.scale
def test_waste_compute_scales_to_5000_devices():
    if os.getenv("RUN_SCALE_BENCHMARK", "0") != "1":
        pytest.skip("Set RUN_SCALE_BENCHMARK=1 to run high-scale benchmark")

    device_count = int(os.getenv("WASTE_SCALE_DEVICES", "5000"))
    rows = _synthetic_rows(90)
    shifts = [{"day_of_week": datetime.now(timezone.utc).weekday(), "shift_start": "08:00", "shift_end": "18:00"}]

    t0 = time.perf_counter()
    total_cost = 0.0
    for i in range(device_count):
        result = compute_device_waste(
            device_id=f"SCALE-{i:05d}",
            device_name=f"Scale Device {i}",
            data_source_type="metered",
            rows=rows,
            threshold=1.0,
            overconsumption_threshold=20.0,
            tariff_rate=8.5,
            shifts=shifts,
        )
        total_cost += float(result.total_cost or 0.0)
    elapsed = time.perf_counter() - t0

    # Throughput target is intentionally conservative for CI/dev hardware.
    assert elapsed < 120.0, f"5000-device compute exceeded limit: {elapsed:.2f}s"
    assert total_cost >= 0.0

