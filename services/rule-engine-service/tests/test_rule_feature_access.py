from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature


def _request_for_role(role: str):
    entitlements = build_feature_entitlement_state(
        role=role,
        premium_feature_grants=[],
        role_feature_matrix={},
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_context=SimpleNamespace(role=role, entitlements=entitlements),
            feature_entitlements=entitlements,
        )
    )


def test_rules_feature_is_enabled_for_operator_and_plant_manager():
    dependency = require_feature("rules")

    dependency(_request_for_role("operator"))
    dependency(_request_for_role("plant_manager"))
    dependency(_request_for_role("org_admin"))


def test_rules_feature_is_blocked_for_viewer():
    dependency = require_feature("rules")

    with pytest.raises(HTTPException) as exc_info:
        dependency(_request_for_role("viewer"))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "FEATURE_DISABLED"
