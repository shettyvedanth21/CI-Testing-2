const DEFAULT_IDLE_THRESHOLD_PCT = 0.25;

export const OVERCONSUMPTION_THRESHOLD_HELP =
  "Overconsumption is derived from full load current (FLA). Inside an active shift, only measured energy above the FLA band is booked to Overconsumption after Off-hours takes precedence.";

export const EXCLUSIVE_LOSS_BUCKET_HELP =
  "Loss buckets are exclusive and still use measured telemetry energy: outside-shift energy is booked to Off-hours, inside-shift low-current energy is booked to Idle, and only energy above FLA inside an active shift is booked to Overconsumption.";

export const IDLE_WIDGET_SCOPE_HELP =
  "Idle Running Waste shows measured idle loss during active shifts only. Idle detection uses the device FLA with a default idle band at 25% of FLA unless you override the idle percentage.";

export const WASTE_ANALYSIS_POLICY_HELP =
  "Waste Analysis uses the same measured-energy accounting policy as device and dashboard loss views. FLA drives classification bands, while energy booking still comes from telemetry intervals.";

export function getOutsideShiftFinancialBucketMessage(loadStateLabel?: string | null): string {
  const normalizedLabel = (loadStateLabel || "").trim().toLowerCase();
  const operationalStateText =
    normalizedLabel && normalizedLabel !== "unknown"
      ? `The machine can still appear ${normalizedLabel} operationally outside a shift`
      : "The machine can still report an operational state outside a shift";
  return `${operationalStateText}, but outside-shift energy is financially booked to Off-hours Loss. Idle and Overconsumption accrue only during active shifts.`;
}

export function parseEngineeringNumberDraft(input: string): number | null {
  const trimmed = input.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function hasUnsavedEngineeringDraft(
  draft: string,
  persisted: number | null | undefined,
): boolean {
  return parseEngineeringNumberDraft(draft) !== (persisted ?? null);
}

export function validateFlaAndIdlePct(
  fullLoadCurrent: number | null,
  idleThresholdPct: number | null,
): string | null {
  if (fullLoadCurrent == null || !Number.isFinite(fullLoadCurrent) || fullLoadCurrent <= 0) {
    return "Full load current must be a positive number.";
  }
  if (idleThresholdPct == null) {
    return null;
  }
  if (!Number.isFinite(idleThresholdPct) || idleThresholdPct <= 0) {
    return "Idle threshold percent must be greater than 0.";
  }
  if (idleThresholdPct >= 1) {
    return "Idle threshold percent must stay below 1.0 so the idle band remains below FLA.";
  }
  return null;
}

export function getEngineeringSaveBlockReason(
  fullLoadCurrent: number | null,
  idleThresholdPct: number | null,
): string | null {
  return validateFlaAndIdlePct(fullLoadCurrent, idleThresholdPct);
}

export function deriveThresholdsFromFla(
  fullLoadCurrent: number | null | undefined,
  idleThresholdPct: number | null | undefined,
): {
  fullLoadCurrent: number | null;
  idleThresholdPct: number | null;
  derivedIdleThreshold: number | null;
  derivedOverconsumptionThreshold: number | null;
} {
  const fla = fullLoadCurrent ?? null;
  const pct = idleThresholdPct ?? DEFAULT_IDLE_THRESHOLD_PCT;
  if (fla == null || !Number.isFinite(fla) || fla <= 0) {
    return {
      fullLoadCurrent: null,
      idleThresholdPct: idleThresholdPct ?? DEFAULT_IDLE_THRESHOLD_PCT,
      derivedIdleThreshold: null,
      derivedOverconsumptionThreshold: null,
    };
  }
  const resolvedPct = pct != null && Number.isFinite(pct) ? pct : DEFAULT_IDLE_THRESHOLD_PCT;
  return {
    fullLoadCurrent: fla,
    idleThresholdPct: resolvedPct,
    derivedIdleThreshold: fla * resolvedPct,
    derivedOverconsumptionThreshold: fla,
  };
}

export function formatIdleThresholdPctLabel(value: number | null | undefined): string {
  const pct = value ?? DEFAULT_IDLE_THRESHOLD_PCT;
  return `${(pct * 100).toFixed(0)}% of FLA`;
}
