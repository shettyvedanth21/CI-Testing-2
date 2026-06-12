import test from "node:test";
import assert from "node:assert/strict";

import {
  NOTIFICATION_ALERT_GRANT_KEYS,
  PREMIUM_MODULE_GRANT_KEYS,
  isPremiumOrgGrantKey,
  toPremiumOrgGrantSet,
} from "../../lib/orgFeatureEntitlements.ts";

test("premium org entitlement helpers include notification alert grants", () => {
  assert.deepEqual(NOTIFICATION_ALERT_GRANT_KEYS, ["notification_sms", "notification_whatsapp"]);
  assert.deepEqual(PREMIUM_MODULE_GRANT_KEYS, ["analytics", "reports", "waste_analysis", "copilot", "machine_health"]);
  assert.equal(isPremiumOrgGrantKey("notification_sms"), true);
  assert.equal(isPremiumOrgGrantKey("notification_whatsapp"), true);
  assert.equal(isPremiumOrgGrantKey("machines"), false);
});

test("toPremiumOrgGrantSet keeps only supported organisation-level premium grants", () => {
  const grants = toPremiumOrgGrantSet([
    "analytics",
    "machine_health",
    "notification_sms",
    "machines",
    "notification_whatsapp",
    "analytics",
  ]);

  assert.deepEqual(Array.from(grants), ["analytics", "machine_health", "notification_sms", "notification_whatsapp"]);
});
