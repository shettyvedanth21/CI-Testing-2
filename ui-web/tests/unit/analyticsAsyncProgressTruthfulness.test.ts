import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const asyncPresentationPath = path.resolve(__dirname, "../../lib/asyncJobPresentation.ts");
const analyticsPagePath = path.resolve(__dirname, "../../app/(protected)/analytics/page.tsx");
const handoffCardPath = path.resolve(__dirname, "../../components/reports/AsyncJobHandoffCard.tsx");

const asyncPresentationSource = readFileSync(asyncPresentationPath, "utf-8");
const analyticsPageSource = readFileSync(analyticsPagePath, "utf-8");
const handoffCardSource = readFileSync(handoffCardPath, "utf-8");

test("analytics running-state helpers distinguish active overdue jobs from suspicious stalls", () => {
  assert.equal(
    asyncPresentationSource.includes('if (status.activity_state === "active" && status.eta_reliable === false)'),
    true,
  );
  assert.equal(asyncPresentationSource.includes("Worker is still active. Final timing can vary"), true);
  assert.equal(asyncPresentationSource.includes('if (status.activity_state === "stalled")'), true);
  assert.equal(asyncPresentationSource.includes("No recent worker heartbeat detected"), true);
});

test("analytics ETA rendering is gated by reliability instead of blindly showing stale seconds", () => {
  assert.equal(asyncPresentationSource.includes("status.eta_reliable !== false"), true);
  assert.equal(asyncPresentationSource.includes("shouldShowRunningJobEta"), true);
  assert.equal(analyticsPageSource.includes("const etaText = shouldShowRunningJobEta(job) ? formatJobSeconds(job.estimated_completion_seconds) : \"\";"), true);
  assert.equal(
    analyticsPageSource.includes("job.status === \"running\" && job.estimated_completion_seconds ?"),
    false,
  );
});

test("analytics history and handoff views surface alive-vs-stalled truthfulness copy", () => {
  assert.equal(analyticsPageSource.includes("getRunningJobTruthfulnessNote(job)"), true);
  assert.equal(handoffCardSource.includes("getRunningJobTruthfulnessNote"), true);
  assert.equal(handoffCardSource.includes("!etaText && runningTruthfulnessNote ?"), true);
});
