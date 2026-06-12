from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.shared.feature_entitlements import (
    FEATURES_AUTO_AVAILABLE_WITH_BASELINE,
    FEATURE_KEYS,
    ORG_GRANTABLE_FEATURES,
    PLANT_MANAGER_DELEGATABLE_FEATURES,
    build_feature_entitlement_state,
    get_allowed_premium_features_for_role,
    get_baseline_features_for_role,
    normalize_feature_keys,
    validate_premium_grants,
    validate_role_feature_matrix,
)


class TestMachineHealthFeatureKey:
    def test_machine_health_is_valid_feature_key(self):
        assert "machine_health" in FEATURE_KEYS

    def test_machine_health_is_org_grantable(self):
        assert "machine_health" in ORG_GRANTABLE_FEATURES

    def test_machine_health_not_plant_manager_delegatable(self):
        assert "machine_health" not in PLANT_MANAGER_DELEGATABLE_FEATURES

    def test_machine_health_auto_available_with_machines_baseline(self):
        assert FEATURES_AUTO_AVAILABLE_WITH_BASELINE["machine_health"] == "machines"

    def test_normalize_accepts_machine_health(self):
        result = normalize_feature_keys(["machine_health"])
        assert result == ["machine_health"]

    def test_validate_premium_grants_accepts_machine_health(self):
        result = validate_premium_grants(["machine_health"])
        assert result == ["machine_health"]


class TestMachineHealthOrgGrant:
    def test_super_admin_can_grant_machine_health(self):
        result = validate_premium_grants(["machine_health", "analytics"])
        assert "machine_health" in result

    def test_org_admin_cannot_modify_org_level_grants(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_role_feature_matrix(
                role_feature_matrix={"plant_manager": ["machine_health"], "operator": [], "viewer": []},
                allowed_premium_features=["machine_health"],
                caller_role="org_admin",
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == "FEATURE_SCOPE_DENIED"

    def test_machine_health_not_delegatable_to_plant_manager_via_matrix(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_role_feature_matrix(
                role_feature_matrix={"plant_manager": ["machine_health"], "operator": [], "viewer": []},
                allowed_premium_features=["machine_health"],
                caller_role="org_admin",
            )
        assert exc_info.value.status_code == 403
        assert "machine_health" in exc_info.value.detail.get("invalid_features", [])


class TestMachineHealthAutoAvailability:
    def test_org_admin_gets_machine_health_when_granted(self):
        state = build_feature_entitlement_state(
            role="org_admin",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features

    def test_plant_manager_gets_machine_health_when_org_granted(self):
        state = build_feature_entitlement_state(
            role="plant_manager",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features

    def test_operator_gets_machine_health_when_org_granted(self):
        state = build_feature_entitlement_state(
            role="operator",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features

    def test_viewer_gets_machine_health_when_org_granted(self):
        state = build_feature_entitlement_state(
            role="viewer",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features

    def test_super_admin_resolved_as_org_admin_gets_machine_health(self):
        state = build_feature_entitlement_state(
            role="super_admin",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features


class TestMachineHealthNotExposedWhenAbsent:
    @pytest.mark.parametrize("role", ["org_admin", "plant_manager", "operator", "viewer"])
    def test_no_machine_health_without_org_grant(self, role: str):
        state = build_feature_entitlement_state(
            role=role,
            premium_feature_grants=["analytics"],
            role_feature_matrix={"plant_manager": ["analytics"], "operator": [], "viewer": []},
            entitlements_version=1,
        )
        assert "machine_health" not in state.available_features

    def test_empty_grants_no_machine_health(self):
        state = build_feature_entitlement_state(
            role="viewer",
            premium_feature_grants=[],
            role_feature_matrix=None,
            entitlements_version=0,
        )
        assert "machine_health" not in state.available_features


class TestMachineHealthRequiresMachinesBaseline:
    def test_auto_availability_requires_matching_baseline(self):
        original = dict(FEATURES_AUTO_AVAILABLE_WITH_BASELINE)
        try:
            FEATURES_AUTO_AVAILABLE_WITH_BASELINE["machine_health"] = "copilot"
            state = build_feature_entitlement_state(
                role="viewer",
                premium_feature_grants=["machine_health"],
                role_feature_matrix=None,
                entitlements_version=1,
            )
            assert "machine_health" not in state.available_features
        finally:
            FEATURES_AUTO_AVAILABLE_WITH_BASELINE.clear()
            FEATURES_AUTO_AVAILABLE_WITH_BASELINE.update(original)

    def test_all_machines_roles_get_machine_health(self):
        for role in ["org_admin", "plant_manager", "operator", "viewer"]:
            baseline = get_baseline_features_for_role(role)
            assert "machines" in baseline, f"{role} should have machines baseline"

        state = build_feature_entitlement_state(
            role="operator",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features


class TestMachineHealthEffectiveFeaturesByRole:
    def test_effective_features_include_machine_health_for_all_roles_when_granted(self):
        state = build_feature_entitlement_state(
            role="org_admin",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        for role_name, features in state.effective_features_by_role.items():
            assert "machine_health" in features, f"{role_name} should have machine_health"

    def test_effective_features_exclude_machine_health_when_not_granted(self):
        state = build_feature_entitlement_state(
            role="org_admin",
            premium_feature_grants=["analytics"],
            role_feature_matrix={"plant_manager": ["analytics"], "operator": [], "viewer": []},
            entitlements_version=1,
        )
        for role_name, features in state.effective_features_by_role.items():
            assert "machine_health" not in features, f"{role_name} should not have machine_health"


class TestMachineHealthEntitlementVersioning:
    def test_entitlements_version_preserved(self):
        state = build_feature_entitlement_state(
            role="viewer",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=5,
        )
        assert state.entitlements_version == 5

    def test_entitlements_version_zero(self):
        state = build_feature_entitlement_state(
            role="viewer",
            premium_feature_grants=[],
            role_feature_matrix=None,
            entitlements_version=0,
        )
        assert state.entitlements_version == 0

    def test_machine_health_grant_in_premium_feature_grants(self):
        state = build_feature_entitlement_state(
            role="org_admin",
            premium_feature_grants=["machine_health", "analytics"],
            role_feature_matrix=None,
            entitlements_version=2,
        )
        assert "machine_health" in state.premium_feature_grants
        assert "analytics" in state.premium_feature_grants


class TestMachineHealthCombinedWithOtherFeatures:
    def test_machine_health_and_analytics_together(self):
        state = build_feature_entitlement_state(
            role="plant_manager",
            premium_feature_grants=["machine_health", "analytics"],
            role_feature_matrix={"plant_manager": ["analytics"], "operator": [], "viewer": []},
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features
        assert "analytics" in state.available_features
        assert "machines" in state.available_features
        assert "rules" in state.available_features

    def test_machine_health_flows_without_delegation_entry(self):
        state = build_feature_entitlement_state(
            role="plant_manager",
            premium_feature_grants=["machine_health"],
            role_feature_matrix={"plant_manager": [], "operator": [], "viewer": []},
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features

    def test_operator_gets_machine_health_plus_baseline(self):
        state = build_feature_entitlement_state(
            role="operator",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features
        assert "machines" in state.available_features
        assert "rules" in state.available_features

    def test_viewer_gets_machine_health_plus_baseline_only(self):
        state = build_feature_entitlement_state(
            role="viewer",
            premium_feature_grants=["machine_health"],
            role_feature_matrix=None,
            entitlements_version=1,
        )
        assert "machine_health" in state.available_features
        assert "machines" in state.available_features
        assert len(state.available_features) == 2
