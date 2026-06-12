import * as SecureStore from "expo-secure-store";
import { create } from "zustand";

import { mobileAuthApi, type MeResponse } from "../api/authApi";

const USER_NAME_KEY = "shivex_user_name";
const USER_ROLE_KEY = "shivex_user_role";

interface UserStore {
  userName: string | null;
  userRole: string | null;
  setUserName: (name: string) => void;
  setUserRole: (role: string) => void;

  me: MeResponse | null;
  isAuthenticated: boolean;
  isHydrating: boolean;

  setMe: (me: MeResponse) => void;
  clearAuth: () => void;
  setHydrating: (v: boolean) => void;

  hydrateFromStorage: () => Promise<void>;

  setUser: (name: string, role: string) => Promise<void>;
  loadUser: () => Promise<void>;
  clearUser: () => Promise<void>;
}

export const useUserStore = create<UserStore>((set) => ({
  userName: null,
  userRole: null,
  me: null,
  isAuthenticated: false,
  isHydrating: false,

  setUserName: (name) => {
    const nextName = name.trim();
    set({ userName: nextName || null });
    void SecureStore.setItemAsync(USER_NAME_KEY, nextName);
  },

  setUserRole: (role) => {
    const nextRole = role.trim();
    set({ userRole: nextRole || null });
    void SecureStore.setItemAsync(USER_ROLE_KEY, nextRole);
  },

  setMe: (me) => {
    const displayName = me.user.full_name ?? me.user.email;
    set({
      me,
      isAuthenticated: true,
      userName: displayName,
      userRole: me.user.role,
    });
    void Promise.all([
      SecureStore.setItemAsync(USER_NAME_KEY, displayName),
      SecureStore.setItemAsync(USER_ROLE_KEY, me.user.role),
    ]);
  },

  clearAuth: () => {
    set({
      me: null,
      isAuthenticated: false,
      userName: null,
      userRole: null,
    });
    void Promise.allSettled([
      SecureStore.deleteItemAsync(USER_NAME_KEY),
      SecureStore.deleteItemAsync(USER_ROLE_KEY),
      mobileAuthApi.logout(),
    ]);
  },

  setHydrating: (v) => {
    set({ isHydrating: v });
  },

  hydrateFromStorage: async () => {
    set({ isHydrating: true });
    try {
      const result = await mobileAuthApi.hydrateSession();
      if (result) {
        const displayName = result.user.full_name ?? result.user.email;
        set({
          me: result,
          isAuthenticated: true,
          userName: displayName,
          userRole: result.user.role,
        });
        await Promise.all([
          SecureStore.setItemAsync(USER_NAME_KEY, displayName),
          SecureStore.setItemAsync(USER_ROLE_KEY, result.user.role),
        ]);
      } else {
        set({
          me: null,
          isAuthenticated: false,
          userName: null,
          userRole: null,
        });
        await Promise.allSettled([
          SecureStore.deleteItemAsync(USER_NAME_KEY),
          SecureStore.deleteItemAsync(USER_ROLE_KEY),
        ]);
      }
    } finally {
      set({ isHydrating: false });
    }
  },

  setUser: async (name, role) => {
    const nextName = name.trim();
    const nextRole = role.trim();
    set({
      userName: nextName || null,
      userRole: nextRole || null,
    });
    await Promise.all([
      SecureStore.setItemAsync(USER_NAME_KEY, nextName),
      SecureStore.setItemAsync(USER_ROLE_KEY, nextRole),
    ]);
  },

  loadUser: async () => {
    const [storedName, storedRole] = await Promise.all([
      SecureStore.getItemAsync(USER_NAME_KEY),
      SecureStore.getItemAsync(USER_ROLE_KEY),
    ]);
    set({
      userName: storedName ?? null,
      userRole: storedRole ?? null,
    });
  },

  clearUser: async () => {
    set({
      userName: null,
      userRole: null,
      me: null,
      isAuthenticated: false,
    });
    await Promise.allSettled([
      SecureStore.deleteItemAsync(USER_NAME_KEY),
      SecureStore.deleteItemAsync(USER_ROLE_KEY),
    ]);
  },
}));
