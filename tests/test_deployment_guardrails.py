from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_declares_local_development_only_and_requires_mqtt_choice():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()

    assert "Local development stack only" in compose
    assert "EMQX_ALLOW_ANONYMOUS:-true" not in compose
    assert "Set EMQX_ALLOW_ANONYMOUS explicitly" in compose


def test_production_template_disables_mqtt_anonymous_and_uses_placeholders():
    template = (PROJECT_ROOT / ".env.production.example").read_text()

    assert "ENVIRONMENT=production" in template
    assert "EMQX_ALLOW_ANONYMOUS=false" in template
    assert "MYSQL_PASSWORD=energy" not in template
    assert "MINIO_ROOT_PASSWORD=minio123" not in template
    assert "INFLUXDB_TOKEN=energy-token" not in template


def test_aws_production_guidance_lists_required_operational_controls():
    doc = (PROJECT_ROOT / "docs" / "aws_production_deployment.md").read_text()

    for required in [
        "private subnets",
        "Secrets Manager",
        "Anonymous access must be disabled",
        "Block public access",
        "lifecycle policies",
        "automated backups",
        "fail closed",
    ]:
        assert required in doc
