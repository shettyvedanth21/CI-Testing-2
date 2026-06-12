import type { HealthConfig, ParameterScore } from "@/lib/deviceApi";

const CANONICAL_PARAMETER_ALIASES: Record<string, string[]> = {
  current: ["current_a", "phase_current"],
  power: ["active_power", "active_power_kw", "business_power_w", "power_kw", "kw"],
  power_factor: ["pf", "cos_phi", "powerfactor", "pf_business", "raw_power_factor"],
  voltage: ["voltage_v"],
};

const ALIASES_TO_CANONICAL = new Map<string, string>(
  Object.entries(CANONICAL_PARAMETER_ALIASES).flatMap(([canonical, aliases]) =>
    aliases.map((alias) => [normalizeHealthParameterKey(alias), canonical] as const),
  ),
);

export function normalizeHealthParameterKey(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

export function canonicalizeHealthParameterKey(value: unknown): string {
  const normalized = normalizeHealthParameterKey(value);
  return ALIASES_TO_CANONICAL.get(normalized) ?? normalized;
}

export function matchesHealthParameterKey(left: unknown, right: unknown): boolean {
  return canonicalizeHealthParameterKey(left) === canonicalizeHealthParameterKey(right);
}

export function findMatchingHealthConfigsForMetric(metric: string, configs: HealthConfig[]): HealthConfig[] {
  return configs.filter((config) => matchesHealthParameterKey(config.parameter_name, metric));
}

function sortHealthConfigsByFreshness(configs: HealthConfig[]): HealthConfig[] {
  return [...configs].sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.created_at || "");
    const rightTime = Date.parse(right.updated_at || right.created_at || "");
    if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return right.id - left.id;
  });
}

export function findHealthConfigForMetric(metric: string, configs: HealthConfig[]): HealthConfig | null {
  const matches = findMatchingHealthConfigsForMetric(metric, configs);
  return sortHealthConfigsByFreshness(matches)[0] ?? null;
}

export function findParameterScoreForMetric(metric: string, scores: ParameterScore[]): ParameterScore | null {
  return (
    scores.find(
      (score) =>
        matchesHealthParameterKey(score.parameter_name, metric) ||
        (score.telemetry_key ? matchesHealthParameterKey(score.telemetry_key, metric) : false),
    ) ?? null
  );
}
