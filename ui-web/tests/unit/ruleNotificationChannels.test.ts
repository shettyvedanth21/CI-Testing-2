import test from "node:test";
import assert from "node:assert/strict";

import type { MeResponse } from "../../lib/authApi.ts";
import type { PremiumOrgGrantKey } from "../../lib/orgFeatureEntitlements.ts";
import {
  buildUnavailableSelectedChannelMessage,
  getRuleNotificationChannelStates,
} from "../../lib/ruleNotificationChannels.ts";

const baseMe: MeResponse = {
  user: {
    id: "user-1",
    email: "ops@example.com",
    full_name: "Ops",
    role: "org_admin",
    tenant_id: "SH00000001",
    is_active: true,
    created_at: "2026-04-26T00:00:00Z",
    last_login_at: null,
  },
  tenant: {
    id: "SH00000001",
    name: "Demo Org",
    slug: "demo-org",
    is_active: true,
    created_at: "2026-04-26T00:00:00Z",
  },
  plant_ids: ["plant-1"],
  entitlements: {
    premium_feature_grants: [],
    role_feature_matrix: {},
    baseline_features_by_role: {},
    effective_features_by_role: {},
    available_features: [],
    entitlements_version: 1,
  },
};

test("email stays available while premium channels are disabled without entitlement", () => {
  const states = getRuleNotificationChannelStates(baseMe, []);

  assert.equal(states[0]?.channel, "email");
  assert.equal(states[0]?.disabled, false);
  assert.equal(states[1]?.channel, "sms");
  assert.equal(states[1]?.disabled, true);
  assert.equal(states[1]?.helperText, "Enable SMS alerts in organisation settings to use this channel.");
  assert.equal(states[2]?.channel, "whatsapp");
  assert.equal(states[2]?.disabled, true);
  assert.equal(states[2]?.helperText, "Enable WhatsApp alerts in organisation settings to use this channel.");
});

function withPremiumGrants(grants: PremiumOrgGrantKey[]): MeResponse {
  return {
    ...baseMe,
    entitlements: {
      ...baseMe.entitlements!,
      premium_feature_grants: grants,
    },
  };
}

test("premium notification grants unlock their matching channels", () => {
  const me = withPremiumGrants(["notification_sms", "notification_whatsapp"]);

  const states = getRuleNotificationChannelStates(me, ["sms"]);

  assert.equal(states[1]?.disabled, false);
  assert.equal(states[1]?.helperText, "Available with premium notification alerts.");
  assert.equal(states[2]?.disabled, false);
  assert.equal(states[2]?.helperText, "Available with premium notification alerts.");
});

test("single premium notification grants only unlock their matching channel", () => {
  const smsOnly = withPremiumGrants(["notification_sms"]);
  const whatsappOnly = withPremiumGrants(["notification_whatsapp"]);

  const smsStates = getRuleNotificationChannelStates(smsOnly, []);
  const whatsappStates = getRuleNotificationChannelStates(whatsappOnly, []);

  assert.equal(smsStates[1]?.disabled, false);
  assert.equal(smsStates[2]?.disabled, true);
  assert.equal(whatsappStates[1]?.disabled, true);
  assert.equal(whatsappStates[2]?.disabled, false);
});

test("legacy unavailable premium channels stay removable and show a clear warning", () => {
  const states = getRuleNotificationChannelStates(baseMe, ["sms"]);

  assert.equal(states[1]?.disabled, false);
  assert.equal(states[1]?.legacyUnavailable, true);
  assert.match(states[1]?.helperText ?? "", /no longer has access/i);
  assert.match(buildUnavailableSelectedChannelMessage(states) ?? "", /SMS alerts are no longer enabled/i);
});

test("legacy unavailable combined premium channels use one clear organisation warning", () => {
  const states = getRuleNotificationChannelStates(baseMe, ["sms", "whatsapp"]);

  assert.equal(states[1]?.legacyUnavailable, true);
  assert.equal(states[2]?.legacyUnavailable, true);
  assert.equal(
    buildUnavailableSelectedChannelMessage(states),
    "SMS and WhatsApp alerts are no longer enabled for this organisation. Remove those channels or ask a super admin to re-enable them in organisation settings.",
  );
});
