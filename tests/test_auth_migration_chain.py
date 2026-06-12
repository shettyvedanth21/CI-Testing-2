from __future__ import annotations

from pathlib import Path


AUTH_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "services" / "auth-service" / "alembic" / "versions"


def _read_revision_metadata(path: Path) -> tuple[str, str | None]:
    revision = None
    down_revision = None
    for line in path.read_text().splitlines():
        if line.startswith("revision = "):
            revision = line.split("=", 1)[1].strip().strip('"\'')
        elif line.startswith("down_revision = "):
            value = line.split("=", 1)[1].strip()
            down_revision = None if value == "None" else value.strip('"\'')
    if revision is None:
        raise AssertionError(f"Missing revision in {path.name}")
    return revision, down_revision


def test_auth_alembic_revision_chain_is_contiguous() -> None:
    revisions = {}
    for path in AUTH_VERSIONS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        revision, down_revision = _read_revision_metadata(path)
        revisions[revision] = down_revision

    assert "0008_sh_tenant_ids" in revisions
    assert revisions["0008_sh_tenant_ids"] == "0007_tenant_id_columns"
    assert "0011_platform_maintenance" in revisions
    assert revisions["0011_platform_maintenance"] == "0010_action_token_cleanup"

    known_revisions = set(revisions)
    for revision, down_revision in revisions.items():
        if down_revision is not None:
            assert down_revision in known_revisions, f"{revision} points to missing {down_revision}"
