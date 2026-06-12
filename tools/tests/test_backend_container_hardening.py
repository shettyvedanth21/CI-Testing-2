from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SERVICES = [
    "analytics-service",
    "auth-service",
    "copilot-service",
    "data-export-service",
    "data-service",
    "device-service",
    "energy-service",
    "reporting-service",
    "rule-engine-service",
    "waste-analysis-service",
]


def _final_stage_content(content: str) -> str:
    return content.rsplit("FROM python:3.11-slim AS final", 1)[1]


def test_root_dockerignore_matches_compose_root_context():
    compose_file = (REPO_ROOT / "docker-compose.yml").read_text()
    ignore_file = (REPO_ROOT / ".dockerignore").read_text()

    assert "context: ." in compose_file
    assert ".git" in ignore_file
    assert "**/tests" in ignore_file
    assert "**/__pycache__" in ignore_file
    assert ".env" in ignore_file


def test_backend_service_dockerfiles_use_multi_stage_and_non_root_runtime():
    for service in BACKEND_SERVICES:
        dockerfile = REPO_ROOT / "services" / service / "Dockerfile"
        content = dockerfile.read_text()
        assert "USER appuser" in content, service

        if service == "analytics-service":
            api_requirements = REPO_ROOT / "services" / service / "requirements.api.txt"
            worker_requirements = REPO_ROOT / "services" / service / "requirements.worker.txt"
            assert "FROM python:3.11-slim AS builder" in content, service
            assert "FROM builder AS worker-builder" in content, service
            assert "FROM python:3.11-slim AS api" in content, service
            assert "FROM python:3.11-slim AS worker" in content, service
            api_stage = content.split("FROM python:3.11-slim AS api", 1)[1].split(
                "FROM python:3.11-slim AS worker",
                1,
            )[0]
            api_reqs = api_requirements.read_text()
            worker_reqs = worker_requirements.read_text()
            worker_stage = content.split("FROM python:3.11-slim AS worker", 1)[1]
            assert "requirements.api.txt" in content, service
            assert "requirements.worker.txt" in content, service
            assert "COPY --from=builder /install /usr/local" in content, service
            assert "COPY --from=worker-builder /install /usr/local" in content, service
            assert "chmod +x /app/start.sh" in content, service
            assert "tensorflow" not in api_stage, service
            assert "xgboost" not in api_stage, service
            assert "prophet" not in api_stage, service
            assert "shap" not in api_stage, service
            assert "statsmodels" not in api_stage, service
            assert "build-essential" not in api_stage, service
            assert "default-libmysqlclient-dev" not in api_stage, service
            assert "pkg-config" not in api_stage, service
            assert "tensorflow" in worker_reqs, service
            assert "xgboost" in worker_reqs, service
            assert "prophet" in worker_reqs, service
            assert "shap" in worker_reqs, service
            assert "statsmodels" in worker_reqs, service
            assert "tensorflow" not in api_reqs, service
            assert "tensorflow" not in worker_stage, service
        else:
            final_stage = _final_stage_content(content)

            assert "FROM python:3.11-slim AS builder" in content, service
            assert "FROM python:3.11-slim AS final" in content, service
            assert "COPY --from=builder /install /usr/local" in content, service
            assert "chmod +x" in content, service
            assert "build-essential" not in final_stage, service
            assert "default-libmysqlclient-dev" not in final_stage, service
            assert "pkg-config" not in final_stage, service
