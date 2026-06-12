export const PREMIUM_MODULE_GRANT_KEYS = [
  "analytics",
  "reports",
  "waste_analysis",
  "copilot",
  "machine_health",
] as const;

export const NOTIFICATION_ALERT_GRANT_KEYS = [
  "notification_sms",
  "notification_whatsapp",
] as const;

export const PREMIUM_ORG_GRANT_KEYS = [
  ...PREMIUM_MODULE_GRANT_KEYS,
  ...NOTIFICATION_ALERT_GRANT_KEYS,
] as const;

export type PremiumModuleGrantKey = (typeof PREMIUM_MODULE_GRANT_KEYS)[number];
export type NotificationAlertGrantKey = (typeof NOTIFICATION_ALERT_GRANT_KEYS)[number];
export type PremiumOrgGrantKey = (typeof PREMIUM_ORG_GRANT_KEYS)[number];

export const PLANT_MANAGER_DELEGATABLE_KEYS: PremiumModuleGrantKey[] = ["analytics", "reports", "waste_analysis"];

export function isPremiumModuleGrantKey(value: string): value is PremiumModuleGrantKey {
  return PREMIUM_MODULE_GRANT_KEYS.includes(value as PremiumModuleGrantKey);
}

export function isPlantManagerDelegatable(value: string): boolean {
  return PLANT_MANAGER_DELEGATABLE_KEYS.includes(value as PremiumModuleGrantKey);
}

export function getOrgPremiumModuleLabels(): { key: PremiumModuleGrantKey; label: string; delegatable: boolean }[] {
  return PREMIUM_MODULE_GRANT_KEYS.map((key) => ({
    key,
    label: key === "machine_health" ? "Machine Health" : key.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
    delegatable: isPlantManagerDelegatable(key),
  }));
}

export function isPremiumOrgGrantKey(value: string): value is PremiumOrgGrantKey {
  return PREMIUM_ORG_GRANT_KEYS.includes(value as PremiumOrgGrantKey);
}

export function toPremiumOrgGrantSet(values: Iterable<string> | null | undefined): Set<PremiumOrgGrantKey> {
  const next = new Set<PremiumOrgGrantKey>();
  for (const value of values ?? []) {
    if (isPremiumOrgGrantKey(value)) {
      next.add(value);
    }
  }
  return next;
}
