import { buildUrl, runtimeConfig } from './config.js';
import { requestJson, parseJson } from './http.js';

let authSession = null;
let resolvedTenantId = runtimeConfig.scope.tenantId || '';

function nextExpiry(expiresInSeconds) {
  const ttlMs = Math.max(1, Number(expiresInSeconds || 0)) * 1000;
  return Date.now() + ttlMs;
}

function authBaseTags(extra = {}) {
  return {
    domain: 'auth',
    ...extra,
  };
}

function parseTokenResponse(response) {
  const payload = parseJson(response) || {};
  return {
    accessToken: payload.access_token,
    expiresAt: nextExpiry(payload.expires_in),
    tokenType: payload.token_type || 'bearer',
  };
}

export function setResolvedTenantId(tenantId) {
  if (tenantId) {
    resolvedTenantId = tenantId;
  }
}

export function getResolvedTenantId() {
  return resolvedTenantId || runtimeConfig.scope.tenantId || '';
}

export function login(extraTags = {}) {
  const response = requestJson(
    'POST',
    buildUrl('auth', '/api/v1/auth/login'),
    {
      email: runtimeConfig.credentials.email,
      password: runtimeConfig.credentials.password,
    },
    {
      expectedStatuses: [200],
      tags: authBaseTags({ endpoint: 'login', name: 'auth.login', ...extraTags }),
    },
  );
  authSession = parseTokenResponse(response);
  return authSession;
}

export function refresh(extraTags = {}) {
  const response = requestJson(
    'POST',
    buildUrl('auth', '/api/v1/auth/refresh'),
    null,
    {
      expectedStatuses: [200, 401],
      tags: authBaseTags({ endpoint: 'refresh', name: 'auth.refresh', ...extraTags }),
    },
  );

  if (response.status === 401) {
    return login(extraTags);
  }

  authSession = parseTokenResponse(response);
  return authSession;
}

export function ensureSession(extraTags = {}) {
  if (!authSession || !authSession.accessToken) {
    return login(extraTags);
  }
  if (Date.now() + 60_000 >= authSession.expiresAt) {
    return refresh(extraTags);
  }
  return authSession;
}

export function authHeaders() {
  const session = ensureSession();
  const tenantId = getResolvedTenantId();
  return {
    Authorization: `Bearer ${session.accessToken}`,
    ...(tenantId ? { 'X-Tenant-Id': tenantId } : {}),
  };
}

export function loadMe(extraTags = {}) {
  const response = requestJson('GET', buildUrl('auth', '/api/v1/auth/me'), null, {
    expectedStatuses: [200],
    headers: authHeaders(),
    tags: authBaseTags({ endpoint: 'me', name: 'auth.me', ...extraTags }),
  });
  const payload = parseJson(response) || {};
  const tenantId = resolvedTenantId || payload?.tenant?.id || payload?.user?.tenant_id || '';
  setResolvedTenantId(tenantId);
  return payload;
}
