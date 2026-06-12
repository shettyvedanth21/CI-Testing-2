import test from "node:test";
import assert from "node:assert/strict";

import { extractFilename } from "../../lib/downloadFilename.ts";

test("extractFilename prefers content-disposition filename", () => {
  assert.equal(
    extractFilename('attachment; filename="waste_report_job-123.pdf"', "fallback.pdf"),
    "waste_report_job-123.pdf",
  );
});

test("extractFilename supports utf8 encoded filenames", () => {
  assert.equal(
    extractFilename("attachment; filename*=UTF-8''waste_report_%E2%82%B9.pdf", "fallback.pdf"),
    "waste_report_₹.pdf",
  );
});

test("extractFilename falls back when header is missing", () => {
  assert.equal(extractFilename(null, "fallback.pdf"), "fallback.pdf");
});
