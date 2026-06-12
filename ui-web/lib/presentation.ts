export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export function getStatusTone(status: string | null | undefined): Tone {
  const value = (status || "").toLowerCase();
  if (["active", "online", "running", "healthy", "up", "classified"].includes(value)) return "success";
  if (["warning", "degraded", "idle", "maintenance", "pending"].includes(value)) return "warning";
  if (["inactive", "offline", "stopped", "down", "error", "failed"].includes(value)) return "danger";
  if (["paused", "open", "unclassified", "info"].includes(value)) return "info";
  return "neutral";
}

export function formatCompactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatCurrencyINR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(value);
}

function isVisibleSmallNonZero(value: number, threshold: number): boolean {
  return Number.isFinite(value) && value > 0 && value < threshold;
}

export function formatEnergyKwh(value: number | null | undefined, threshold = 0.01): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (isVisibleSmallNonZero(value, threshold)) {
    return `< ${threshold.toFixed(2)} kWh`;
  }
  return `${value.toFixed(2)} kWh`;
}

export function formatCo2Kg(value: number | null | undefined, threshold = 0.01): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (isVisibleSmallNonZero(value, threshold)) {
    return `< ${threshold.toFixed(2)} kg CO₂`;
  }
  return `${value.toFixed(2)} kg CO₂`;
}

const _FACTOR_UNIT_DISPLAY: Record<string, string> = {
  kg_co2_per_kwh: "kg CO₂/kWh",
};

export function formatEmissionFactorUnit(unit: string): string {
  return _FACTOR_UNIT_DISPLAY[unit] ?? unit;
}

const _FACTOR_SOURCE_DISPLAY: Record<string, string> = {
  platform_default: "Platform Default",
  tenant_default: "Organisation Default",
};

export function formatFactorSource(source: string | null | undefined): string {
  if (!source) return "";
  const mapped = _FACTOR_SOURCE_DISPLAY[source];
  if (mapped) return mapped;
  if (source === "unconfigured" || source === "unknown") return "";
  return "";
}

export function formatCo2Footnote(params: {
  value: number;
  unit: string;
  source?: string | null;
  factorSource?: string | null;
}): string {
  const displayUnit = formatEmissionFactorUnit(params.unit);
  const displaySource = formatFactorSource(params.factorSource);
  const sourceName = params.source?.trim() || "";

  const parts: string[] = [];
  if (sourceName) {
    parts.push(sourceName);
  }
  if (displaySource) {
    parts.push(displaySource);
  }
  const attribution = parts.length > 0 ? ` (${parts.join(", ")})` : "";

  return `Emission factor: ${params.value} ${displayUnit}${attribution}`;
}

export function formatCurrencyValue(
  value: number | null | undefined,
  currency = "INR",
  threshold = 0.01,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const formatter = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (isVisibleSmallNonZero(value, threshold)) {
    return `< ${formatter.format(threshold)}`;
  }
  return formatter.format(value);
}

export function formatCurrencyCodeValue(
  value: number | null | undefined,
  currency = "INR",
  threshold = 0.01,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (isVisibleSmallNonZero(value, threshold)) {
    return `< ${currency} ${threshold.toFixed(2)}`;
  }
  return `${currency} ${value.toFixed(2)}`;
}
