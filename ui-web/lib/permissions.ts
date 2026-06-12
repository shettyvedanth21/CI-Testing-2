import type { UserRole } from "./authApi";

export interface PermissionFlags {
  canCreateDevice: boolean;
  canEditDevice: boolean;
  canDeleteDevice: boolean;
  canCreateRule: boolean;
  canAcknowledgeAlert: boolean;
  canGenerateReport: boolean;
  canViewAdmin: boolean;
  canManageTeam: boolean;
  isReadOnly: boolean;
}

export function getPermissionsForRole(role: UserRole | null): PermissionFlags {
  const hasRole = (...roles: UserRole[]) => (role ? roles.includes(role) : false);

  return {
    canCreateDevice: hasRole("super_admin", "org_admin", "plant_manager"),
    canEditDevice: hasRole("super_admin", "org_admin", "plant_manager"),
    canDeleteDevice: hasRole("super_admin", "org_admin", "plant_manager"),
    canCreateRule: hasRole("super_admin", "org_admin", "plant_manager", "operator"),
    canAcknowledgeAlert: hasRole("super_admin", "org_admin", "plant_manager", "operator"),
    canGenerateReport: hasRole("super_admin", "org_admin", "plant_manager"),
    canViewAdmin: hasRole("super_admin"),
    canManageTeam: hasRole("super_admin", "org_admin"),
    isReadOnly: hasRole("viewer"),
  };
}
