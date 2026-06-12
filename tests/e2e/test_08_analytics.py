from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.assertions import assert_no_nan_inf
from tests.helpers.wait import wait_until
from tests.helpers.wait import wait_for_job


pytestmark = pytest.mark.slow


@pytest.fixture(scope="module", autouse=True)
def ensure_analytics_telemetry(api, simulator, device_id):
    simulator.send_bulk(count=80, mode="normal", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="idle", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="normal", interval_sec=0.1)
    simulator.send_bulk(count=40, mode="overconsumption", interval_sec=0.1)

    def enough():
        items = api.data.get_telemetry(device_id, hours_back=4)
        return len(items) >= 150

    wait_until(enough, timeout_sec=90, description="analytics telemetry window to be available")


def _time_window(hours_back: int = 4):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours_back)
    return start.isoformat(), end.isoformat()


def test_models_endpoint_responsive(api):
    models = api.analytics.get_models()
    assert isinstance(models, dict)
    assert "anomaly_detection" in models
    assert "failure_prediction" in models


def test_submit_anomaly_job(api, device_id, state):
    start, end = _time_window(hours_back=4)
    result = api.analytics.run_job(
        {
            "device_id": device_id,
            "analysis_type": "anomaly",
            "model_name": "anomaly_ensemble",
            "start_time": start,
            "end_time": end,
        }
    )
    state["anomaly_job_id"] = result["job_id"]
    assert result["job_id"]


def test_anomaly_job_completes(api, state):
    wait_for_job(lambda: api.analytics.get_status(state["anomaly_job_id"]), timeout_sec=300, description="anomaly detection job")


def test_anomaly_result_structure(api, state):
    result = api.analytics.get_results(state["anomaly_job_id"])
    assert "data_quality_flags" in result
    assert isinstance(result["data_quality_flags"], list)


def test_anomaly_result_no_nan(api, state):
    result = api.analytics.get_results(state["anomaly_job_id"])
    assert_no_nan_inf(result)


def test_submit_failure_job(api, device_id, state):
    start, end = _time_window(hours_back=4)
    result = api.analytics.run_job(
        {
            "device_id": device_id,
            "analysis_type": "prediction",
            "model_name": "failure_ensemble",
            "start_time": start,
            "end_time": end,
        }
    )
    state["failure_job_id"] = result["job_id"]
    assert result["job_id"]


def test_failure_job_completes(api, state):
    wait_for_job(lambda: api.analytics.get_status(state["failure_job_id"]), timeout_sec=300, description="failure prediction job")


def test_failure_result_has_verdict(api, state):
    result = api.analytics.get_results(state["failure_job_id"])
    if "ensemble" in result:
        assert result["ensemble"]["verdict"] in ("CRITICAL", "WARNING", "WATCH", "NORMAL")
    if "time_to_failure" in result:
        assert "label" in result["time_to_failure"]


def test_failure_result_data_quality_flag_low(api, state):
    result = api.analytics.get_results(state["failure_job_id"])
    flags = result.get("data_quality_flags", [])
    matching = [item for item in flags if item.get("type") == "data_confidence" or "confidence" in str(item.get("type", "")).lower()]
    if matching:
        assert matching[0].get("confidence_level") in ("Very Low", "Low", "Moderate")


def test_failure_result_no_nan(api, state):
    result = api.analytics.get_results(state["failure_job_id"])
    assert_no_nan_inf(result)


def test_anomaly_sequential_runs_same_range(api, device_id):
    start, end = _time_window(hours_back=4)
    first = api.analytics.run_job(
        {
            "device_id": device_id,
            "analysis_type": "anomaly",
            "model_name": "anomaly_ensemble",
            "start_time": start,
            "end_time": end,
        }
    )
    second = api.analytics.run_job(
        {
            "device_id": device_id,
            "analysis_type": "anomaly",
            "model_name": "anomaly_ensemble",
            "start_time": start,
            "end_time": end,
        }
    )

    wait_for_job(lambda: api.analytics.get_status(first["job_id"]), timeout_sec=300, description="anomaly sequential job 1")
    wait_for_job(lambda: api.analytics.get_status(second["job_id"]), timeout_sec=300, description="anomaly sequential job 2")
