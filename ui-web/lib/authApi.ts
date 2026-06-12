import { clearAccessToken, getAccessToken, setAccessToken } from "./browserSession.ts";
import { clearSelectedTenant, initializeTenantStore } from "./tenantStore.js";
import { apiFetch, configureApiFetchAuthRecovery } from "./apiFetch";
import type { PremiumOrgGrantKey } from "./orgFeatureEntitlements";

const AUTH_BASE = "/backend/auth";

export type UserRole =
  | "super_admin"
  | "org_admin"
  | "plant_manager"
  | "operator"
  | "viewer";

export interface UserProfile {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  tenant_id: string | null;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  lifecycle_state?: "invited" | "invite_expired" | "active" | "deactivated" | null;
  invite_status?: "pending" | "expired" | "none" | null;
  pending_invite_expires_at?: string | null;
  can_resend_invite?: boolean | null;
  can_reactivate?: boolean | null;
  can_deactivate?: boolean | null;
}

export interface TenantProfile {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string;
}

export interface SuperAdminSummary {
  total_organisations: number;
  total_active_devices: number;
}

export interface FeatureEntitlements {
  premium_feature_grants: PremiumOrgGrantKey[];
  role_feature_matrix: Record<string, string[]>;
  baseline_features_by_role: Record<string, string[]>;
  effective_features_by_role: Record<string, string[]>;
  available_features: string[];
  entitlements_version: number;
}

export interface PlantDeleteGuard {
  can_delete: boolean;
  device_count: number;
  code?: string;
  message: string;
}

export interface PlantProfile {
  id: string;
  tenant_id: string;
  name: string;
  slug?: string | null;
  location: string | null;
  timezone: string;
  is_active: boolean;
  created_at: string;
  updated_at?: string | null;
}

export type PlatformMaintenanceSeverity = "info" | "warning" | "critical";
export type PlatformMaintenanceStatus = "draft" | "scheduled" | "active" | "completed" | "cancelled";

