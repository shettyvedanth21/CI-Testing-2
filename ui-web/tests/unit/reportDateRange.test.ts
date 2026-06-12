import test from "node:test";
import assert from "node:assert/strict";

import {
  getCustomEndDateBounds,
  getCustomEndDateBoundsWithLimit,
  getRangeDaySpan,
  getWasteDefaultRange,
  resolveCustomEndFromStart,
  resolveCustomEndFromStartWithLimit,
  resolveMonthRange,
  resolvePresetRange,
} from "../../lib/reportDateRange.ts";

const FIXED_NOW = new Date("2026-04-16T08:00:00Z");

test("quick preset range resolves expected waste/energy-compatible ISO dates", () => {
  const lastSeven = resolvePresetRange(7, 0, FIXED_NOW);
  assert.deepEqual(lastSeven, {
    start: "2026-04-10",
    end: "2026-04-16",
  });
});

test("today preset resolves to a single-day range", () => {
  const today = resolvePresetRange(1, 0, FIXED_NOW);
  assert.deepEqual(today, {
    start: "2026-04-16",
    end: "2026-04-16",
  });
});

test("yesterday preset resolves to a single-day range", () => {
  const yesterday = resolvePresetRange(1, 1, FIXED_NOW);
  assert.deepEqual(yesterday, {
    start: "2026-04-15",
    end: "2026-04-15",
  });
});

test("month picker range resolves first and last day of target month", () => {
  const marchRange = resolveMonthRange(new Date("2026-03-01T00:00:00Z"));
  assert.deepEqual(marchRange, {
    start: "2026-03-01",
    end: "2026-03-31",
  });
});

test("custom start auto-resolves bounded custom end date", () => {
  const end = resolveCustomEndFromStart("2026-02-01", FIXED_NOW);
  assert.equal(end, "2026-04-16");
});

test("custom end bounds allow single-day ranges and cap at today within 90 inclusive days", () => {
  const bounds = getCustomEndDateBounds("2026-04-01", FIXED_NOW);
  assert.deepEqual(bounds, {
    min: "2026-04-01",
    max: "2026-04-16",
  });
});

test("custom range helpers support stricter max-day caps", () => {
  assert.equal(resolveCustomEndFromStartWithLimit("2026-01-01", FIXED_NOW, 30), "2026-01-30");
  assert.deepEqual(getCustomEndDateBoundsWithLimit("2026-01-01", FIXED_NOW, 30), {
    min: "2026-01-01",
    max: "2026-01-30",
  });
});

test("range day span returns inclusive day count", () => {
  assert.equal(getRangeDaySpan("2026-04-01", "2026-04-01"), 1);
  assert.equal(getRangeDaySpan("2026-04-01", "2026-04-30"), 30);
});

test("waste default range remains sensible and deterministic", () => {
  assert.deepEqual(getWasteDefaultRange(FIXED_NOW), {
    start: "2026-04-10",
    end: "2026-04-16",
  });
});

test("preset ranges follow local calendar day semantics after midnight in IST", () => {
  const shortlyAfterMidnightIst = new Date("2026-05-09T01:42:00+05:30");

  assert.deepEqual(resolvePresetRange(1, 0, shortlyAfterMidnightIst), {
    start: "2026-05-09",
    end: "2026-05-09",
  });

  assert.deepEqual(resolvePresetRange(1, 1, shortlyAfterMidnightIst), {
    start: "2026-05-08",
    end: "2026-05-08",
  });

  assert.deepEqual(getWasteDefaultRange(shortlyAfterMidnightIst), {
    start: "2026-05-03",
    end: "2026-05-09",
  });
});
