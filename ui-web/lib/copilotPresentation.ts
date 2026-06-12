import type { CopilotResponse } from "./copilotApi";

export const COPILOT_TITLE = "Factory Copilot";
export const COPILOT_SUBTITLE = "Approved operational insights";
export const COPILOT_EMPTY_STATE_MESSAGE =
  "Choose a supported factory question to get accurate, deterministic insights.";
export const CURATED_ONLY_HELPER_TEXT =
  "Copilot currently supports approved deterministic factory questions only. Select a starter question or continue with the suggested follow-ups below each answer.";
export const CURATED_ONLY_SECTION_TITLE = "Approved questions";
export const CURATED_ONLY_SECTION_SUBTITLE =
  "Choose one of the supported operational questions to see deterministic answers, tables, and charts.";
export const APPROVED_QUESTIONS_ONLY_HINT = "Try one of the suggested supported questions below.";

export function getApprovedQuestionsOnlyHint(response: CopilotResponse | undefined): string | null {
  if (!response) {
    return null;
  }
  if (response.error_code === "APPROVED_QUESTIONS_ONLY") {
    return APPROVED_QUESTIONS_ONLY_HINT;
  }
  return null;
}