export interface PlatformMaintenanceAnnouncement {
  id: string;
  title: string;
  severity: PlatformMaintenanceSeverity;
  message: string;
  starts_at: string;
  estimated_duration_minutes: number;
  ends_at: string;
  status: PlatformMaintenanceStatus;
  effective_status: PlatformMaintenanceStatus;
  broadcast_all_tenants: boolean;
  target_tenant_ids: string[];
  created_by: string;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CurrentPlatformMaintenanceResponse {
  tenant_id: string;
  announcements: PlatformMaintenanceAnnouncement[];
}

export interface PlatformMaintenanceAnnouncementWritePayload {
  title: string;
  severity: PlatformMaintenanceSeverity;
  message: string;
  starts_at: string;
  estimated_duration_minutes: number;
  status: PlatformMaintenanceStatus;
  broadcast_all_tenants: boolean;
  target_tenant_ids: string[];
}

export interface PlatformMaintenanceAnnouncementUpdatePayload {
  title?: string;
  severity?: PlatformMaintenanceSeverity;
  message?: string;
  starts_at?: string;
  estimated_duration_minutes?: number;
  status?: PlatformMaintenanceStatus;
  broadcast_all_tenants?: boolean;
  target_tenant_ids?: string[];
}

export interface MeResponse {
  user: UserProfile;
  tenant: TenantProfile | null;
  plant_ids: string[];
  entitlements: FeatureEntitlements | null;
}

type TenantScopedPayload = {
  tenant_id?: string | null;
};

function resolveTenantId(payload: TenantScopedPayload): string {
  const tenantId = payload.tenant_id ?? null;
  if (!tenantId) {
    throw new Error("tenant_id is required");
  }
  return tenantId;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type: string;
  expires_in: number;
}

export interface ActionTokenStatus {
  status: "valid" | "expired" | "used" | "invalid";
  action_type: "invite_set_password" | "password_reset" | null;
  email: string | null;
  full_name: string | null;
}

const ME_KEY = "factoryops_me";
export const AUTH_STATE_CHANGE_EVENT = "factoryops-auth-state-change";

let refreshAccessTokenInFlight: Promise<string | null> | null = null;

type AuthStateChangeDetail = {
  me: MeResponse | null;
};

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function notifyAuthStateChange(me: MeResponse | null): void {
  if (!isBrowser()) {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<AuthStateChangeDetail>(AUTH_STATE_CHANGE_EVENT, {
      detail: { me },
    }),
  );
}

export function subscribeToAuthStateChange(listener: (me: MeResponse | null) => void): () => void {
  if (!isBrowser()) {
    return () => {};
  }

  const handleEvent = (event: Event) => {
    const detail = (event as CustomEvent<AuthStateChangeDetail>).detail;
    listener(detail?.me ?? null);
  };

  window.addEventListener(AUTH_STATE_CHANGE_EVENT, handleEvent);
  return () => {
    window.removeEventListener(AUTH_STATE_CHANGE_EVENT, handleEvent);
  };
}

function extractMessage(body: unknown): string | null {
  if (!body || typeof body !== "object") {
    return null;
  }

  const record = body as Record<string, unknown>;
  const message = record.message;
  if (typeof message === "string" && message.trim().length > 0) {
    return message;
  }

  const detail = record.detail;
  if (typeof detail === "string" && detail.trim().length > 0) {
    return detail;
  }

  if (detail && typeof detail === "object") {
    const detailRecord = detail as Record<string, unknown>;
    const detailMessage = detailRecord.message;
    if (typeof detailMessage === "string" && detailMessage.trim().length > 0) {
      const extraDetails = detailRecord.details;
      if (Array.isArray(extraDetails)) {
        const firstDetail = extraDetails.find(
          (entry) => entry && typeof entry === "object" && typeof (entry as Record<string, unknown>).msg === "string",
        ) as Record<string, unknown> | undefined;
        const firstMessage = firstDetail?.msg;
        if (typeof firstMessage === "string" && firstMessage.trim().length > 0) {
          return `${detailMessage}: ${firstMessage}`;
        }
      }
      return detailMessage;
    }
  }

  return null;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { message: text };
  }
}

export const tokenStore = {
  getAccessToken(): string | null {
    return getAccessToken();
  },

  setAccessToken(token: string | null): void {
    setAccessToken(token);
    initializeTenantStore();
  },

  getMeData(): MeResponse | null {
    if (!isBrowser()) {
      return null;
    }

    const raw = window.sessionStorage.getItem(ME_KEY);
    if (!raw) {
      return null;
    }

    try {
      return JSON.parse(raw) as MeResponse;
    } catch {
      return null;
    }
  },

  setMeData(me: MeResponse): void {
    if (!isBrowser()) {
      return;
    }
    window.sessionStorage.setItem(ME_KEY, JSON.stringify(me));
    notifyAuthStateChange(me);
  },

  clearAll(): void {
    clearAccessToken();
    if (!isBrowser()) {
      initializeTenantStore();
      return;
    }
    window.sessionStorage.removeItem(ME_KEY);
    clearSelectedTenant();
    notifyAuthStateChange(null);
  },
};

async function authFetch<T>(path: string, options: RequestInit = {}, retried = false): Promise<T> {
  const headers = new Headers(options.headers);
  const token = tokenStore.getAccessToken();

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  headers.set("Content-Type", "application/json");

  const response = await apiFetch(`${AUTH_BASE}${path}`, {
    ...options,
    bypassTenantCheck: true,
    credentials: "include",
    headers,
  });

  if (response.status === 401) {
    if (retried) {
      tokenStore.clearAll();
      throw new Error("SESSION_EXPIRED");
    }

    const refreshed = await authApi.refreshAccessToken();
    if (!refreshed) {
      tokenStore.clearAll();
      throw new Error("SESSION_EXPIRED");
    }

    return authFetch<T>(path, options, true);
  }

  const body = await readJson(response);

  if (!response.ok) {
    throw new Error(extractMessage(body) ?? "Request failed");
  }

  return body as T;
}

