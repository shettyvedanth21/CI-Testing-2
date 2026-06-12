from __future__ import annotations

import os
import time
from datetime import date

import pytest
import requests

from tests.helpers.wait import wait_for_job


pytestmark = [pytest.mark.scale, pytest.mark.slow]


def _list_device_ids(limit: int) -> list[str]:
    resp = requests.get("http://localhost:8000/api/v1/devices", timeout=30)
    resp.raise_for_status()
    body = resp.json()
    items = []
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "devices", "results"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        if not items:
            for key in ("items", "devices", "results"):
                if isinstance(body.get(key), list):
                    items = body[key]
                    break
    ids = [str(x.get("device_id")) for x in items if x.get("device_id")]
    return ids[:limit]


def test_live_waste_scale_run_selected_devices(api):
    if os.getenv("RUN_SCALE_BENCHMARK", "0") != "1":
        pytest.skip("Set RUN_SCALE_BENCHMARK=1 to run live scale validation")

    target = int(os.getenv("LIVE_SCALE_TARGET_DEVICES", "250"))
    device_ids = _list_device_ids(target)
    if not device_ids:
        pytest.skip("No devices available for live scale validation")

    today = date.today().isoformat()
    t0 = time.perf_counter()
    result = api.waste.run_analysis(
        {
            "scope": "selected",
            "device_ids": device_ids,
            "start_date": today,
            "end_date": today,
            "granularity": "daily",
            "job_name": f"Scale Validation ({len(device_ids)} devices)",
        }
    )
    job_id = result["job_id"]
    wait_for_job(lambda: api.waste.get_status(job_id), timeout_sec=900, description="live waste scale job")
    elapsed = time.perf_counter() - t0

    payload = api.waste.get_result(job_id)
    rows = payload.get("device_summaries") or payload.get("devices") or []
    assert len(rows) > 0

    total_energy = payload.get("total_energy_cost")
    total_waste = payload.get("total_waste_cost")
    if isinstance(total_energy, (int, float)) and isinstance(total_waste, (int, float)):
        assert total_waste <= total_energy + 0.01

    # Guardrail target for staging: keep selected-device scale run bounded.
    assert elapsed < 900.0, f"Scale run exceeded timeout budget: {elapsed:.2f}s"

