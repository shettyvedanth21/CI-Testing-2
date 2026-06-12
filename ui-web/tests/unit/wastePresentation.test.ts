import test from "node:test";
import assert from "node:assert/strict";

import { formatCurrencyCodeValue, formatCurrencyValue, formatEnergyKwh } from "../../lib/presentation.ts";
import {
  deriveThresholdsFromFla,
  EXCLUSIVE_LOSS_BUCKET_HELP,
  formatIdleThresholdPctLabel,
  getEngineeringSaveBlockReason,
  hasUnsavedEngineeringDraft,
  IDLE_WIDGET_SCOPE_HELP,
  OVERCONSUMPTION_THRESHOLD_HELP,
  getOutsideShiftFinancialBucketMessage,
  parseEngineeringNumberDraft,
  validateFlaAndIdlePct,
  WASTE_ANALYSIS_POLICY_HELP,
} from "../../lib/wasteSemantics.ts";

test("formatEnergyKwh keeps small non-zero values visible", () => {
  assert.equal(formatEnergyKwh(0), "0.00 kWh");
  assert.equal(formatEnergyKwh(0.0042), "< 0.01 kWh");
  assert.equal(formatEnergyKwh(0.125), "0.13 kWh");
});

test("formatCurrencyValue keeps small non-zero values visible", () => {
  assert.equal(formatCurrencyValue(0, "INR"), "₹0.00");
  assert.equal(formatCurrencyValue(0.0042, "INR"), "< ₹0.01");
  assert.equal(formatCurrencyValue(12.5, "INR"), "₹12.50");
});

test("formatCurrencyCodeValue preserves cents for report summary cards", () => {
  assert.equal(formatCurrencyCodeValue(3.55, "INR"), "INR 3.55");
  assert.equal(formatCurrencyCodeValue(0.0042, "INR"), "< INR 0.01");
  assert.equal(formatCurrencyCodeValue(null, "INR"), "—");
});

test("overconsumption help text matches exclusive accounting policy", () => {
  assert.match(OVERCONSUMPTION_THRESHOLD_HELP, /derived from full load current/i);
  assert.match(OVERCONSUMPTION_THRESHOLD_HELP, /measured energy above the FLA band/i);
});

test("shared loss copy explains exclusive buckets and outside-shift booking", () => {
  assert.match(EXCLUSIVE_LOSS_BUCKET_HELP, /measured telemetry energy/i);
  assert.match(IDLE_WIDGET_SCOPE_HELP, /default idle band at 25% of FLA/i);
  assert.match(WASTE_ANALYSIS_POLICY_HELP, /FLA drives classification bands/i);
});

test("outside-shift financial bucket message separates operational state from financial loss bucket", () => {
  assert.match(
    getOutsideShiftFinancialBucketMessage("Idle"),
    /appear idle operationally outside a shift/i,
  );
  assert.match(
    getOutsideShiftFinancialBucketMessage("Idle"),
    /financially booked to Off-hours Loss/i,
  );
  assert.match(
    getOutsideShiftFinancialBucketMessage("In Load"),
    /idle and overconsumption accrue only during active shifts/i,
  );
});

test("engineering draft parser accepts numeric input and blanks", () => {
  assert.equal(parseEngineeringNumberDraft(""), null);
  assert.equal(parseEngineeringNumberDraft(" 0.25 "), 0.25);
  assert.equal(parseEngineeringNumberDraft("abc"), null);
});

test("FLA and idle percent validation enforces positive FLA and sub-100 idle percent", () => {
  assert.equal(
    validateFlaAndIdlePct(null, 0.25),
    "Full load current must be a positive number.",
  );
  assert.equal(
    validateFlaAndIdlePct(20, 1),
    "Idle threshold percent must stay below 1.0 so the idle band remains below FLA.",
  );
  assert.equal(validateFlaAndIdlePct(20, 0.25), null);
});

test("save block reason reuses the shared FLA validation", () => {
  assert.equal(
    getEngineeringSaveBlockReason(20, 0.25),
    null,
  );
  assert.match(getEngineeringSaveBlockReason(0, 0.25) || "", /full load current/i);
});

test("unsaved engineering draft detection compares parsed numeric values", () => {
  assert.equal(hasUnsavedEngineeringDraft("0.25", 0.25), false);
  assert.equal(hasUnsavedEngineeringDraft("0.30", 0.25), true);
});

test("derived thresholds use FLA and the default 25 percent idle band", () => {
  assert.deepEqual(deriveThresholdsFromFla(20, null), {
    fullLoadCurrent: 20,
    idleThresholdPct: 0.25,
    derivedIdleThreshold: 5,
    derivedOverconsumptionThreshold: 20,
  });
  assert.deepEqual(deriveThresholdsFromFla(null, 0.4), {
    fullLoadCurrent: null,
    idleThresholdPct: 0.4,
    derivedIdleThreshold: null,
    derivedOverconsumptionThreshold: null,
  });
});

test("idle threshold percent label renders as a readable percentage", () => {
  assert.equal(formatIdleThresholdPctLabel(0.25), "25% of FLA");
  assert.equal(formatIdleThresholdPctLabel(null), "25% of FLA");
});
