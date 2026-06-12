from __future__ import annotations

import json
import os
import sys
from importlib import util
from pathlib import Path


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

_MIGRATION_PATH = AUTH_SERVICE_ROOT / "alembic" / "versions" / "0004_add_org_feature_entitlements.py"
_spec = util.spec_from_file_location("org_feature_entitlements_migration", _MIGRATION_PATH)
assert _spec and _spec.loader
migration = util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def test_org_entitlements_migration_serializes_json_defaults() -> None:
    assert migration._json([]) == "[]"
    assert json.loads(migration._json({"plant_manager": [], "operator": [], "viewer": []})) == {
        "plant_manager": [],
        "operator": [],
        "viewer": [],
    }
