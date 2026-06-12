import test from "node:test";
import assert from "node:assert/strict";

import { hasFeature, getMachineHealthDisplayState, FEATURE_LABELS, type FeatureKey } from "../../lib/features.ts";
import {
  PREMIUM_MODULE_GRANT_KEYS,
  PLANT_MANAGER_DELEGATABLE_KEYS,
  getOrgPremiumModuleLabels,
  isPremiumModuleGrantKey,
  isPlantManagerDelegatable,
  toPremiumOrgGrantSet,
} from "../../lib/orgFeatureEntitlements.ts";
import type { MeResponse } from "../../lib/authApi.ts";
import type { PremiumOrgGrantKey } from "../../lib/orgFeatureEntitlements.ts";

function makeMe(availableFeatures: string[]): MeResponse {
  const premiumGrants: PremiumOrgGrantKey[] = availableFeatures.filter(
    (f): f is PremiumOrgGrantKey => toPremiumOrgGrantSet([f]).size > 0,
  );
  return {
    user: {
      id: "u1",
      email: "test@example.com",
      full_name: "Test",
      role: "org_admin",
      tenant_id: "org-1",
      is_active: true,
      created_at: new Date().toISOString(),
      last_login_at: null,
    },
    tenant: {
      id: "org-1",
      name: "Org 1",
      slug: "org-1",
      is_active: true,
      created_at: new Date().toISOString(),
    },
    plant_ids: [],
    entitlements: {
      premium_feature_grants: premiumGrants,
      role_feature_matrix: {},
      baseline_features_by_role: {},
      effective_features_by_role: {},
      available_features: availableFeatures,
      entitlements_version: 1,
    },
  };
}

test("org premium module editor includes Machine Health", () => {
  const modules = getOrgPremiumModuleLabels();
  const mh = modules.find((m) => m.key === "machine_health");
  assert.ok(mh, "machine_health must appear in org premium modules");
  assert.equal(mh.label, "Machine Health");
});

test("machine_health is NOT plant-manager delegatable", () => {
  assert.equal(isPlantManagerDelegatable("machine_health"), false);
});

test("Machine Health is not in the plant-manager delegatable list", () => {
  assert.ok(!PLANT_MANAGER_DELEGATABLE_KEYS.includes("machine_health" as any));
});

test("org premium module labels list all five modules", () => {
  const modules = getOrgPremiumModuleLabels();
  const keys = modules.map((m) => m.key);
  assert.deepEqual(keys, ["analytics", "reports", "waste_analysis", "copilot", "machine_health"]);
});

test("only analytics, reports, waste_analysis are plant-manager delegatable", () => {
  const modules = getOrgPremiumModuleLabels();
  const delegatable = modules.filter((m) => m.delegatable).map((m) => m.key);
  assert.deepEqual(delegatable, ["analytics", "reports", "waste_analysis"]);
});

test("getMachineHealthDisplayState returns enabled when feature present", () => {
  const me = makeMe(["machines", "machine_health", "calendar", "rules", "settings"]);
  assert.equal(getMachineHealthDisplayState(me), "enabled");
});

test("getMachineHealthDisplayState returns locked when feature absent", () => {
  const me = makeMe(["machines", "calendar", "rules", "settings"]);
  assert.equal(getMachineHealthDisplayState(me), "locked");
});

test("getMachineHealthDisplayState returns unresolved for null me", () => {
  assert.equal(getMachineHealthDisplayState(null), "unresolved");
});

test("getMachineHealthDisplayState returns locked for null entitlements", () => {
  const me: MeResponse = {
    user: {
      id: "u1",
      email: "sa@example.com",
      full_name: "Super Admin",
      role: "super_admin",
      tenant_id: null,
      is_active: true,
      created_at: new Date().toISOString(),
      last_login_at: null,
    },
    tenant: null,
    plant_ids: [],
    entitlements: null,
  };
  assert.equal(getMachineHealthDisplayState(me), "locked");
});

test("machine_health has correct label for locked premium card", () => {
  assert.equal(FEATURE_LABELS["machine_health"], "Machine Health");
});

test("hasFeature(me, machine_health) is true when org grant enabled for viewer role", () => {
  const me = makeMe(["machines", "machine_health"]);
  assert.equal(hasFeature(me, "machine_health"), true);
});

test("hasFeature(me, machine_health) is false when org grant absent for viewer role", () => {
  const me = makeMe(["machines"]);
  assert.equal(hasFeature(me, "machine_health"), false);
});

test("isPremiumModuleGrantKey accepts machine_health", () => {
  assert.equal(isPremiumModuleGrantKey("machine_health"), true);
});

test("isPremiumModuleGrantKey rejects baseline keys", () => {
  assert.equal(isPremiumModuleGrantKey("machines"), false);
  assert.equal(isPremiumModuleGrantKey("calendar"), false);
  assert.equal(isPremiumModuleGrantKey("rules"), false);
  assert.equal(isPremiumModuleGrantKey("settings"), false);
});
