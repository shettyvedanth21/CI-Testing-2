import test from "node:test";
import assert from "node:assert/strict";

import { getLifecycleActions, getLifecycleStatus } from "../../lib/userLifecycle.ts";
import type { UserProfile } from "../../lib/authApi.ts";

function buildUser(overrides: Partial<UserProfile>): UserProfile {
  return {
    id: "user-1",
    email: "user@example.com",
    full_name: "User One",
    role: "viewer",
    tenant_id: "org-a",
    is_active: false,
    created_at: "2026-04-18T00:00:00Z",
    last_login_at: null,
    ...overrides,
  };
}

test("pending invite users show invited status and resend action", () => {
  const user = buildUser({ lifecycle_state: "invited", invite_status: "pending", can_resend_invite: true });
  assert.deepEqual(getLifecycleStatus(user), { label: "Invited", variant: "info" });
  assert.deepEqual(getLifecycleActions(user), ["resend_invite"]);
});

test("expired invite users show warning status and reinvite action", () => {
  const user = buildUser({ lifecycle_state: "invite_expired", invite_status: "expired", can_resend_invite: true });
  assert.deepEqual(getLifecycleStatus(user), { label: "Invite expired", variant: "warning" });
  assert.deepEqual(getLifecycleActions(user), ["reinvite"]);
});

test("deactivated active-once users expose reactivate action", () => {
  const user = buildUser({ lifecycle_state: "deactivated", can_reactivate: true });
  assert.deepEqual(getLifecycleStatus(user), { label: "Deactivated", variant: "default" });
  assert.deepEqual(getLifecycleActions(user), ["reactivate"]);
});

test("active users expose deactivate action", () => {
  const user = buildUser({ is_active: true, lifecycle_state: "active", can_deactivate: true });
  assert.deepEqual(getLifecycleStatus(user), { label: "Active", variant: "success" });
  assert.deepEqual(getLifecycleActions(user), ["deactivate"]);
});
