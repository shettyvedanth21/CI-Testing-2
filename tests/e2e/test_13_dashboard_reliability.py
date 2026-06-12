from __future__ import annotations

import json
import time

import requests


def _read_sse_event(url: str, headers: dict[str, str] | None = None, timeout_sec: int = 12) -> dict:
    resp = requests.get(url, headers=headers or {}, stream=True, timeout=(5, timeout_sec))
    resp.raise_for_status()
    try:
        event = {"id": None, "event": None, "data": None}
        started = time.time()
        for line in resp.iter_lines(decode_unicode=True):
            if time.time() - started > timeout_sec:
                break
            if line is None:
                continue
            if line.startswith("id:"):
                event["id"] = line.split(":", 1)[1].strip()
            elif line.startswith("event:"):
                event["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                event["data"] = json.loads(payload)
            elif line == "" and event["data"] is not None:
                return event
        raise AssertionError("Timed out waiting for SSE event payload")
    finally:
        resp.close()


def test_dashboard_bootstrap_missing_device_returns_404(api):
    missing_id = "DOES_NOT_EXIST_DEVICE_XYZ"
    resp = api.device.c.get(f"/api/v1/devices/{missing_id}/dashboard-bootstrap")
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("success") is False
    assert body.get("error", {}).get("code") == "DEVICE_NOT_FOUND"


def test_fleet_stream_emits_events_or_heartbeat(api):
    event = _read_sse_event(
        "http://localhost:8000/api/v1/devices/dashboard/fleet-stream?page_size=5",
        headers=dict(api.device.c.headers),
    )
    assert event["event"] in {"fleet_update", "heartbeat"}
    assert event["id"] is not None
    assert isinstance(event["data"], dict)
    assert "stale" in event["data"]


def test_fleet_stream_reconnect_supports_last_event_id(api):
    headers = dict(api.device.c.headers)
    first = _read_sse_event(
        "http://localhost:8000/api/v1/devices/dashboard/fleet-stream?page_size=5",
        headers=headers,
    )
    first_id = int(first["id"])
    second = _read_sse_event(
        "http://localhost:8000/api/v1/devices/dashboard/fleet-stream?page_size=5",
        headers={**headers, "Last-Event-ID": str(first_id)},
    )
    assert second["id"] is not None
    assert int(second["id"]) >= first_id
    assert second["event"] in {"fleet_update", "heartbeat"}


def test_metrics_endpoint_exposes_dashboard_slo_metrics():
    resp = requests.get("http://localhost:8000/metrics", timeout=10)
    assert resp.status_code == 200
    text = resp.text
    assert "dashboard_snapshot_age_seconds" in text
    assert "dashboard_scheduler_lag_seconds" in text
    assert "fleet_stream_emit_lag_seconds" in text
    assert "dashboard_cost_data_age_seconds" in text
    assert "dashboard_cost_data_state_total" in text
    assert "dashboard_cost_refresh_failures_total" in text
    assert "calendar_cost_snapshot_age_seconds" in text


def test_dashboard_summary_exposes_cost_data_contract(api):
    resp = api.device.c.get("/api/v1/devices/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("cost_data_state") in {"fresh", "stale", "unavailable"}
    assert isinstance(body.get("cost_data_reasons"), list)
    assert "energy_widgets" in body


def test_monthly_calendar_exposes_cost_data_contract(api):
    now = time.localtime()
    year = now.tm_year
    month = now.tm_mon
    resp = api.device.c.get("/api/v1/devices/calendar/monthly-energy", params={"year": year, "month": month})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("cost_data_state") in {"fresh", "stale", "unavailable"}
    assert isinstance(body.get("cost_data_reasons"), list)
    assert "summary" in body
