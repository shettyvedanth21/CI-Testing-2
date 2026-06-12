export const BUSINESS_TELEMETRY_FIELDS = new Set([
  "active_power",
  "active_power_kw",
  "apparent_power",
  "cos_phi",
  "current",
  "energy",
  "energy_kwh",
  "frequency",
  "kva",
  "kvar",
  "kwh",
  "pf",
  "power",
  "power_factor",
  "power_kw",
  "powerfactor",
  "reactive_power",
  "run_hours",
  "temperature",
  "thd",
  "voltage",
]);

export const DIAGNOSTIC_PHASE_TELEMETRY_FIELDS = new Set([
  "current_l1",
  "current_l2",
  "current_l3",
  "i_l1",
  "i_l2",
  "i_l3",
  "power_l1",
  "power_l2",
  "power_l3",
  "power_factor_l1",
  "power_factor_l2",
  "power_factor_l3",
  "pf_l1",
  "pf_l2",
  "pf_l3",
  "voltage_l1",
  "voltage_l2",
  "voltage_l3",
  "v_l1",
  "v_l2",
  "v_l3",
]);

const NON_TELEMETRY_NUMERIC_FIELDS = new Set([
  "_start",
  "_stop",
  "_time",
  "_value",
  "day",
  "day_of_week",
  "day_of_year",
  "enrichment_status",
  "hour",
  "index",
  "minute",
  "month",
  "quarter",
  "schema_version",
  "second",
  "table",
  "timestamp",
  "unnamed: 0",
  "week",
  "week_of_year",
  "year",
]);

export function normalizeTelemetryFieldName(field: unknown): string {
  return String(field ?? "").trim().toLowerCase();
}

export function isBusinessTelemetryField(field: unknown): boolean {
  return BUSINESS_TELEMETRY_FIELDS.has(normalizeTelemetryFieldName(field));
}

export function isPhaseDiagnosticField(field: unknown): boolean {
  return DIAGNOSTIC_PHASE_TELEMETRY_FIELDS.has(normalizeTelemetryFieldName(field));
}

export function isRuleSelectableMetric(field: unknown): boolean {
  const normalized = normalizeTelemetryFieldName(field);
  return normalized.length > 0 && !NON_TELEMETRY_NUMERIC_FIELDS.has(normalized) && !isPhaseDiagnosticField(normalized);
}

export function isAnalyticsBusinessFeature(field: unknown): boolean {
  const normalized = normalizeTelemetryFieldName(field);
  return normalized.length > 0 && !NON_TELEMETRY_NUMERIC_FIELDS.has(normalized) && !isPhaseDiagnosticField(normalized);
}
