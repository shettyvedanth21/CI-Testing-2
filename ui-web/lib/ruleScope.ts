import type { RuleScope } from "./ruleApi";

export type RuleScopedRole = "super_admin" | "org_admin" | "plant_manager" | "operator" | "viewer" | string;

export function isPlantScopedRuleRole(role: RuleScopedRole | null | undefined): boolean {
  return role === "plant_manager" || role === "operator" || role === "viewer";
}

export function getRuleScopeOptions(role: RuleScopedRole | null | undefined): Array<{ value: RuleScope; label: string }> {
  if (isPlantScopedRuleRole(role)) {
    return [
      { value: "all_devices", label: "All Accessible Devices" },
      { value: "selected_devices", label: "Selected Devices" },
    ];
  }
  return [
    { value: "all_devices", label: "All Devices" },
    { value: "selected_devices", label: "Selected Devices" },
  ];
}

export function getAllDevicesScopeLabel(role: RuleScopedRole | null | undefined): string {
  return isPlantScopedRuleRole(role) ? "All Accessible Devices" : "All Devices";
}

export function getRulesPageSubtitle(role: RuleScopedRole | null | undefined): string {
  return isPlantScopedRuleRole(role)
    ? "Manage monitoring rules across your accessible machines"
    : "Manage monitoring rules across all machines";
}

export function getRulesScopeHint(role: RuleScopedRole | null | undefined): string | null {
  return isPlantScopedRuleRole(role)
    ? 'For your role, "All Accessible Devices" means only devices from your assigned plants.'
    : null;
}

export function getRuleDeviceScopeDisplay(
  deviceIds: string[],
  role: RuleScopedRole | null | undefined,
  resolveDeviceName: (deviceId: string) => string,
): string {
  if (deviceIds.length === 0) {
    return getAllDevicesScopeLabel(role);
  }
  return deviceIds.map(resolveDeviceName).join(", ");
}
