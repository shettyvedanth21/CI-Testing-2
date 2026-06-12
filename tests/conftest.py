"""
Shared session-scoped fixtures for live-stack E2E tests.
"""

from datetime import datetime, timezone
import os
import time
import uuid
import warnings

import httpx
import pytest

from tests.helpers.api_client import APIClient
from tests.helpers.simulator import TelemetrySimulator

os.environ.setdefault(
    "INTERNAL_SERVICE_SHARED_SECRET",
    "test-internal-service-secret-at-least-32-chars",
)
if os.environ.get("MYSQL_HOST") == "mysql":
    os.environ["MYSQL_HOST"] = "localhost"


SERVICES = {
    "device": "http://localhost:8000",
    "data": "http://localhost:8081",
    "rules": "http://localhost:8002",
    "analytics": "http://localhost:8003",
    "reporting": "http://localhost:8085",
    "waste": "http://localhost:8087",
    "copilot": "http://localhost:8007",
}

TEST_RUN_ID = uuid.uuid4().hex[:8].upper()
DB_AVAILABLE = True


def _check_e2e_infrastructure() -> bool:
    try:
        resp = httpx.get(f"{SERVICES['device']}/health", timeout=3)
        if resp.status_code != 200:
            return False
    except Exception:
        return False
    try:
        from tests.helpers.db_client import get_db_connection
        conn = get_db_connection()
        conn.close()
    except Exception:
        return False
    return True


E2E_INFRA_AVAILABLE = _check_e2e_infrastructure()


def pytest_sessionstart(session):
    global DB_AVAILABLE
    try:
        from tests.helpers.db_client import get_db_connection

        conn = get_db_connection()
        conn.close()
    except Exception:
        DB_AVAILABLE = False
        warnings.warn(
            "Direct DB connection not available. DB-level verification tests will be skipped.",
            UserWarning,
        )

    if not E2E_INFRA_AVAILABLE:
        warnings.warn(
            "E2E infrastructure not reachable. Live-stack E2E tests will be skipped.",
            UserWarning,
        )


def pytest_collection_modifyitems(config, items):
    if E2E_INFRA_AVAILABLE:
        return
    skip_e2e = pytest.mark.skip(reason="E2E infrastructure not reachable")
    for item in items:
        if item.fspath and "tests/e2e" in str(item.fspath):
            item.add_marker(skip_e2e)


def pytest_configure(config):
    config.e2e = {
        "device_id": None,
        "device_name": f"E2E Compressor {TEST_RUN_ID}",
        "suite_started_at": datetime.now(timezone.utc),
        "channel_id": None,
        "shift_id": None,
        "rule_id": None,
        "anomaly_job_id": None,
        "failure_job_id": None,
        "report_id": None,
        "waste_job_id": None,
        "waste_device_row": None,
    }


def _run_includes_live_e2e(request: pytest.FixtureRequest) -> bool:
    return any(item.nodeid.startswith("tests/e2e/") for item in request.session.items)


@pytest.fixture(scope="session")
def state(pytestconfig):
    return pytestconfig.e2e


@pytest.fixture(scope="session")
def device_id(pytestconfig, ensure_e2e_device_exists):
    return pytestconfig.e2e["device_id"]


@pytest.fixture(scope="session")
def api():
    return APIClient(SERVICES)


@pytest.fixture(scope="session")
def simulator(device_id, api):
    mqtt_creds = api.device.register_mqtt_credential(device_id)
    sim = TelemetrySimulator(
        broker_host="localhost",
        broker_port=1883,
        device_id=device_id,
        tenant_id=api.default_tenant,
        mqtt_username=mqtt_creds.get("mqtt_username"),
        mqtt_password=mqtt_creds.get("mqtt_password"),
    )
    yield sim
    sim.disconnect()


@pytest.fixture(scope="session", autouse=True)
def ensure_e2e_device_exists(request: pytest.FixtureRequest, state):
    if not _run_includes_live_e2e(request):
        return None

    if not E2E_INFRA_AVAILABLE:
        return None

    api = APIClient(SERVICES)
    existing_device_id = state.get("device_id")
    if existing_device_id:
        try:
            api.device.get_device(existing_device_id)
            return existing_device_id
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

    created = api.device.create_device(
        {
            "device_name": state["device_name"],
            "device_type": "compressor",
            "location": "E2E Test Floor",
            "data_source_type": "metered",
            "phase_type": "single",
        }
    )
    state["device_id"] = created["device_id"]
    state["device_name"] = created["device_name"]
    return created["device_id"]