export const authApi = {
  clearSession(): void {
    tokenStore.clearAll();
  },

  async login(email: string, password: string): Promise<MeResponse> {
    const response = await apiFetch(`${AUTH_BASE}/api/v1/auth/login`, {
      method: "POST",
      bypassTenantCheck: true,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, password }),
    });

    const body = await readJson(response);

    if (response.status === 401) {
      throw new Error("Invalid email or password");
    }

    if (response.status === 403) {
      const message = extractMessage(body) ?? "Account is disabled";
      throw new Error(message);
    }

    if (!response.ok) {
      throw new Error(extractMessage(body) ?? "Request failed");
    }

    const tokenResponse = body as TokenResponse;
    tokenStore.setAccessToken(tokenResponse.access_token ?? null);

    const me = await this.getMe();
    tokenStore.setMeData(me);
    return me;
  },

  async logout(): Promise<void> {
    try {
      await apiFetch(`${AUTH_BASE}/api/v1/auth/logout`, {
        method: "POST",
        bypassTenantCheck: true,
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
      });
    } catch {
      // Logout should never block the client from clearing local auth state.
    } finally {
      tokenStore.clearAll();
    }
  },

  async refreshAccessToken(): Promise<string | null> {
    if (refreshAccessTokenInFlight) {
      return refreshAccessTokenInFlight;
    }

    refreshAccessTokenInFlight = (async () => {
      try {
        const response = await apiFetch(`${AUTH_BASE}/api/v1/auth/refresh`, {
          method: "POST",
          bypassTenantCheck: true,
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
        });

        if (!response.ok) {
          tokenStore.setAccessToken(null);
          return null;
        }

        const body = (await readJson(response)) as TokenResponse;
        tokenStore.setAccessToken(body.access_token ?? null);
        return body.access_token;
      } catch {
        tokenStore.setAccessToken(null);
        return null;
      } finally {
        refreshAccessTokenInFlight = null;
      }
    })();

    return refreshAccessTokenInFlight;
  },

  async getMe(): Promise<MeResponse> {
    const me = await authFetch<MeResponse>("/api/v1/auth/me", {
      method: "GET",
    });
    tokenStore.setMeData(me);
    return me;
  },

  async createTenant(data: { name: string; slug: string }): Promise<TenantProfile> {
    return authFetch<TenantProfile>("/api/admin/tenants", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  async listTenants(): Promise<TenantProfile[]> {
    return authFetch<TenantProfile[]>("/api/admin/tenants", {
      method: "GET",
    });
  },

  async getSuperAdminSummary(): Promise<SuperAdminSummary> {
    return authFetch<SuperAdminSummary>("/api/admin/summary", {
      method: "GET",
    });
  },

  async listPlatformMaintenanceAnnouncements(): Promise<PlatformMaintenanceAnnouncement[]> {
    return authFetch<PlatformMaintenanceAnnouncement[]>("/api/admin/platform-maintenance", {
      method: "GET",
    });
  },

  async getCurrentPlatformMaintenance(): Promise<CurrentPlatformMaintenanceResponse> {
    return authFetch<CurrentPlatformMaintenanceResponse>("/api/v1/platform-maintenance/current", {
      method: "GET",
    });
  },

  async getPlatformMaintenanceAnnouncement(announcementId: string): Promise<PlatformMaintenanceAnnouncement> {
    return authFetch<PlatformMaintenanceAnnouncement>(`/api/admin/platform-maintenance/${encodeURIComponent(announcementId)}`, {
      method: "GET",
    });
  },

  async createPlatformMaintenanceAnnouncement(
    data: PlatformMaintenanceAnnouncementWritePayload,
  ): Promise<PlatformMaintenanceAnnouncement> {
    return authFetch<PlatformMaintenanceAnnouncement>("/api/admin/platform-maintenance", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  async updatePlatformMaintenanceAnnouncement(
    announcementId: string,
    data: PlatformMaintenanceAnnouncementUpdatePayload,
  ): Promise<PlatformMaintenanceAnnouncement> {
    return authFetch<PlatformMaintenanceAnnouncement>(`/api/admin/platform-maintenance/${encodeURIComponent(announcementId)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  async deletePlatformMaintenanceAnnouncement(announcementId: string): Promise<void> {
    await authFetch<void>(`/api/admin/platform-maintenance/${encodeURIComponent(announcementId)}`, {
      method: "DELETE",
    });
  },

  async suspendTenant(tenantId: string): Promise<TenantProfile> {
    return authFetch<TenantProfile>(`/api/admin/tenants/${tenantId}/suspend`, {
      method: "PATCH",
    });
  },

  async reactivateTenant(tenantId: string): Promise<TenantProfile> {
    return authFetch<TenantProfile>(`/api/admin/tenants/${tenantId}/reactivate`, {
      method: "PATCH",
    });
  },

  async getTenantEntitlements(tenantId: string): Promise<FeatureEntitlements> {
    return authFetch<FeatureEntitlements>(`/api/v1/tenants/${tenantId}/entitlements`, {
      method: "GET",
    });
  },

  async updateTenantEntitlements(
    tenantId: string,
    data: {
      premium_feature_grants?: PremiumOrgGrantKey[] | null;
      role_feature_matrix?: Record<string, string[]> | null;
    },
  ): Promise<FeatureEntitlements> {
    return authFetch<FeatureEntitlements>(`/api/v1/tenants/${tenantId}/entitlements`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async createTenantAdmin(data: {
    email: string;
    full_name: string;
    tenant_id: string;
  }): Promise<UserProfile> {
    const tenantId = resolveTenantId(data);
    return authFetch<UserProfile>(`/api/v1/tenants/${tenantId}/users`, {
      method: "POST",
      body: JSON.stringify({
        role: "org_admin",
        email: data.email,
        full_name: data.full_name,
        tenant_id: tenantId,
        plant_ids: [],
      }),
    });
  },

  async createPlant(
    tenantId: string,
    data: {
      name: string;
      location?: string;
      timezone?: string;
    },
  ): Promise<PlantProfile> {
    return authFetch<PlantProfile>(`/api/v1/tenants/${tenantId}/plants`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  async updatePlant(
    tenantId: string,
    plantId: string,
    data: {
      name: string;
      location?: string;
      timezone?: string;
    },
  ): Promise<PlantProfile> {
    return authFetch<PlantProfile>(`/api/v1/tenants/${tenantId}/plants/${plantId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async listPlants(tenantId: string): Promise<PlantProfile[]> {
    return authFetch<PlantProfile[]>(`/api/v1/tenants/${tenantId}/plants`, {
      method: "GET",
    });
  },

  async deactivatePlant(tenantId: string, plantId: string): Promise<PlantProfile> {
    return authFetch<PlantProfile>(`/api/v1/tenants/${tenantId}/plants/${plantId}/deactivate`, {
      method: "PATCH",
    });
  },

  async reactivatePlant(tenantId: string, plantId: string): Promise<PlantProfile> {
    return authFetch<PlantProfile>(`/api/v1/tenants/${tenantId}/plants/${plantId}/reactivate`, {
      method: "PATCH",
    });
  },

  async getPlantDeleteGuard(tenantId: string, plantId: string): Promise<PlantDeleteGuard> {
    return authFetch<PlantDeleteGuard>(`/api/v1/tenants/${tenantId}/plants/${plantId}/delete-guard`, {
      method: "GET",
    });
  },

  async inviteUser(
    tenantId: string,
    data: {
      email: string;
      full_name: string;
      role: "plant_manager" | "operator" | "viewer";
      tenant_id?: string;
      plant_ids: string[];
    },
  ): Promise<UserProfile> {
    const scopedTenantId = resolveTenantId({
      tenant_id: data.tenant_id ?? tenantId,
    });
    return authFetch<UserProfile>(`/api/v1/tenants/${tenantId}/users`, {
      method: "POST",
      body: JSON.stringify({
        ...data,
        tenant_id: scopedTenantId,
      }),
    });
  },

  async listTenantUsers(tenantId: string): Promise<UserProfile[]> {
    return authFetch<UserProfile[]>(`/api/v1/tenants/${tenantId}/users`, {
      method: "GET",
    });
  },

  async getUserPlantIds(tenantId: string, userId: string): Promise<string[]> {
    const result = await authFetch<{ plant_ids: string[] }>(`/api/v1/tenants/${tenantId}/users/${userId}/plant-access`, {
      method: "GET",
    });
    return result.plant_ids;
  },

  async updateUser(
    tenantId: string,
    userId: string,
    data: {
      full_name?: string;
      role?: string;
      is_active?: boolean;
      plant_ids?: string[];
    },
  ): Promise<UserProfile> {
    return authFetch<UserProfile>(`/api/v1/tenants/${tenantId}/users/${userId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async deactivateUser(tenantId: string, userId: string): Promise<void> {
    await authFetch<{ message: string }>(`/api/v1/tenants/${tenantId}/users/${userId}/deactivate`, {
      method: "PATCH",
    });
  },

  async reactivateUser(tenantId: string, userId: string): Promise<void> {
    await authFetch<{ message: string }>(`/api/v1/tenants/${tenantId}/users/${userId}/reactivate`, {
      method: "PATCH",
    });
  },

  async resendInvitation(tenantId: string, userId: string): Promise<void> {
    await authFetch<{ message: string }>(`/api/v1/tenants/${tenantId}/users/${userId}/resend-invite`, {
      method: "POST",
    });
  },

  async getActionTokenStatus(token: string): Promise<ActionTokenStatus> {
    return authFetch<ActionTokenStatus>(`/api/v1/auth/action-token/${encodeURIComponent(token)}/status`, {
      method: "GET",
    });
  },

  async acceptInvitation(token: string, password: string, confirmPassword: string): Promise<void> {
    await apiFetch(`${AUTH_BASE}/api/v1/auth/invitations/accept`, {
      method: "POST",
      bypassTenantCheck: true,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        token,
        password,
        confirm_password: confirmPassword,
      }),
    }).then(async (response) => {
      const body = await readJson(response);
      if (!response.ok) {
        throw new Error(extractMessage(body) ?? "Failed to accept invitation");
      }
    });
  },

  async requestPasswordReset(email: string): Promise<void> {
    await apiFetch(`${AUTH_BASE}/api/v1/auth/password/forgot`, {
      method: "POST",
      bypassTenantCheck: true,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email }),
    }).then(async (response) => {
      const body = await readJson(response);
      if (!response.ok) {
        throw new Error(extractMessage(body) ?? "Failed to request password reset");
      }
    });
  },

  async resetPassword(token: string, password: string, confirmPassword: string): Promise<void> {
    await apiFetch(`${AUTH_BASE}/api/v1/auth/password/reset`, {
      method: "POST",
      bypassTenantCheck: true,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        token,
        password,
        confirm_password: confirmPassword,
      }),
    }).then(async (response) => {
      const body = await readJson(response);
      if (!response.ok) {
        throw new Error(extractMessage(body) ?? "Failed to reset password");
      }
    });
  },
};

configureApiFetchAuthRecovery({
  refreshAccessToken: () => authApi.refreshAccessToken(),
  clearSession: () => tokenStore.clearAll(),
});
