import test from "node:test";
import assert from "node:assert/strict";

import { formatIST, formatISTCompact } from "../../lib/utils.ts";

test("formatISTCompact renders UTC timestamps in IST for analytics history", () => {
  assert.equal(
    formatISTCompact("2026-05-16T16:43:00Z"),
    "16 May 2026, 10:13 pm",
  );
});

test("formatISTCompact treats timezone-less backend timestamps as UTC before rendering IST", () => {
  assert.equal(
    formatISTCompact("2026-05-16T16:43:00"),
    "16 May 2026, 10:13 pm",
  );
});

test("formatIST keeps the explicit IST suffix for detailed timestamp contexts", () => {
  assert.equal(
    formatIST("2026-05-16T16:43:00Z"),
    "16 May 2026, 10:13:00 pm IST",
  );
});