@pytest.fixture(scope="session", autouse=True)
def verify_all_services_healthy(request: pytest.FixtureRequest):
    if not _run_includes_live_e2e(request):
        return

    if not E2E_INFRA_AVAILABLE:
        return

    health_map = {
        "device-service": f"{SERVICES['device']}/health",
        "data-service": f"{SERVICES['data']}/api/v1/data/health",
        "rule-engine": f"{SERVICES['rules']}/health",
        "analytics-service": f"{SERVICES['analytics']}/health/live",
        "reporting-service": f"{SERVICES['reporting']}/health",
        "waste-service": f"{SERVICES['waste']}/health",
        "copilot-service": f"{SERVICES['copilot']}/health",
    }

    for name, url in health_map.items():
        ok = False
        last_err = None
        last_detail = None
        for _ in range(12):
            try:
                resp = httpx.get(url, timeout=5)
                if resp.status_code == 200:
                    body = resp.json() if "application/json" in resp.headers.get("content-type", "") else None
                    if name == "data-service" and isinstance(body, dict):
                        checks = body.get("checks") or {}
                        if body.get("status") == "healthy" and checks.get("mqtt") == "connected":
                            ok = True
                            break
                        last_detail = body
                    else:
                        ok = True
                        break
                else:
                    last_detail = resp.text
            except Exception as exc:  # pragma: no cover
                last_err = exc
            time.sleep(5)
        if not ok:
            detail_block = f"Last response: {last_detail}\n" if last_detail is not None else ""
            pytest.fail(
                f"\n{'=' * 60}\n"
                f"SERVICE NOT READY: {name}\n"
                f"URL: {url}\n"
                f"{detail_block}"
                f"Last error: {last_err}\n"
                f"Fix: docker-compose up -d && wait 30s\n"
                f"{'=' * 60}"
            )


def _cleanup_interrupted_analytics_jobs(suite_started_at: datetime) -> int:
    from tests.helpers.db_client import get_db_connection

    sql = """
        UPDATE analytics_jobs
        SET status = 'failed',
            error_code = 'SERVICE_RESTART',
            error_message = 'Job was interrupted before E2E suite start. Please resubmit.',
            message = 'Job was interrupted before E2E suite start. Please resubmit.',
            completed_at = UTC_TIMESTAMP(),
            updated_at = UTC_TIMESTAMP()
        WHERE status IN ('running', 'queued', 'pending')
          AND created_at < %s
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (suite_started_at,))
        conn.commit()
        return int(conn.affected_rows() or 0)
    finally:
        conn.close()


def _analytics_runtime_ready(api: APIClient) -> tuple[bool, dict | None]:
    try:
        snapshot = api.analytics.c.get("/api/v1/analytics/ops/queue").json()
    except Exception:
        return False, None

    ready = (
        int(snapshot.get("active_workers") or 0) > 0
        and int(snapshot.get("queue_depth") or 0) == 0
        and int(snapshot.get("running_jobs") or 0) == 0
    )
    return ready, snapshot


@pytest.fixture(scope="session", autouse=True)
def ensure_analytics_runtime_clean(request: pytest.FixtureRequest, state, verify_all_services_healthy):
    if not _run_includes_live_e2e(request):
        return

    if not DB_AVAILABLE:
        return

    api = APIClient(SERVICES)
    cleaned = _cleanup_interrupted_analytics_jobs(state["suite_started_at"])
    if cleaned:
        warnings.warn(
            f"Cleaned {cleaned} interrupted analytics jobs before E2E suite start.",
            UserWarning,
        )

    deadline = time.time() + 90
    last_snapshot = None
    while time.time() < deadline:
        ready, snapshot = _analytics_runtime_ready(api)
        last_snapshot = snapshot
        if ready:
            return
        time.sleep(3)

    pytest.fail(
        "\n".join(
            [
                "=" * 60,
                "ANALYTICS RUNTIME NOT CLEAN BEFORE E2E SUITE",
                f"Last queue snapshot: {last_snapshot}",
                "Fix: ensure analytics worker is alive and no stale pending/running jobs remain.",
                "=" * 60,
            ]
        )
    )


@pytest.fixture(scope="session", autouse=True)
def cleanup(request):
    if not _run_includes_live_e2e(request):
        yield
        return

    if not DB_AVAILABLE:
        yield
        return

    api = request.getfixturevalue("api")
    device_id = request.getfixturevalue("device_id")
    state = request.getfixturevalue("state")

    yield
    try:
        if state.get("rule_id"):
            api.rules.delete_rule(state["rule_id"])
    except Exception:
        pass
    try:
        if state.get("channel_id"):
            api.reporting.delete_notification_channel(state["channel_id"])
    except Exception:
        pass
    try:
        api.device.delete_device(device_id)
    except Exception:
        pass
