from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.workers.job_queue import RedisJobQueue


class _FakeRedis:
    def __init__(self):
        self.xgroup_create_calls = 0
        self.xreadgroup_calls = 0

    async def xgroup_create(self, stream, group, id="0", mkstream=True):
        self.xgroup_create_calls += 1
        return True

    async def xreadgroup(self, **kwargs):
        self.xreadgroup_calls += 1
        if self.xreadgroup_calls == 1:
            raise Exception(
                "NOGROUP No such key 'analytics_jobs_stream' or consumer group 'analytics_workers' in XREADGROUP with GROUP option"
            )
        return [("analytics_jobs_stream", [("1710000000000-0", {"job_id": "job-1", "attempt": "2", "raw_payload": "{\"job_type\":\"analytics\",\"tenant_id\":\"SH00000001\",\"device_id\":\"AD00000001\",\"initiated_by_user_id\":\"tester\",\"initiated_by_role\":\"super_admin\",\"payload\":{\"device_id\":\"AD00000001\",\"analysis_type\":\"anomaly\",\"model_name\":\"anomaly_ensemble\",\"start_time\":\"2026-04-01T00:00:00Z\",\"end_time\":\"2026-04-02T00:00:00Z\"}}"} )])]


class _LoggerProbe:
    def __init__(self):
        self.warning_calls = []

    def warning(self, event, **kwargs):
        self.warning_calls.append((event, kwargs))


async def test_redis_job_queue_recovers_when_consumer_group_disappears():
    queue = RedisJobQueue.__new__(RedisJobQueue)
    queue._redis = _FakeRedis()
    queue._stream = "analytics_jobs_stream"
    queue._dead_stream = "analytics_jobs_dead_letter"
    queue._group = "analytics_workers"
    queue._consumer = "analytics-worker-1"
    queue._maxsize = 10000
    queue._logger = _LoggerProbe()
    queue._group_ready = True

    job = await queue.get_job()

    assert job is not None
    assert job.job_id == "job-1"
    assert job.attempt == 2
    assert job.receipt == "1710000000000-0"
    assert queue._redis.xgroup_create_calls == 1
    assert queue._redis.xreadgroup_calls == 2
    assert queue._logger.warning_calls
    assert queue._logger.warning_calls[0][0] == "redis_consumer_group_missing_recreating"
