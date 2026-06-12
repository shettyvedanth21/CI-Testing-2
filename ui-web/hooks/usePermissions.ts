"use client";

import { useAuth } from "@/lib/authContext";
import { getPermissionsForRole } from "@/lib/permissions";

export function usePermissions() {
  const { me } = useAuth();
  const role = me?.user.role ?? null;

  return {
    ...getPermissionsForRole(role),
    currentRole: role,
  };
}
