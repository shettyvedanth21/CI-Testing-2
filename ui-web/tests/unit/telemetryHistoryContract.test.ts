import test from "node:test";
import assert from "node:assert/strict";

import {
  TelemetryHistoryUnavailableError,
  isTelemetryHistoryUnavailableError,
} from "../../lib/dataApi.ts";

test("telemetry history unavailable error preserves structured contract fields", () => {
  const error = new TelemetryHistoryUnavailableError({
    message: "Telemetry history is temporarily unavailable.",
    status: 504,
    code: "TELEMETRY_HISTORY_TIMEOUT",
    retryable: true,
    source: "influx",
  });

  assert.equal(error.message, "Telemetry history is temporarily unavailable.");
  assert.equal(error.status, 504);
  assert.equal(error.code, "TELEMETRY_HISTORY_TIMEOUT");
  assert.equal(error.retryable, true);
  assert.equal(error.source, "influx");
  assert.equal(isTelemetryHistoryUnavailableError(error), true);
});

test("telemetry history unavailable guard rejects ordinary errors", () => {
  assert.equal(isTelemetryHistoryUnavailableError(new Error("boom")), false);
});
