import type { Rule } from "./ruleApi";

export const MAX_COOLDOWN_SECONDS = 7 * 24 * 60 * 60;

export const COOLDOWN_MINUTE_PRESETS = [
  { value: "5", label: "5 minutes" },
  { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "240", label: "4 hours" },
  { value: "1440", label: "24 hours" },
];

function formatMinuteDuration(totalMinutes: number): string {
  const mins = Math.max(0, Math.floor(totalMinutes));
  if (mins === 0) return "0 minutes";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"}`;

  const hours = Math.floor(mins / 60);
  const remaining = mins % 60;

  if (remaining === 0) {
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }

  return `${hours} hour${hours === 1 ? "" : "s"} ${remaining} minute${remaining === 1 ? "" : "s"}`;
}

export function formatCooldownLabel(rule: Pick<
  Rule,
  "cooldownMode" | "cooldownUnit" | "cooldownMinutes" | "cooldownSeconds"
>): string {
  if (rule.cooldownMode === "no_repeat") return "No repeat";

  if (rule.cooldownUnit === "seconds") {
    const seconds = rule.cooldownSeconds ?? 0;
    return `${seconds} second${seconds === 1 ? "" : "s"}`;
  }

  const minutes = rule.cooldownMinutes ?? (rule.cooldownSeconds != null ? Math.max(1, Math.ceil(rule.cooldownSeconds / 60)) : 15);
  return formatMinuteDuration(minutes);
}
