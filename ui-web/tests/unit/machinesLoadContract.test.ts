import test from "node:test";
import assert from "node:assert/strict";

import { loadMachinesInitialChannels } from "../../lib/machinesLoadContract.ts";

test("machines initial load becomes usable when fleet succeeds even if summary is still pending", async () => {
  let resolveSummary: (() => void) | undefined;
  let summaryStarted = false;

  const result = await loadMachinesInitialChannels({
    loadFleet: async () => "fleet-ready",
    loadSummary: () =>
      new Promise<void>((resolve) => {
        summaryStarted = true;
        resolveSummary = resolve;
      }),
  });

  assert.equal(summaryStarted, true);
  assert.equal(result.fatalError, null);

  let summarySettled = false;
  void result.summaryPromise.then(() => {
    summarySettled = true;
  });
  await Promise.resolve();
  assert.equal(summarySettled, false);

  const finishSummary = resolveSummary as () => void;
  finishSummary();
  await result.summaryPromise;
  assert.equal(summarySettled, true);
});

test("machines initial load returns fatal error when fleet cards fail", async () => {
  const result = await loadMachinesInitialChannels({
    loadFleet: async () => {
      throw new Error("fleet timeout");
    },
    loadSummary: async () => "summary-ready",
  });

  assert.equal(result.fatalError, "fleet timeout");
  await result.summaryPromise;
});

test("machines initial load retries one transient fleet timeout before failing", async () => {
  let attempts = 0;

  const result = await loadMachinesInitialChannels({
    loadFleet: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("Request timed out");
      }
      return "fleet-ready";
    },
    loadSummary: async () => "summary-ready",
  });

  assert.equal(attempts, 2);
  assert.equal(result.fatalError, null);
  await result.summaryPromise;
});

test("machines initial load still fails after two transient fleet timeouts", async () => {
  let attempts = 0;

  const result = await loadMachinesInitialChannels({
    loadFleet: async () => {
      attempts += 1;
      throw new Error("Request timed out");
    },
    loadSummary: async () => "summary-ready",
  });

  assert.equal(attempts, 2);
  assert.equal(result.fatalError, "Request timed out");
  await result.summaryPromise;
});

test("machines initial load does not turn summary failure into a fatal page error", async () => {
  const result = await loadMachinesInitialChannels({
    loadFleet: async () => "fleet-ready",
    loadSummary: async () => {
      throw new Error("summary timeout");
    },
  });

  assert.equal(result.fatalError, null);
  await result.summaryPromise;
});
