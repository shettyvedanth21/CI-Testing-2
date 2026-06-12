import * as SecureStore from "expo-secure-store";

import { API_CONFIG } from "../constants/api";

const KEYS = {
  ACCESS_TOKEN: "shivex_access_token",
  REFRESH_TOKEN: "shivex_refresh_token",
  USER_PROFILE: "shivex_user_profile",
} as const;

export type UserRole = "super_admin" | "org_admin" | "plant_manager" | "operator" | "viewer";

export interface UserProfile {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  tenant_id: string | null;
  is_active: boolean;
}

export interface MeResponse {
  user: UserProfile;
  org: { id: string; name: string; slug: string } | null;
  plant_ids: string[];
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

function getMessageFromBody(body: unknown, fallback: string): string {
  if (typeof body === "string" && body.trim()) {
    return body;
  }

  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    const detail = record.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (detail && typeof detail === "object") {
      const detailMessage = (detail as Record<string, unknown>).message;
      if (typeof detailMessage === "string" && detailMessage.trim()) {
        return detailMessage;
      }
    }
    const message = record.message;
    if (typeof message === "string" && message.trim()) {
      return message;
    }
  }

  return fallback;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      return await response.json();
    }
    return await response.text();
  } catch {
    return null;
  }
}

async function expectJson<T>(response: Response, fallback: string): Promise<T> {
  const body = await parseResponseBody(response);
  if (!response.ok) {
    throw new Error(getMessageFromBody(body, fallback));
  }
  return body as T;
}

async function authedRequest(path: string, options: RequestInit = {}, retry = false): Promise<Response> {
  const headers = new Headers(options.headers);
  const accessToken = await secureTokenStore.getAccessToken();
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${API_CONFIG.AUTH_SERVICE}${path}`, {
    ...options,
    headers,
  });

  if (response.status !== 401) {
    return response;
  }

  if (retry) {
    await secureTokenStore.clearAll();
    throw new Error("SESSION_EXPIRED");
  }

  const refreshed = await mobileAuthApi.refresh();
  if (!refreshed) {
    await secureTokenStore.clearAll();
    throw new Error("SESSION_EXPIRED");
  }

  return authedRequest(path, options, true);
}

export const secureTokenStore = {
  async getAccessToken(): Promise<string | null> {
    return SecureStore.getItemAsync(KEYS.ACCESS_TOKEN);
  },

  async setAccessToken(token: string): Promise<void> {
    await SecureStore.setItemAsync(KEYS.ACCESS_TOKEN, token);
  },

  async getRefreshToken(): Promise<string | null> {
    return SecureStore.getItemAsync(KEYS.REFRESH_TOKEN);
  },

  async setRefreshToken(token: string): Promise<void> {
    await SecureStore.setItemAsync(KEYS.REFRESH_TOKEN, token);
  },

  async getUser(): Promise<MeResponse | null> {
    const stored = await SecureStore.getItemAsync(KEYS.USER_PROFILE);
    if (!stored) {
      return null;
    }

    try {
      return JSON.parse(stored) as MeResponse;
    } catch {
      return null;
    }
  },

  async setUser(me: MeResponse): Promise<void> {
    await SecureStore.setItemAsync(KEYS.USER_PROFILE, JSON.stringify(me));
  },

  async clearAll(): Promise<void> {
    await Promise.allSettled([
      SecureStore.deleteItemAsync(KEYS.ACCESS_TOKEN),
      SecureStore.deleteItemAsync(KEYS.REFRESH_TOKEN),
      SecureStore.deleteItemAsync(KEYS.USER_PROFILE),
    ]);
  },
};

export const mobileAuthApi = {
  async login(email: string, password: string): Promise<MeResponse> {
    const response = await fetch(`${API_CONFIG.AUTH_SERVICE}/api/v1/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, password }),
    });

    const tokens = await expectJson<TokenResponse>(
      response,
      "Unable to sign in. Please check your credentials.",
    );

    await Promise.all([
      secureTokenStore.setAccessToken(tokens.access_token),
      secureTokenStore.setRefreshToken(tokens.refresh_token),
    ]);

    const me = await this.getMe();
    if (!me) {
      throw new Error("Unable to load your profile.");
    }

    await secureTokenStore.setUser(me);
    return me;
  },

  async logout(): Promise<void> {
    const refreshToken = await secureTokenStore.getRefreshToken();

    try {
      await fetch(`${API_CONFIG.AUTH_SERVICE}/api/v1/auth/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refresh_token: refreshToken ?? "" }),
      });
    } finally {
      await secureTokenStore.clearAll();
    }
  },

  async refresh(): Promise<string | null> {
    const refreshToken = await secureTokenStore.getRefreshToken();
    if (!refreshToken) {
      return null;
    }

    try {
      const response = await fetch(`${API_CONFIG.AUTH_SERVICE}/api/v1/auth/refresh`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (response.status === 401) {
        return null;
      }

      const tokens = await expectJson<TokenResponse>(response, "Unable to refresh your session.");
      await Promise.all([
        secureTokenStore.setAccessToken(tokens.access_token),
        secureTokenStore.setRefreshToken(tokens.refresh_token),
      ]);
      return tokens.access_token;
    } catch {
      return null;
    }
  },

  async getMe(): Promise<MeResponse | null> {
    try {
      const response = await authedRequest("/api/v1/auth/me", {
        method: "GET",
      });

      if (response.status === 401) {
        return null;
      }

      const me = await expectJson<MeResponse>(response, "Unable to load your profile.");
      await secureTokenStore.setUser(me);
      return me;
    } catch (error) {
      if (error instanceof Error && error.message === "SESSION_EXPIRED") {
        return null;
      }
      throw error;
    }
  },

  async hydrateSession(): Promise<MeResponse | null> {
    const directMe = await this.getMe();
    if (directMe) {
      return directMe;
    }

    const refreshed = await this.refresh();
    if (!refreshed) {
      await secureTokenStore.clearAll();
      return null;
    }

    const hydratedMe = await this.getMe();
    if (!hydratedMe) {
      await secureTokenStore.clearAll();
      return null;
    }

    return hydratedMe;
  },
};

/**
 * Authenticated fetch for all mobile API calls.
 * Replaces bare fetch() in all src/api/*.ts files.
 * Auto-attaches access token. Retries once on 401 after refresh.
 * Throws Error("SESSION_EXPIRED") if refresh fails.
 */
export async function mobileFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const execute = async (retry = false): Promise<Response> => {
    const headers = new Headers(options.headers);
    const accessToken = await secureTokenStore.getAccessToken();
    if (accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (response.status !== 401) {
      return response;
    }

    if (retry) {
      await secureTokenStore.clearAll();
      throw new Error("SESSION_EXPIRED");
    }

    const refreshed = await mobileAuthApi.refresh();
    if (!refreshed) {
      await secureTokenStore.clearAll();
      throw new Error("SESSION_EXPIRED");
    }

    return execute(true);
  };

  return execute(false);
}
