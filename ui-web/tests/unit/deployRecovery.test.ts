import test from "node:test";
import assert from "node:assert/strict";

import {
  DEPLOY_RECOVERY_COOLDOWN_MS,
  extractDeployRecoveryMessage,
  isRecoverableDeployError,
  shouldAttemptAutomaticDeployRecovery,
} from "../../lib/deployRecovery.ts";

test("recoverable deploy errors include stale RSC and server-action mismatches", () => {
  assert.equal(isRecoverableDeployError("Failed to fetch RSC payload. Falling back to browser navigation."), true);
  assert.equal(isRecoverableDeployError(new Error("Server action request failed after deploy boundary changed.")), true);
  assert.equal(isRecoverableDeployError("Loading chunk 42 failed"), true);
});

test("generic runtime errors are not treated as deploy-recovery candidates", () => {
  assert.equal(isRecoverableDeployError("Validation failed for device payload"), false);
  assert.equal(isRecoverableDeployError(new Error("Select an organisation before continuing.")), false);
});

test("deploy recovery extracts nested rejection messages safely", () => {
  assert.equal(
    extractDeployRecoveryMessage({ reason: new Error("ChunkLoadError: Loading chunk app failed") }),
    "ChunkLoadError: Loading chunk app failed",
  );
});

test("automatic deploy recovery is rate-limited to avoid reload loops", () => {
  const now = 1_714_500_000_000;
  assert.equal(shouldAttemptAutomaticDeployRecovery(null, now), true);
  assert.equal(shouldAttemptAutomaticDeployRecovery(now - DEPLOY_RECOVERY_COOLDOWN_MS - 1, now), true);
  assert.equal(shouldAttemptAutomaticDeployRecovery(now - 1_000, now), false);
});
