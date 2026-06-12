import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const energyPagePath = path.resolve(__dirname, "../../app/(protected)/reports/energy/page.tsx");
const presentationPath = path.resolve(__dirname, "../../lib/presentation.ts");

const energyPageSource = readFileSync(energyPagePath, "utf-8");
const presentationSource = readFileSync(presentationPath, "utf-8");

test("energy report page imports formatCo2Kg from presentation", () => {
  assert.equal(energyPageSource.includes("formatCo2Kg"), true);
  assert.equal(presentationSource.includes("export function formatCo2Kg"), true);
});

test("energy report page extends ReportResult with co2_overview", () => {
  assert.equal(energyPageSource.includes("co2_overview"), true);
  assert.equal(energyPageSource.includes("available: boolean"), true);
  assert.equal(energyPageSource.includes("total_co2_kg"), true);
  assert.equal(energyPageSource.includes("off_shift_co2_kg"), true);
});

test("energy report page renders Total CO₂ card when available", () => {
  assert.equal(energyPageSource.includes("Total CO₂"), true);
  assert.equal(energyPageSource.includes("bg-teal-50"), true);
  assert.equal(energyPageSource.includes("text-teal-600"), true);
});

test("energy report page renders Off-Shift CO₂ card", () => {
  assert.equal(energyPageSource.includes("Off-Shift CO₂"), true);
  assert.equal(energyPageSource.includes("bg-cyan-50"), true);
  assert.equal(energyPageSource.includes("text-cyan-600"), true);
});

test("energy report page renders factor footnote with factor metadata", () => {
  assert.equal(energyPageSource.includes("CO₂ estimated using emission factor"), true);
  assert.equal(energyPageSource.includes("factor_source"), true);
  assert.equal(energyPageSource.includes("formatEmissionFactorUnit"), true);
  assert.equal(energyPageSource.includes("formatFactorSource"), true);
});

test("energy report page renders unavailable notice when co2_overview not available", () => {
  assert.equal(energyPageSource.includes("CO₂ emissions estimation is unavailable"), true);
  assert.equal(energyPageSource.includes("emission factor has not been configured"), true);
});

test("energy report page CO2 section is conditional on co2_overview availability", () => {
  assert.equal(energyPageSource.includes("co2_overview?.available"), true);
  assert.equal(energyPageSource.includes("!result.co2_overview.available"), true);
});

test("energy report page does not modify report history preview for CO2", () => {
  const reportsPagePath = path.resolve(__dirname, "../../app/(protected)/reports/page.tsx");
  const reportsPageSource = readFileSync(reportsPagePath, "utf-8");
  assert.equal(reportsPageSource.includes("co2_overview"), false);
});
