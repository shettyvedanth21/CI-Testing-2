import { formatCurrencyCodeValue } from "./presentation.ts";

export interface HiddenOverconsumptionSummary {
  selected_days?: number | null;
  total_actual_energy_kwh?: number | null;
  aggregate_p75_baseline_reference?: number | null;
  total_baseline_energy_kwh?: number | null;
  total_hidden_overconsumption_kwh?: number | null;
  total_hidden_overconsumption_cost?: number | null;
  tariff_rate_used?: number | null;
}

export interface HiddenOverconsumptionDailyRow {
  date: string;
  actual_energy_kwh?: number | null;
  p75_power_baseline_w?: number | null;
  baseline_energy_kwh?: number | null;
  hidden_overconsumption_kwh?: number | null;
  hidden_overconsumption_cost?: number | null;
  sample_count?: number | null;
  covered_duration_hours?: number | null;
  tariff_rate_used?: number | null;
}

export interface HiddenOverconsumptionDeviceRow {
  date: string;
  device_id?: string | null;
  device_name?: string | null;
  actual_energy_kwh?: number | null;
  p75_power_baseline_w?: number | null;
  baseline_energy_kwh?: number | null;
  difference_vs_baseline_kwh?: number | null;
  status?: HiddenBaselineStatus | string | null;
  hidden_overconsumption_kwh?: number | null;
  hidden_overconsumption_cost?: number | null;
  sample_count?: number | null;
  covered_duration_hours?: number | null;
  tariff_rate_used?: number | null;
}

export interface HiddenOverconsumptionInsight {
  summary?: HiddenOverconsumptionSummary | null;
  daily_breakdown?: HiddenOverconsumptionDailyRow[] | null;
  device_breakdown?: HiddenOverconsumptionDeviceRow[] | null;
  aggregation_rule?: Record<string, string> | null;
  insight_text?: string | null;
}

export type HiddenBaselineStatus = "Above Baseline" | "Within Baseline" | "Below Baseline" | "Unavailable";

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatHiddenNumber(value: number | null | undefined, decimals = 2): string {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return value.toFixed(decimals);
}

export function formatHiddenCost(
  value: number | null | undefined,
  currency: string,
): string {
  if (!isFiniteNumber(value)) {
    return "N/A";
  }
  return formatCurrencyCodeValue(value, currency);
}

export function getUsableHiddenInsightRows(
  rows: HiddenOverconsumptionDailyRow[] | null | undefined,
): HiddenOverconsumptionDailyRow[] {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.filter(
    (row) =>
      isFiniteNumber(row.p75_power_baseline_w) &&
      isFiniteNumber(row.baseline_energy_kwh),
  );
}

export function getHiddenInsightDailyBreakdown(
  insight: HiddenOverconsumptionInsight | null | undefined,
): HiddenOverconsumptionDailyRow[] | null | undefined {
  return insight?.daily_breakdown;
}

export function getDifferenceVsBaselineKwh(row: HiddenOverconsumptionDailyRow): number | null {
  if (!isFiniteNumber(row.actual_energy_kwh) || !isFiniteNumber(row.baseline_energy_kwh)) {
    return null;
  }
  return row.actual_energy_kwh - row.baseline_energy_kwh;
}

export function getUsableHiddenDeviceRows(
  rows: HiddenOverconsumptionDeviceRow[] | null | undefined,
): HiddenOverconsumptionDeviceRow[] {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.filter(
    (row) =>
      isFiniteNumber(row.p75_power_baseline_w) &&
      isFiniteNumber(row.baseline_energy_kwh),
  );
}

export function getHiddenDeviceDisplayName(row: HiddenOverconsumptionDeviceRow): string {
  const trimmedName = typeof row.device_name === "string" ? row.device_name.trim() : "";
  if (trimmedName) {
    return trimmedName;
  }
  const trimmedId = typeof row.device_id === "string" ? row.device_id.trim() : "";
  return trimmedId || "—";
}

export function getHiddenBaselineStatus(row: HiddenOverconsumptionDailyRow): HiddenBaselineStatus {
  const diff = getDifferenceVsBaselineKwh(row);
  if (!isFiniteNumber(diff)) {
    return "Unavailable";
  }
  if (diff > 0) {
    return "Above Baseline";
  }
  if (diff < 0) {
    return "Below Baseline";
  }
  return "Within Baseline";
}

export function formatSignedKwh(value: number | null | undefined, decimals = 2): string {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  const abs = Math.abs(value).toFixed(decimals);
  if (value > 0) {
    return `+${abs}`;
  }
  if (value < 0) {
    return `-${abs}`;
  }
  return `0.${"0".repeat(decimals)}`;
}
