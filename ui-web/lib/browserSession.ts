"use client";

type AccessTokenClaims = {
  role?: string;
  tenant_id?: string | null;
  exp?: number;
};

const LEGACY_ACCESS_TOKEN_KEY = "factoryops_access_token";
const LEGACY_REFRESH_TOKEN_KEY = "factoryops_refresh_token";
const ACCESS_TOKEN_KEY = "factoryops_access_token_v2";

let accessToken: string | null = null;

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function hydrateLegacyAccessToken(): void {
  if (accessToken || !isBrowser()) {
    return;
  }

  const persistedToken = window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (persistedToken) {
    accessToken = persistedToken;
    return;
  }

  const legacyToken = window.sessionStorage.getItem(LEGACY_ACCESS_TOKEN_KEY);
  if (legacyToken) {
    accessToken = legacyToken;
    window.sessionStorage.setItem(ACCESS_TOKEN_KEY, legacyToken);
  }
  window.sessionStorage.removeItem(LEGACY_REFRESH_TOKEN_KEY);
}

function decodeClaims(token: string | null): AccessTokenClaims | null {
  if (!token) {
    return null;
  }

  try {
    const parts = token.split(".");
    if (parts.length !== 3) {
      return null;
    }
    return JSON.parse(atob(parts[1])) as AccessTokenClaims;
  } catch {
    return null;
  }
}

function isTokenValid(claims: AccessTokenClaims | null): boolean {
  if (!claims || typeof claims.exp !== "number") {
    return false;
  }

  return claims.exp * 1000 > Date.now();
}

export function getAccessToken(): string | null {
  hydrateLegacyAccessToken();
  return accessToken;
}

export function getAccessTokenClaims(): AccessTokenClaims | null {
  hydrateLegacyAccessToken();
  return decodeClaims(accessToken);
}

export function hasValidAccessToken(): boolean {
  hydrateLegacyAccessToken();
  return isTokenValid(decodeClaims(accessToken));
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  if (isBrowser()) {
    if (token) {
      window.sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
    } else {
      window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    }
    window.sessionStorage.removeItem(LEGACY_REFRESH_TOKEN_KEY);
  }
}

export function clearAccessToken(): void {
  accessToken = null;
  if (isBrowser()) {
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    window.sessionStorage.removeItem(LEGACY_ACCESS_TOKEN_KEY);
    window.sessionStorage.removeItem(LEGACY_REFRESH_TOKEN_KEY);
  }
}
