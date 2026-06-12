import { COPILOT_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface CuratedContext {
  device_id?: string | null;
}

export interface CuratedQuestionItem {
  id: string;
  text: string;
}

export interface CopilotResponse {
  answer: string;
  reasoning: string;
  reasoning_sections?: {
    what_happened: string;
    why_it_matters: string;
    how_calculated: string;
  } | null;
  data_table?: {
    headers: string[];
    rows: Array<Array<string | number | null>>;
  } | null;
  chart?: {
    type: "bar" | "line" | "pie";
    title: string;
    labels: string[];
    datasets: Array<{ label: string; data: number[] }>;
  } | null;
  page_links?: Array<{ label: string; route: string }> | null;
  follow_up_suggestions: string[];
  curated_context?: CuratedContext | null;
  error_code?: string | null;
}

export interface CuratedQuestionsResponse {
  starter_questions: CuratedQuestionItem[];
}

export async function sendCopilotMessage(
  message: string,
  history: ChatTurn[],
  curatedContext?: CuratedContext | null
): Promise<CopilotResponse> {
  const res = await apiFetch(`${COPILOT_SERVICE_BASE}/api/v1/copilot/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      conversation_history: history.slice(-5).map((h) => ({
        role: h.role,
        content: h.content,
      })),
      curated_context: curatedContext ?? null,
    }),
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  return res.json();
}

export async function fetchCuratedStarterQuestions(): Promise<CuratedQuestionItem[]> {
  const res = await apiFetch(`${COPILOT_SERVICE_BASE}/api/v1/copilot/curated-questions`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const payload = (await res.json()) as CuratedQuestionsResponse;
  return payload.starter_questions ?? [];
}
