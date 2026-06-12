from types import SimpleNamespace

from tests.conftest import _analytics_runtime_ready


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_analytics_runtime_ready_requires_idle_queue_and_worker():
    api = SimpleNamespace(
        analytics=SimpleNamespace(
            c=SimpleNamespace(
                get=lambda path: _FakeResponse(
                    {
                        "active_workers": 2,
                        "queue_depth": 0,
                        "running_jobs": 0,
                    }
                )
            )
        )
    )

    ready, snapshot = _analytics_runtime_ready(api)

    assert ready is True
    assert snapshot["active_workers"] == 2


def test_analytics_runtime_ready_rejects_backlog_or_missing_worker():
    api = SimpleNamespace(
        analytics=SimpleNamespace(
            c=SimpleNamespace(
                get=lambda path: _FakeResponse(
                    {
                        "active_workers": 0,
                        "queue_depth": 1,
                        "running_jobs": 0,
                    }
                )
            )
        )
    )

    ready, snapshot = _analytics_runtime_ready(api)

    assert ready is False
    assert snapshot["queue_depth"] == 1
