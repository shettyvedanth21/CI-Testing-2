import { getAccessToken, getAccessTokenClaims } from "./browserSession.ts";
import { initializeTenantStore, tenantStore } from "./tenantStore.js";

type ApiFetchOptions = RequestInit & {
  bypassTenantCheck?: boolean;
};

type AuthRecoveryHooks = {
  refreshAccessToken: () => Promise<string | null>;
  clearSession: () => void;
};

let authRecoveryHooks: AuthRecoveryHooks | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export class TenantNotSelectedError extends Error {
  constructor() {
    super("Select an organisation before making tenant-scoped requests.");
    this.name = "TenantNotSelectedError";
  }
}

export function configureApiFetchAuthRecovery(hooks: AuthRecoveryHooks | null): void {
  authRecoveryHooks = hooks;
}

type AccessTokenClaims = ReturnType<typeof getAccessTokenClaims>;

function getClaimTenantId(claims: AccessTokenClaims | null): string | null {
  return claims?.tenant_id ?? null;
}

function isAuthRequest(url: string): boolean {
  return (
    url.includes(":8090") ||
    url.includes("/api/v1/auth/") ||
    url.includes("/api/admin/") ||
    url.includes("/api/v1/tenants/")
  );
}

async function refreshAccessTokenSingleFlight(): Promise<string | null> {
  if (!authRecoveryHooks) {
    return null;
  }
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = authRecoveryHooks.refreshAccessToken().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

function applyTenantHeaders(
  headers: Headers,
  url: string,
  claims: AccessTokenClaims | null,
  bypassTenantCheck: boolean,
): void {
  if (!claims?.role) {
    return;
  }

  if (claims.role === "super_admin") {
    const selectedTenantId = tenantStore.selectedTenantId;
    const canBypass = bypassTenantCheck || isAuthRequest(url);
    if (!selectedTenantId && !canBypass) {
      throw new TenantNotSelectedError();
    }
    if (selectedTenantId) {
      headers.set("X-Target-Tenant-Id", selectedTenantId);
    }
    headers.delete("X-Tenant-Id");
    return;
  }

  const tenantId = getClaimTenantId(claims);
  if (tenantId) {
    headers.set("X-Tenant-Id", tenantId);
  }
  headers.delete("X-Target-Tenant-Id");
}

async function performApiFetch(url: string, options: ApiFetchOptions = {}): Promise<Response> {
  initializeTenantStore();

  const token = getAccessToken();
  const claims = getAccessTokenClaims();
  const headers = new Headers(options.headers);

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  applyTenantHeaders(headers, url, claims, Boolean(options.bypassTenantCheck));

  const { bypassTenantCheck: _bypassTenantCheck, ...requestInit } = options;
  return fetch(url, {
    ...requestInit,
    headers,
  });
}

export async function apiFetch(url: string, options: ApiFetchOptions = {}): Promise<Response> {
  const response = await performApiFetch(url, options);
  if (response.status !== 401 || isAuthRequest(url) || !authRecoveryHooks) {
    return response;
  }

  const refreshedToken = await refreshAccessTokenSingleFlight();
  if (!refreshedToken) {
    authRecoveryHooks.clearSession();
    return response;
  }

  const retriedResponse = await performApiFetch(url, options);
  if (retriedResponse.status === 401) {
    authRecoveryHooks.clearSession();
  }
  return retriedResponse;
}
