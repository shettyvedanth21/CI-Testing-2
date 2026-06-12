from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.shared.feature_entitlements import build_feature_entitlement_state, require_feature


def _request_for_feature(feature: str, *, granted: bool):
    grants = [feature] if granted else []
    role_feature_matrix = {"plant_manager": [], "operator": [], "viewer": []}
    if granted and feature in {"analytics", "reports", "waste_analysis"}:
        role_feature_matrix["plant_manager"] = [feature]
    entitlements = build_feature_entitlement_state(
        role="org_admin",
        premium_feature_grants=grants,
        role_feature_matrix=role_feature_matrix,
        entitlements_version=1 if granted else 0,
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_context=SimpleNamespace(role="org_admin", entitlements=entitlements),
            feature_entitlements=entitlements,
        )
    )


@pytest.mark.parametrize("feature", ["analytics", "reports", "waste_analysis", "copilot", "machine_health"])
def test_premium_feature_gate_blocks_disabled_features(feature: str):
    dependency = require_feature(feature)

    with pytest.raises(HTTPException) as exc_info:
        dependency(_request_for_feature(feature, granted=False))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "FEATURE_DISABLED"


@pytest.mark.parametrize("feature", ["analytics", "reports", "waste_analysis", "copilot", "machine_health"])
def test_premium_feature_gate_allows_granted_features(feature: str):
    dependency = require_feature(feature)

    dependency(_request_for_feature(feature, granted=True))
