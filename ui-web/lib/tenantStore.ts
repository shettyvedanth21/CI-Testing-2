"use client";

import { useSyncExternalStore } from "react";
import { getAccessTokenClaims } from "./browserSession.ts";

const TENANT_KEY = "factoryops_selected_tenant";

type UserRole = "super_admin" | "org_admin" | "plant_manager" | "operator" | "viewer";

type AccessTokenClaims = {
  role?: UserRole;
  tenant_id?: string | null;
  exp?: number;
};

type TenantState = {
  selectedTenantId: string | null;
};

const listeners = new Set<() => void>();
const state: TenantState = {
  selectedTenantId: null,
};

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

function getCurrentClaims(): AccessTokenClaims | null {
  return getAccessTokenClaims() as AccessTokenClaims | null;
}

function getClaimTenantId(claims: AccessTokenClaims | null): string | null {
  return claims?.tenant_id ?? null;
}

function isTokenValid(claims: AccessTokenClaims | null): claims is AccessTokenClaims {
  if (!claims || !claims.role) {
    return false;
  }
  if (typeof claims.exp !== "number") {
    return false;
  }
  return claims.exp * 1000 > Date.now();
}

function writeSelectedTenantId(selectedTenantId: string | null): void {
  if (!isBrowser()) {
    return;
  }
  if (selectedTenantId) {
    window.sessionStorage.setItem(TENANT_KEY, selectedTenantId);
  } else {
    window.sessionStorage.removeItem(TENANT_KEY);
  }
}

function setState(selectedTenantId: string | null): void {
  if (state.selectedTenantId === selectedTenantId) {
    return;
  }
  state.selectedTenantId = selectedTenantId;
  emit();
}

function readPersistedTenantId(): string | null {
  if (!isBrowser()) {
    return null;
  }
  const raw = window.sessionStorage.getItem(TENANT_KEY);
  return raw && raw.trim().length > 0 ? raw : null;
}

export function initializeTenantStore(): void {
  if (!isBrowser()) {
    return;
  }

  const claims = getCurrentClaims();
  if (!isTokenValid(claims)) {
    setState(readPersistedTenantId());
    return;
  }

  if (claims.role !== "super_admin") {
    const tenantId = getClaimTenantId(claims);
    writeSelectedTenantId(tenantId);
    setState(tenantId);
    return;
  }

  setState(readPersistedTenantId());
}

export function setSelectedTenantId(id: string): void {
  const claims = getCurrentClaims();
  if (!isTokenValid(claims)) {
    writeSelectedTenantId(null);
    setState(null);
    return;
  }

  if (claims.role !== "super_admin") {
    const tenantId = getClaimTenantId(claims);
    writeSelectedTenantId(tenantId);
    setState(tenantId);
    return;
  }

  const nextId = id.trim();
  if (!nextId) {
    return;
  }

  writeSelectedTenantId(nextId);
  setState(nextId);
}

export function clearSelectedTenant(): void {
  writeSelectedTenantId(null);
  setState(null);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): string | null {
  return state.selectedTenantId;
}

export function useTenantStore(): TenantState {
  const selectedTenantId = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { selectedTenantId };
}

export const tenantStore = {
  get selectedTenantId(): string | null {
    return state.selectedTenantId;
  },
  setSelectedTenantId,
  clearSelectedTenant,
  initializeTenantStore,
  subscribe,
  getSnapshot,
};

export const selectedTenantId = (): string | null => tenantStore.selectedTenantId;
