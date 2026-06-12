import test from "node:test";
import assert from "node:assert/strict";

import {
  getActivityHistoryDegradedMessage,
  isActivityHistoryAbortError,
  isTransientActivityHistoryError,
} from "../../lib/activityHistoryResilience.ts";

test("abort-like activity history failures are treated as harmless cancellation", () => {
  const error = new Error("The request was aborted");
  error.name = "AbortError";

  assert.equal(isActivityHistoryAbortError(error), true);
  assert.equal(isTransientActivityHistoryError(error), true);
});

test("network fetch misses are treated as transient activity history degradation", () => {
  assert.equal(isTransientActivityHistoryError(new TypeError("Failed to fetch")), true);
  assert.equal(isTransientActivityHistoryError(new Error("fetch failed while contacting alerts service")), true);
  assert.equal(isTransientActivityHistoryError(new Error("net::ERR_FAILED")), true);
  assert.equal(isTransientActivityHistoryError(new Error("Load failed")), true);
});

test("real backend failures remain non-transient", () => {
  assert.equal(isTransientActivityHistoryError(new Error("HTTP 503")), false);
  assert.equal(isTransientActivityHistoryError(new Error("DEVICE_NOT_FOUND")), false);
});

test("degraded messaging distinguishes empty history from cached history", () => {
  assert.match(getActivityHistoryDegradedMessage(false), /temporarily unavailable/i);
  assert.match(getActivityHistoryDegradedMessage(true), /out of date/i);
});
