from pathlib import Path


def test_auth_dockerfile_uses_multi_stage_build_and_non_root_user():
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    content = dockerfile.read_text()
    final_stage = content.split("FROM python:3.11-slim AS final", 1)[1]

    assert "FROM python:3.11-slim AS builder" in content
    assert "COPY --from=builder /install /usr/local" in content
    assert "build-essential" not in final_stage
    assert "default-libmysqlclient-dev" not in final_stage
    assert "pkg-config" not in final_stage
    assert "curl" in final_stage
    assert "useradd -m -u 1000 appuser" in content
    assert "USER appuser" in content
    assert "chmod +x start.sh" in content
    assert "HEALTHCHECK" in content
    assert "EXPOSE 8090" in content
