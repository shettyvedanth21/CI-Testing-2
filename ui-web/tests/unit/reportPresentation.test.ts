import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const reportPresentationPath = path.resolve(__dirname, "../../lib/reportPresentation.ts");
const reportsPagePath = path.resolve(__dirname, "../../app/(protected)/reports/page.tsx");

const reportPresentationSource = readFileSync(reportPresentationPath, "utf-8");
const reportsPageSource = readFileSync(reportsPagePath, "utf-8");

test("report presentation keeps no-coverage downloads audit-only, not business-successful", () => {
  assert.equal(
    reportPresentationSource.includes("This artifact remains available for audit, but this report is not usable for business decisions."),
    true,
  );
  assert.equal(reportPresentationSource.includes('return coverageSummary ?? "This report is not usable for business decisions.";'), true);
});

test("report presentation treats insufficient coverage as business-blocked", () => {
  assert.equal(reportPresentationSource.includes('coverage.level === "no_coverage" || coverage.level === "insufficient_coverage"'), true);
  assert.equal(
    reportPresentationSource.includes('return `${coverageSummary ?? "This report is not usable for business decisions."} The current output is available to review, but this report is not usable for business decisions.`;'),
    true,
  );
});

test("report presentation keeps partial coverage usable but visibly warned", () => {
  assert.equal(reportPresentationSource.includes('return coverageSummary ?? "This result is usable with coverage warnings.";'), true);
  assert.equal(
    reportPresentationSource.includes("Review the coverage warnings before using the downloaded artifact for business decisions."),
    true,
  );
});

test("report presentation preserves a distinct real-success path", () => {
  assert.equal(reportPresentationSource.includes('return "Ready to download";'), true);
  assert.equal(
    reportPresentationSource.includes('return "This report completed successfully. The output is ready to review or download.";'),
    true,
  );
});

test("reports page renders the shared coverage callout instead of hiding coverage in highlights only", () => {
  assert.equal(reportsPageSource.includes("Coverage state"), true);
  assert.equal(reportsPageSource.includes("selectedReportPresentation?.coverageCallout"), true);
  assert.equal(
    reportsPageSource.includes("This report finished successfully. Use the actions below to review the output or download the artifact when it is ready."),
    false,
  );
});
