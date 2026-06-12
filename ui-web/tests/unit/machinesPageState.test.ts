import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMachinesFilterKey,
  getMachinesEmptyStateCopy,
  normalizeMachinesSearchInput,
  shouldResetMachinesPage,
} from "../../lib/machinesPageState.ts";

test("machines search normalization trims and collapses whitespace", () => {
  assert.equal(normalizeMachinesSearchInput("  Press   Line   1  "), "Press Line 1");
});

test("machines page resets to page 1 when the search filter changes", () => {
  const previousKey = buildMachinesFilterKey({
    plantId: null,
    operationalStatus: "all",
    search: "",
  });
  const nextKey = buildMachinesFilterKey({
    plantId: null,
    operationalStatus: "all",
    search: "press",
  });

  assert.equal(
    shouldResetMachinesPage({
      currentPage: 3,
      previousFilterKey: previousKey,
      nextFilterKey: nextKey,
    }),
    true,
  );
});

test("machines empty state becomes search-specific when no device matches", () => {
  assert.deepEqual(
    getMachinesEmptyStateCopy({
      search: "press",
      hasPlantFilter: false,
      hasOperationalStatusFilter: false,
    }),
    {
      title: "No machines match your search",
      message: "No devices match \"press\". Try a different device name or clear the search.",
    },
  );
});
