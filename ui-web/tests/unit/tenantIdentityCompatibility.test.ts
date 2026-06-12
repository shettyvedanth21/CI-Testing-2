import test, { afterEach } from "node:test";
import assert from "node:assert/strict";

import { clearAccessToken, setAccessToken } from "../../lib/browserSession.ts";
import { resolveScopedTenantId } from "../../lib/orgScope.ts";
import { clearSelectedTenant, initializeTenantStore, selectedTenantId } from "../../lib/tenantStore.ts";

class SessionStorageMock {
  private readonly store = new Map<string, string>();

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }
}

function base64UrlEncode(value: object): string {
  return Buffer.from(JSON.stringify(value), "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function makeToken(payload: object): string {
  return `header.${base64UrlEncode(payload)}.signature`;
}

function installWindow(): SessionStorageMock {
  const sessionStorage = new SessionStorageMock();
  Object.defineProperty(globalThis, "window", {
    value: { sessionStorage },
    configurable: true,
    writable: true,
  });
  return sessionStorage;
}

afterEach(() => {
  clearAccessToken();
  clearSelectedTenant();
  Reflect.deleteProperty(globalThis, "window");
});

test("tenant store initializes non-super-admin scope from canonical tenant_id first", () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000042", exp: Math.floor(Date.now() / 1000) + 3600 }));

  initializeTenantStore();

  assert.equal(selectedTenantId(), "SH00000042");
});

test("tenant store preserves persisted super-admin selection until auth refresh restores claims", () => {
  const storage = installWindow();
  storage.setItem("factoryops_selected_tenant", "SH00000013");

  initializeTenantStore();

  assert.equal(selectedTenantId(), "SH00000013");
  assert.equal(storage.getItem("factoryops_selected_tenant"), "SH00000013");
});

test("resolveScopedTenantId prefers canonical tenant_id", () => {
  assert.equal(
    resolveScopedTenantId(
      {
        user: {
          id: "user-1",
          email: "user@example.com",
          full_name: "User",
          role: "org_admin",
          tenant_id: "SH00000007",
          is_active: true,
          created_at: "2026-04-11T00:00:00Z",
          last_login_at: null,
        },
        tenant: { id: "tenant-from-record", name: "Tenant", slug: "tenant", is_active: true, created_at: "2026-04-11T00:00:00Z" },
        plant_ids: [],
        entitlements: null,
      },
      null,
    ),
    "SH00000007",
  );

  assert.equal(
    resolveScopedTenantId(
      {
        user: {
          id: "user-2",
          email: "user2@example.com",
          full_name: "User Two",
          role: "org_admin",
          tenant_id: null,
          is_active: true,
          created_at: "2026-04-11T00:00:00Z",
          last_login_at: null,
        },
        tenant: null,
        plant_ids: [],
        entitlements: null,
      },
      null,
    ),
    null,
  );
});
