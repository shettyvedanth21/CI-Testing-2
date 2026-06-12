import test from "node:test";
import assert from "node:assert/strict";

import type { HealthConfig, ParameterScore } from "../../lib/deviceApi.ts";
import {
  findHealthConfigForMetric,
  findMatchingHealthConfigsForMetric,
  findParameterScoreForMetric,
  matchesHealthParameterKey,
} from "../../lib/healthScoring.ts";

test("health scoring helpers match exact and alias parameter names", () => {
  assert.equal(matchesHealthParameterKey("temperature", "temperature"), true);
  assert.equal(matchesHealthParameterKey("power_factor", "pf"), true);
  assert.equal(matchesHealthParameterKey("pf", "power_factor"), true);
  assert.equal(matchesHealthParameterKey("vibration", "pressure"), false);
});

test("health scoring helpers resolve backend config and parameter score aliases", () => {
  const configs = [
    { parameter_name: "power_factor" },
    { parameter_name: "temperature" },
  ] as unknown as HealthConfig[];
  const scores = [
    { parameter_name: "power_factor", telemetry_key: "pf", raw_score: 92, weighted_score: 92, weight: 100, status: "Healthy", status_color: "🟢" },
    { parameter_name: "temperature", telemetry_key: "temperature", raw_score: 88, weighted_score: 88, weight: 100, status: "Healthy", status_color: "🟢" },
  ] as unknown as ParameterScore[];

  assert.equal(findHealthConfigForMetric("pf", configs), configs[0]);
  assert.equal(findHealthConfigForMetric("temperature", configs), configs[1]);
  assert.equal(findParameterScoreForMetric("pf", scores), scores[0]);
  assert.equal(findParameterScoreForMetric("power_factor", scores), scores[0]);
});

test("health scoring helpers expose duplicate config matches and prefer the freshest config", () => {
  const older = {
    id: 8,
    parameter_name: "temperature",
    created_at: "2026-04-13T15:31:43Z",
    updated_at: "2026-04-13T15:31:43Z",
  } as unknown as HealthConfig;
  const newer = {
    id: 9,
    parameter_name: "temperature",
    created_at: "2026-04-13T15:31:43Z",
    updated_at: "2026-04-13T15:32:00Z",
  } as unknown as HealthConfig;

  assert.deepEqual(findMatchingHealthConfigsForMetric("temperature", [older, newer]), [older, newer]);
  assert.equal(findHealthConfigForMetric("temperature", [older, newer]), newer);
});
