import test from "node:test";
import assert from "node:assert/strict";

import { recoverStableFleetSnapshot } from "../../lib/fleetSnapshotRecovery.ts";

test("recoverStableFleetSnapshot waits for two matching snapshots before resolving", async () => {
  const snapshots = [
    [{ id: "A", version: 1 }],
    [{ id: "A", version: 2 }],
    [{ id: "A", version: 2 }],
  ];
  const applied: Array<Array<{ id: string; version: number }>> = [];
  let nowValue = 0;

  const devices = await recoverStableFleetSnapshot({
    fetchSnapshot: async () => snapshots.shift() ?? [{ id: "A", version: 2 }],
    applySnapshot: async (items) => {
      applied.push(items);
    },
    timeoutMs: 5_000,
    pollIntervalMs: 100,
    now: () => nowValue,
    sleep: async (ms) => {
      nowValue += ms;
    },
  });

  assert.deepEqual(
    applied.map((items) => items.map((item) => item.version)),
    [[1], [2], [2]],
  );
  assert.deepEqual(devices, [{ id: "A", version: 2 }]);
});

test("recoverStableFleetSnapshot returns the latest snapshot when timeout is reached", async () => {
  const snapshots = [
    [{ id: "A", version: 1 }],
    [{ id: "A", version: 2 }],
  ];
  let nowValue = 0;

  const devices = await recoverStableFleetSnapshot({
    fetchSnapshot: async () => snapshots.shift() ?? [{ id: "A", version: 3 }],
    timeoutMs: 50,
    pollIntervalMs: 50,
    now: () => nowValue,
    sleep: async (ms) => {
      nowValue += ms;
    },
  });

  assert.deepEqual(devices, [{ id: "A", version: 2 }]);
});
