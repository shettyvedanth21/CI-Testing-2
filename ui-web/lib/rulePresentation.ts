import type { Rule } from "./ruleApi";

export const RULE_TYPE_OPTIONS = [
  { value: "threshold", label: "Threshold Rule" },
  { value: "time_based", label: "Time-Based Rule" },
  { value: "continuous_idle_duration", label: "Continuous Idle Duration" },
] as const;

export function getRuleTypeLabel(ruleType: Rule["ruleType"]): string {
  switch (ruleType) {
    case "time_based":
      return "Time-Based";
    case "continuous_idle_duration":
      return "Continuous Idle Duration";
    default:
      return "Threshold";
  }
}

export function getRuleTypeBadgeLabel(ruleType: Rule["ruleType"]): string {
  return getRuleTypeLabel(ruleType);
}

export function getRuleTypeHelperText(ruleType: Rule["ruleType"]): string {
  switch (ruleType) {
    case "time_based":
      return "Alert when the machine is running during a restricted wall-clock window.";
    case "continuous_idle_duration":
      return "Alert when the machine stays idle continuously for N minutes.";
    default:
      return "Alert when a telemetry value crosses a threshold.";
  }
}

export function getRuleTriggerSummary(rule: Pick<
  Rule,
  "ruleType" | "property" | "condition" | "threshold" | "timeWindowStart" | "timeWindowEnd" | "durationMinutes"
>): string {
  if (rule.ruleType === "time_based") {
    return `Running in restricted window ${rule.timeWindowStart ?? "--:--"} - ${rule.timeWindowEnd ?? "--:--"} IST`;
  }

  if (rule.ruleType === "continuous_idle_duration") {
    return `Idle continuously for ${rule.durationMinutes ?? "-"} minute${rule.durationMinutes === 1 ? "" : "s"}`;
  }

  return `${rule.property ?? "property"} ${rule.condition ?? ""} ${rule.threshold ?? "-"}`.trim();
}

export function getRuleConditionSummary(rule: Pick<
  Rule,
  "ruleType" | "property" | "condition" | "threshold" | "timeWindowStart" | "timeWindowEnd" | "durationMinutes"
>): string {
  if (rule.ruleType === "time_based") {
    return `${rule.timeWindowStart ?? "--:--"} - ${rule.timeWindowEnd ?? "--:--"} IST`;
  }

  if (rule.ruleType === "continuous_idle_duration") {
    return `${rule.durationMinutes ?? "-"} minute${rule.durationMinutes === 1 ? "" : "s"} continuous idle`;
  }

  return `${rule.condition ?? "="} ${rule.threshold ?? "-"}`;
}

