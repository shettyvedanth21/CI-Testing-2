import test, { afterEach, mock } from "node:test";
import assert from "node:assert/strict";

import { clearAccessToken, setAccessToken } from "../../lib/browserSession.ts";
import { apiFetch, configureApiFetchAuthRecovery } from "../../lib/apiFetch.ts";

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

  clear(): void {
    this.store.clear();
  }
}

const TENANT_KEY = "factoryops_selected_tenant";

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
  configureApiFetchAuthRecovery(null);
  mock.restoreAll();
  Reflect.deleteProperty(globalThis, "window");
});

test("protected API fetch refreshes token on first 401 and retries once", async () => {
  const storage = installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  const seenHeaders: Array<{ auth: string | null; tenant: string | null }> = [];
  let callCount = 0;
  mock.method(globalThis, "fetch", async (_input: RequestInfo | URL, init?: RequestInit) => {
    callCount += 1;
    const headers = new Headers(init?.headers);
    seenHeaders.push({
      auth: headers.get("authorization"),
      tenant: headers.get("x-tenant-id"),
    });
    return new Response(null, { status: callCount === 1 ? 401 : 200 });
  });

  let refreshCount = 0;
  let clearCount = 0;
  configureApiFetchAuthRecovery({
    refreshAccessToken: async () => {
      refreshCount += 1;
      const freshToken = makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 });
      setAccessToken(freshToken);
      return freshToken;
    },
    clearSession: () => {
      clearCount += 1;
      storage.clear();
      clearAccessToken();
    },
  });

  const response = await apiFetch("/backend/device/api/v1/devices");

  assert.equal(response.status, 200);
  assert.equal(callCount, 2);
  assert.equal(refreshCount, 1);
  assert.equal(clearCount, 0);
  assert.equal(seenHeaders[0]?.tenant, "SH00000001");
  assert.equal(seenHeaders[1]?.tenant, "SH00000001");
  assert.notEqual(seenHeaders[0]?.auth, null);
  assert.notEqual(seenHeaders[1]?.auth, null);
});

test("protected API fetch omits tenant header when tenant_id is absent", async () => {
  installWindow();
  setAccessToken(makeToken({ role: "org_admin", exp: Math.floor(Date.now() / 1000) + 3600 }));

  let seenTenantHeader: string | null = null;
  mock.method(globalThis, "fetch", async (_input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seenTenantHeader = headers.get("x-tenant-id");
    return new Response(null, { status: 200 });
  });

  const response = await apiFetch("/backend/device/api/v1/devices");

  assert.equal(response.status, 200);
  assert.equal(seenTenantHeader, null);
});

test("concurrent 401 responses trigger only one refresh", async () => {
  const storage = installWindow();
  setAccessToken("stale-token");

  const seenAuthHeaders: string[] = [];
  mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    const auth = headers.get("authorization");
    seenAuthHeaders.push(auth ?? "<none>");
    if (auth === "Bearer stale-token") {
      return new Response(null, { status: 401 });
    }
    return new Response(null, { status: 200 });
  });

  let refreshCount = 0;
  configureApiFetchAuthRecovery({
    refreshAccessToken: async () => {
      refreshCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      setAccessToken("fresh-token");
      return "fresh-token";
    },
    clearSession: () => {
      storage.clear();
      clearAccessToken();
    },
  });

  const [a, b] = await Promise.all([
    apiFetch("/backend/device/api/v1/devices"),
    apiFetch("/backend/data/api/v1/data/telemetry/latest-batch"),
  ]);

  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  assert.equal(refreshCount, 1);
  assert.deepEqual(seenAuthHeaders, [
    "Bearer stale-token",
    "Bearer stale-token",
    "Bearer fresh-token",
    "Bearer fresh-token",
  ]);
});

test("failed refresh clears session cleanly after protected 401", async () => {
  const storage = installWindow();
  setAccessToken(makeToken({ role: "org_admin", tenant_id: "SH00000001", exp: Math.floor(Date.now() / 1000) + 3600 }));

  mock.method(globalThis, "fetch", async () => new Response(null, { status: 401 }));

  let clearCount = 0;
  configureApiFetchAuthRecovery({
    refreshAccessToken: async () => null,
    clearSession: () => {
      clearCount += 1;
      storage.clear();
      clearAccessToken();
    },
  });

  const response = await apiFetch("/backend/device/api/v1/devices");

  assert.equal(response.status, 401);
  assert.equal(clearCount, 1);
  assert.equal(storage.getItem(TENANT_KEY), null);
});

test("super-admin tenant selection headers remain correct after refresh retry", async () => {
  const storage = installWindow();
  setAccessToken(makeToken({ role: "super_admin", tenant_id: null, exp: Math.floor(Date.now() / 1000) + 3600 }));
  storage.setItem(TENANT_KEY, "SH00000042");

  const seenHeaders: Array<{ targetTenant: string | null; tenant: string | null }> = [];
  let callCount = 0;
  mock.method(globalThis, "fetch", async (_input: RequestInfo | URL, init?: RequestInit) => {
    callCount += 1;
    const headers = new Headers(init?.headers);
    seenHeaders.push({
      targetTenant: headers.get("x-target-tenant-id"),
      tenant: headers.get("x-tenant-id"),
    });
    return new Response(null, { status: callCount === 1 ? 401 : 200 });
  });

  configureApiFetchAuthRecovery({
    refreshAccessToken: async () => {
      const freshToken = makeToken({ role: "super_admin", tenant_id: null, exp: Math.floor(Date.now() / 1000) + 3600 });
      setAccessToken(freshToken);
      return freshToken;
    },
    clearSession: () => {
      storage.clear();
      clearAccessToken();
    },
  });

  const response = await apiFetch("/backend/device/api/v1/devices");

  assert.equal(response.status, 200);
  assert.deepEqual(seenHeaders, [
    { targetTenant: "SH00000042", tenant: null },
    { targetTenant: "SH00000042", tenant: null },
  ]);
});
