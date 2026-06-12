import test from "node:test";
import assert from "node:assert/strict";

import {
  formatCo2Kg,
  formatEmissionFactorUnit,
  formatFactorSource,
  formatCo2Footnote,
} from "../../lib/presentation.ts";

test("formatCo2Kg returns dash for null", () => {
  assert.equal(formatCo2Kg(null), "—");
});

test("formatCo2Kg returns dash for undefined", () => {
  assert.equal(formatCo2Kg(undefined), "—");
});

test("formatCo2Kg returns dash for NaN", () => {
  assert.equal(formatCo2Kg(NaN), "—");
});

test("formatCo2Kg renders valid zero as 0.00 kg CO₂ not dash", () => {
  assert.equal(formatCo2Kg(0), "0.00 kg CO₂");
});

test("formatCo2Kg renders sub-threshold value with less-than prefix", () => {
  assert.equal(formatCo2Kg(0.005), "< 0.01 kg CO₂");
});

test("formatCo2Kg renders normal value with two decimals", () => {
  assert.equal(formatCo2Kg(71.6), "71.60 kg CO₂");
});

test("formatCo2Kg renders large value correctly", () => {
  assert.equal(formatCo2Kg(1718.4), "1718.40 kg CO₂");
});

test("formatCo2Kg with custom threshold respects the threshold", () => {
  assert.equal(formatCo2Kg(0.5, 1), "< 1.00 kg CO₂");
});

test("formatEmissionFactorUnit maps kg_co2_per_kwh", () => {
  assert.equal(formatEmissionFactorUnit("kg_co2_per_kwh"), "kg CO₂/kWh");
});

test("formatEmissionFactorUnit passes through unknown unit", () => {
  assert.equal(formatEmissionFactorUnit("tonnes_per_mwh"), "tonnes_per_mwh");
});

test("formatEmissionFactorUnit passes through empty string", () => {
  assert.equal(formatEmissionFactorUnit(""), "");
});

test("formatFactorSource maps platform_default", () => {
  assert.equal(formatFactorSource("platform_default"), "Platform Default");
});

test("formatFactorSource maps tenant_default", () => {
  assert.equal(formatFactorSource("tenant_default"), "Organisation Default");
});

test("formatFactorSource omits unconfigured", () => {
  assert.equal(formatFactorSource("unconfigured"), "");
});

test("formatFactorSource omits unknown", () => {
  assert.equal(formatFactorSource("unknown"), "");
});

test("formatFactorSource omits null", () => {
  assert.equal(formatFactorSource(null), "");
});

test("formatFactorSource omits undefined", () => {
  assert.equal(formatFactorSource(undefined), "");
});

test("formatFactorSource omits empty string", () => {
  assert.equal(formatFactorSource(""), "");
});

test("formatCo2Footnote with full metadata", () => {
  assert.equal(
    formatCo2Footnote({
      value: 0.716,
      unit: "kg_co2_per_kwh",
      source: "Central Electricity Authority CO₂ Baseline Database",
      factorSource: "platform_default",
    }),
    "Emission factor: 0.716 kg CO₂/kWh (Central Electricity Authority CO₂ Baseline Database, Platform Default)",
  );
});

test("formatCo2Footnote with no source name but with classification", () => {
  assert.equal(
    formatCo2Footnote({
      value: 0.716,
      unit: "kg_co2_per_kwh",
      source: "",
      factorSource: "platform_default",
    }),
    "Emission factor: 0.716 kg CO₂/kWh (Platform Default)",
  );
});

test("formatCo2Footnote with source name but no displayable classification", () => {
  assert.equal(
    formatCo2Footnote({
      value: 0.716,
      unit: "kg_co2_per_kwh",
      source: "Custom Source",
      factorSource: "unconfigured",
    }),
    "Emission factor: 0.716 kg CO₂/kWh (Custom Source)",
  );
});

test("formatCo2Footnote with no source name and no displayable classification", () => {
  assert.equal(
    formatCo2Footnote({
      value: 0.716,
      unit: "kg_co2_per_kwh",
      source: "",
      factorSource: "unknown",
    }),
    "Emission factor: 0.716 kg CO₂/kWh",
  );
});

test("formatCo2Footnote with tenant_default classification", () => {
  assert.equal(
    formatCo2Footnote({
      value: 0.5,
      unit: "kg_co2_per_kwh",
      source: "Regional Grid",
      factorSource: "tenant_default",
    }),
    "Emission factor: 0.5 kg CO₂/kWh (Regional Grid, Organisation Default)",
  );
});
