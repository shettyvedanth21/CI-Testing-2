import type { MeResponse } from "@/lib/authApi";

export const FEATURE_KEYS = [
  "machines",
  "machine_health",
  "calendar",
  "analytics",
  "reports",
  "waste_analysis",
  "copilot",
  "rules",
  "settings",
] as const;

export type FeatureKey = (typeof FEATURE_KEYS)[number];

export const FEATURE_ROUTE_PREFIX: Record<FeatureKey, string> = {
  machines: "/machines",
  machine_health: "/machines",
  calendar: "/calendar",
  analytics: "/analytics",
  reports: "/reports",
  waste_analysis: "/waste-analysis",
  copilot: "/copilot",
  rules: "/rules",
  settings: "/settings",
};

export const FEATURE_LABELS: Record<FeatureKey, string> = {
  machines: "Machines",
  machine_health: "Machine Health",
  calendar: "Calendar",
  analytics: "Analytics",
  reports: "Reports",
  waste_analysis: "Waste Analysis",
  copilot: "Factory Copilot",
  rules: "Rules",
  settings: "Settings",
};

export const FEATURE_TITLES: Record<FeatureKey, { title: string; subtitle: string }> = {
  machines: { title: "Machines", subtitle: "Fleet health, runtime, and downtime insights" },
  machine_health: { title: "Machine Health", subtitle: "Risk assessment and anomaly detection" },
  calendar: { title: "Calendar", subtitle: "Energy and loss trends by day" },
  analytics: { title: "Analytics", subtitle: "Model-driven diagnosis and predictions" },
  reports: { title: "Reports", subtitle: "Scheduled and on-demand exports" },
  waste_analysis: { title: "Waste Analysis", subtitle: "Actionable waste and loss opportunities" },
  copilot: { title: "Factory Copilot", subtitle: "Conversational operations assistant" },
  rules: { title: "Rules", subtitle: "Alerting logic and response controls" },
  settings: { title: "Settings", subtitle: "Tenant-wide operational configuration" },
};

export function hasFeature(me: MeResponse | null, feature: FeatureKey): boolean {
  return Boolean(me?.entitlements?.available_features.includes(feature));
}

export type MachineHealthDisplayState = "enabled" | "locked" | "unresolved";

export function getMachineHealthDisplayState(me: MeResponse | null): MachineHealthDisplayState {
  if (!me) return "unresolved";
  if (hasFeature(me, "machine_health")) return "enabled";
  return "locked";
}

export function getAvailableFeatures(me: MeResponse | null): FeatureKey[] {
  const raw = me?.entitlements?.available_features ?? [];
  return raw.filter((feature): feature is FeatureKey => FEATURE_KEYS.includes(feature as FeatureKey));
}
