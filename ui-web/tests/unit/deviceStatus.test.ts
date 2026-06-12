import test from "node:test";
import assert from "node:assert/strict";

import {
  getOperationalStatusMeta,
  mergeCurrentStateWithStability,
  preserveKnownStatusAgainstTransientUnknown,
  resolveOperationalStatus,
} from "../../lib/deviceStatus.ts";

test("resolveOperationalStatus keeps overconsumption distinct from running", () => {
  assert.equal(
    resolveOperationalStatus({
      runtimeStatus: "running",
      loadState: "overconsumption",
      currentBand: "overconsumption",
      hasTelemetry: true,
    }),
    "overconsumption",
  );
});

test("resolveOperationalStatus preserves stopped and unknown separately", () => {
  assert.equal(
    resolveOperationalStatus({
      runtimeStatus: "stopped",
      loadState: "unknown",
      hasTelemetry: true,
    }),
    "stopped",
  );
  assert.equal(
    resolveOperationalStatus({
      runtimeStatus: "stopped",
      loadState: "unknown",
      hasTelemetry: false,
    }),
    "unknown",
  );
});

test("getOperationalStatusMeta returns distinct overconsumption presentation", () => {
  const meta = getOperationalStatusMeta("overconsumption");
  assert.equal(meta.label, "Overconsumption");
  assert.match(meta.className, /fuchsia/);
});

test("resolveOperationalStatus keeps unloaded as unknown in dashboard UX", () => {
  assert.equal(
    resolveOperationalStatus({
      runtimeStatus: "running",
      loadState: "unloaded",
      currentBand: "unloaded",
      hasTelemetry: true,
    }),
    "unknown",
  );
});

test("preserveKnownStatusAgainstTransientUnknown blocks weak running->unknown downgrade", () => {
  const merged = preserveKnownStatusAgainstTransientUnknown({
    currentOperationalStatus: "overconsumption",
    currentLoadState: "overconsumption",
    incomingOperationalStatus: "unknown",
    incomingLoadState: "unknown",
    incomingRuntimeStatus: "running",
    incomingLastSeenTimestamp: "2026-04-18T10:00:00Z",
    source: "stream_partial",
    nowMs: Date.parse("2026-04-18T10:00:20Z"),
  });
  assert.equal(merged.operationalStatus, "overconsumption");
  assert.equal(merged.loadState, "overconsumption");
});

test("preserveKnownStatusAgainstTransientUnknown allows unknown after genuine staleness", () => {
  const merged = preserveKnownStatusAgainstTransientUnknown({
    currentOperationalStatus: "running",
    currentLoadState: "running",
    incomingOperationalStatus: "unknown",
    incomingLoadState: "unknown",
    incomingRuntimeStatus: "running",
    incomingLastSeenTimestamp: "2026-04-18T10:00:00Z",
    source: "stream_partial",
    nowMs: Date.parse("2026-04-18T10:02:30Z"),
  });
  assert.equal(merged.operationalStatus, "unknown");
  assert.equal(merged.loadState, "unknown");
});

test("mergeCurrentStateWithStability keeps known load state during fresh transient unknown", () => {
  const merged = mergeCurrentStateWithStability(
    {
      state: "idle",
      current_band: "idle",
      timestamp: "2026-04-18T10:00:00Z",
    },
    {
      state: "unknown",
      current_band: "unknown",
      timestamp: "2026-04-18T10:00:20Z",
    },
    {
      runtimeStatus: "running",
      source: "current_state_poll",
      nowMs: Date.parse("2026-04-18T10:00:30Z"),
    },
  );
  assert.equal(merged?.state, "idle");
  assert.equal(merged?.current_band, "idle");
});

test("mergeCurrentStateWithStability keeps honest unknown when stale", () => {
  const merged = mergeCurrentStateWithStability(
    {
      state: "running",
      current_band: "in_load",
      timestamp: "2026-04-18T10:00:00Z",
    },
    {
      state: "unknown",
      current_band: "unknown",
      timestamp: "2026-04-18T10:00:00Z",
    },
    {
      runtimeStatus: "running",
      source: "current_state_poll",
      nowMs: Date.parse("2026-04-18T10:03:00Z"),
    },
  );
  assert.equal(merged?.state, "unknown");
  assert.equal(merged?.current_band, "unknown");
});
