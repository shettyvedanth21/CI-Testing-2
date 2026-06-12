import test from "node:test";
import assert from "node:assert/strict";

import { bootstrapAuthSession } from "../../lib/authBootstrap.ts";
import type { MeResponse } from "../../lib/authApi.ts";

function makeMe(role: MeResponse["user"]["role"], tenantId: string | null = "SH00000001"): MeResponse {
  return {
    user: {
      id: `${role}-user`,
      email: `${role}@example.com`,
      full_name: `${role} user`,
      role,
      tenant_id: tenantId,
      is_active: true,
      created_at: "2026-04-20T00:00:00Z",
      last_login_at: "2026-04-20T00:00:00Z",
    },
    tenant: tenantId
      ? {
          id: tenantId,
          name: "Tenant",
          slug: "tenant",
          is_active: true,
          created_at: "2026-04-20T00:00:00Z",
        }
      : null,
    plant_ids: [],
    entitlements: null,
  };
}

function createDeps(overrides: Partial<Parameters<typeof bootstrapAuthSession>[1]> = {}) {
  const calls: string[] = [];
  const events: Array<{ type: string; me: MeResponse | null }> = [];
  const cachedMe = overrides.getCachedMe?.() ?? null;
  const resolvedMe = makeMe("org_admin");

  const deps = {
    initializeTenantStore: () => {
      calls.push("initializeTenantStore");
    },
    getCachedMe: () => cachedMe,
    hasValidAccessToken: () => false,
    getMe: async () => {
      calls.push("getMe");
      return resolvedMe;
    },
    refreshAccessToken: async () => {
      calls.push("refreshAccessToken");
      return "fresh-access-token";
    },
    clearSession: () => {
      calls.push("clearSession");
    },
    onCachedMe: (me: MeResponse) => {
      events.push({ type: "cached", me });
    },
    onResolvedMe: (me: MeResponse) => {
      events.push({ type: "resolved", me });
    },
    onLoggedOut: () => {
      events.push({ type: "logged_out", me: null });
    },
    ...overrides,
  };

  return { deps, calls, events, resolvedMe };
}

test("cold startup with valid refresh restores session without calling me first", async () => {
  const { deps, calls, events, resolvedMe } = createDeps({
    hasValidAccessToken: () => false,
  });

  const me = await bootstrapAuthSession({}, deps);

  assert.deepEqual(calls, ["initializeTenantStore", "refreshAccessToken", "getMe"]);
  assert.deepEqual(events, [{ type: "resolved", me: resolvedMe }]);
  assert.deepEqual(me, resolvedMe);
});

test("cold startup with invalid refresh ends in clean logged-out state", async () => {
  const { deps, calls, events } = createDeps({
    refreshAccessToken: async () => {
      calls.push("refreshAccessToken");
      return null;
    },
  });

  const me = await bootstrapAuthSession({}, deps);

  assert.deepEqual(calls, ["initializeTenantStore", "refreshAccessToken", "clearSession"]);
  assert.deepEqual(events, [{ type: "logged_out", me: null }]);
  assert.equal(me, null);
});

test("dead refresh cookie path does not loop retries", async () => {
  const { deps, calls } = createDeps({
    refreshAccessToken: async () => {
      calls.push("refreshAccessToken");
      return null;
    },
  });

  await bootstrapAuthSession({}, deps);

  assert.equal(calls.filter((call) => call === "refreshAccessToken").length, 1);
  assert.equal(calls.includes("getMe"), false);
});

test("super-admin startup restore still works without selected tenant", async () => {
  const superAdminMe = makeMe("super_admin", null);
  const { deps, calls, events } = createDeps({
    getMe: async () => {
      calls.push("getMe");
      return superAdminMe;
    },
  });

  const me = await bootstrapAuthSession({}, deps);

  assert.deepEqual(calls, ["initializeTenantStore", "refreshAccessToken", "getMe"]);
  assert.deepEqual(events, [{ type: "resolved", me: superAdminMe }]);
  assert.deepEqual(me, superAdminMe);
});

test("org-admin startup restore still works with a valid in-memory token", async () => {
  const orgAdminMe = makeMe("org_admin");
  const { deps, calls, events } = createDeps({
    hasValidAccessToken: () => true,
    getMe: async () => {
      calls.push("getMe");
      return orgAdminMe;
    },
  });

  const me = await bootstrapAuthSession({}, deps);

  assert.deepEqual(calls, ["initializeTenantStore", "getMe"]);
  assert.deepEqual(events, [{ type: "resolved", me: orgAdminMe }]);
  assert.deepEqual(me, orgAdminMe);
});
