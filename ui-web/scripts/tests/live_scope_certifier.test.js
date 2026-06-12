const test = require("node:test");
const assert = require("node:assert/strict");

const {
  isDeviceOptionDisambiguated,
  waitForScopeModeReady,
} = require("../live_scope_certifier.js");

test("duplicate-name proof accepts label with device id", () => {
  assert.equal(
    isDeviceOptionDisambiguated({
      label: "Certification Duplicate Machine (01KNHPZSCFCN0EW94DX55HEWBX)",
      description: "Plant A · 01KNHPZSCFCN0EW94DX55HEWBX",
    }),
    true,
  );
});

test("duplicate-name proof accepts plant and generated-id description", () => {
  assert.equal(
    isDeviceOptionDisambiguated({
      label: "Certification Duplicate Machine",
      description: "Plant A · 01KNHPZSCFCN0EW94DX55HEWBX",
    }),
    true,
  );
});

test("scope readiness waits on the selector-scoped plant panel", async () => {
  const calls = [];
  const fakePage = {
    getByTestId(id) {
      return {
        async click() {
          calls.push(["click", id]);
        },
      };
    },
    async waitForFunction(fn, selector, options) {
      calls.push(["waitForFunction", selector, options.timeout]);
      assert.equal(
        selector,
        '[data-testid="device-scope-plant-option"], [data-testid="device-scope-plant-empty"]',
      );
      assert.equal(typeof fn, "function");
    },
  };

  await waitForScopeModeReady(fakePage, "plant");

  assert.deepEqual(calls, [
    ["click", "device-scope-mode-plant"],
    ["waitForFunction", '[data-testid="device-scope-plant-option"], [data-testid="device-scope-plant-empty"]', 30000],
  ]);
});
