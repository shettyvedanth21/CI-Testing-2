"use client";

import type { MeResponse } from "./authApi.ts";
import { hasValidAccessToken } from "./browserSession.ts";
import { initializeTenantStore } from "./tenantStore.ts";

type AuthBootstrapCallbacks = {
  onCachedMe?: (me: MeResponse) => void;
  onResolvedMe?: (me: MeResponse) => void;
  onLoggedOut?: () => void;
};

type AuthBootstrapDeps = AuthBootstrapCallbacks & {
  initializeTenantStore: () => void;
  getCachedMe: () => MeResponse | null;
  hasValidAccessToken: () => boolean;
  getMe: () => Promise<MeResponse>;
  refreshAccessToken: () => Promise<string | null>;
  clearSession: () => void;
};

async function loadMe(deps: AuthBootstrapDeps): Promise<MeResponse | null> {
  try {
    const me = await deps.getMe();
    deps.onResolvedMe?.(me);
    return me;
  } catch {
    deps.clearSession();
    deps.onLoggedOut?.();
    return null;
  }
}

async function createDefaultDeps(callbacks: AuthBootstrapCallbacks): Promise<AuthBootstrapDeps> {
  const { authApi, tokenStore } = await import("./authApi.ts");

  return {
    ...callbacks,
    initializeTenantStore,
    getCachedMe: () => tokenStore.getMeData(),
    hasValidAccessToken,
    getMe: () => authApi.getMe(),
    refreshAccessToken: () => authApi.refreshAccessToken(),
    clearSession: () => tokenStore.clearAll(),
  };
}

export async function bootstrapAuthSession(
  callbacks: AuthBootstrapCallbacks = {},
  deps?: AuthBootstrapDeps,
): Promise<MeResponse | null> {
  const resolvedDeps = deps ?? (await createDefaultDeps(callbacks));

  resolvedDeps.initializeTenantStore();

  const cachedMe = resolvedDeps.getCachedMe();
  if (cachedMe) {
    resolvedDeps.onCachedMe?.(cachedMe);
  }

  if (resolvedDeps.hasValidAccessToken()) {
    return loadMe(resolvedDeps);
  }

  const refreshedAccessToken = await resolvedDeps.refreshAccessToken();
  if (!refreshedAccessToken) {
    resolvedDeps.clearSession();
    resolvedDeps.onLoggedOut?.();
    return null;
  }

  return loadMe(resolvedDeps);
}
