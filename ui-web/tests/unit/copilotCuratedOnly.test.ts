import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  APPROVED_QUESTIONS_ONLY_HINT,
  COPILOT_EMPTY_STATE_MESSAGE,
  CURATED_ONLY_HELPER_TEXT,
  CURATED_ONLY_SECTION_SUBTITLE,
  CURATED_ONLY_SECTION_TITLE,
  getApprovedQuestionsOnlyHint,
} from "../../lib/copilotPresentation.ts";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const copilotPagePath = path.resolve(__dirname, "../../app/(protected)/copilot/page.tsx");
const copilotPageSource = readFileSync(copilotPagePath, "utf-8");

test("copilot page copy no longer implies open-ended chat", () => {
  assert.equal(copilotPageSource.includes("Ask me anything"), false);
  assert.equal(copilotPageSource.includes("ask me anything"), false);
  assert.equal(copilotPageSource.includes("Ask any question"), false);
  assert.equal(copilotPageSource.includes("freeform AI assistant"), false);
  assert.equal(copilotPageSource.includes("anything about your factory"), false);
});

test("open-ended input form is removed from the page", () => {
  assert.equal(copilotPageSource.includes("placeholder={COPILOT_INPUT_PLACEHOLDER}"), false);
  assert.equal(copilotPageSource.includes("type=\"submit\""), false);
  assert.equal(copilotPageSource.includes("onSubmit={onSubmit}"), false);
});

test("curated-only helper text is visible on the page", () => {
  assert.match(CURATED_ONLY_HELPER_TEXT, /approved deterministic factory questions only/i);
  assert.equal(copilotPageSource.includes("CURATED_ONLY_HELPER_TEXT"), true);
});

test("approved questions section is visible on the page", () => {
  assert.match(CURATED_ONLY_SECTION_TITLE, /approved questions/i);
  assert.match(CURATED_ONLY_SECTION_SUBTITLE, /supported operational questions/i);
  assert.equal(copilotPageSource.includes("CURATED_ONLY_SECTION_TITLE"), true);
  assert.equal(copilotPageSource.includes("CURATED_ONLY_SECTION_SUBTITLE"), true);
});

test("starter question flow remains prominent", () => {
  assert.equal(copilotPageSource.includes("fetchCuratedStarterQuestions"), true);
  assert.equal(copilotPageSource.includes("starterQuestions.map"), true);
});

test("unsupported approved-questions-only fallback has clear safe hint", () => {
  const hint = getApprovedQuestionsOnlyHint({
    answer: "This Copilot currently supports approved factory questions only.",
    reasoning: "deterministic fallback",
    follow_up_suggestions: ["Summarize today's factory performance"],
    error_code: "APPROVED_QUESTIONS_ONLY",
  });

  assert.equal(hint, APPROVED_QUESTIONS_ONLY_HINT);
  assert.equal(copilotPageSource.includes("getApprovedQuestionsOnlyHint"), true);
});

test("supported response rendering still includes answer table and chart flows", () => {
  assert.equal(copilotPageSource.includes("msg.content"), true);
  assert.equal(copilotPageSource.includes("msg.response.data_table"), true);
  assert.equal(copilotPageSource.includes("msg.response.chart"), true);
});

test("curated-only copy constants reflect approved deterministic messaging", () => {
  assert.match(COPILOT_EMPTY_STATE_MESSAGE, /supported factory question/i);
  assert.match(COPILOT_EMPTY_STATE_MESSAGE, /deterministic/i);
});
