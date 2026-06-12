from pathlib import Path


def test_analytics_dockerfile_uses_multi_stage_build():
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    api_requirements = Path(__file__).resolve().parents[2] / "requirements.api.txt"
    worker_requirements = Path(__file__).resolve().parents[2] / "requirements.worker.txt"
    content = dockerfile.read_text()
    api_reqs = api_requirements.read_text()
    worker_reqs = worker_requirements.read_text()
    api_stage = content.split("FROM python:3.11-slim AS api", 1)[1].split(
        "FROM python:3.11-slim AS worker",
        1,
    )[0]
    worker_stage = content.split("FROM python:3.11-slim AS worker", 1)[1]

    assert "FROM python:3.11-slim AS builder" in content
    assert "FROM builder AS worker-builder" in content
    assert "FROM python:3.11-slim AS api" in content
    assert "FROM python:3.11-slim AS worker" in content
    assert "build-essential" in content
    assert "default-libmysqlclient-dev" in content
    assert "pkg-config" in content
    assert "COPY --from=builder /install /usr/local" in content
    assert "COPY --from=worker-builder /install /usr/local" in content
    assert "chmod +x /app/start.sh" in content
    assert "USER appuser" in content
    assert "EXPOSE 8003" in content
    assert "requirements.api.txt" in content
    assert "requirements.worker.txt" in content
    assert "build-essential" not in api_stage
    assert "default-libmysqlclient-dev" not in api_stage
    assert "pkg-config" not in api_stage
    assert "tensorflow" not in api_stage
    assert "xgboost" not in api_stage
    assert "prophet" not in api_stage
    assert "shap" not in api_stage
    assert "statsmodels" not in api_stage
    assert "tensorflow" in worker_reqs
    assert "xgboost" in worker_reqs
    assert "prophet" in worker_reqs
    assert "shap" in worker_reqs
    assert "statsmodels" in worker_reqs
    assert "tensorflow" not in api_reqs
    assert "tensorflow" not in worker_stage
