import { buildServiceUrl, readJson } from "./base";

export type CopilotHistoryTurn = {
  role: "user" | "assistant";
  content: string;
};

export type CopilotResponse = {
  answer: string;
  reasoning: string;
  reasoningSections?: {
    what_happened: string;
    why_it_matters: string;
    how_calculated: string;
  } | null;
  dataTable?: {
    headers: string[];
    rows: Array<Array<string | number | null>>;
  } | null;
  chart?: {
    type: "bar" | "line" | "pie";
    title: string;
    labels: string[];
    datasets: Array<{ label: string; data: number[] }>;
  } | null;
  pageLinks?: Array<{ label: string; route: string }> | null;
  followUpSuggestions: string[];
  errorCode?: "AI_UNAVAILABLE" | "NOT_CONFIGURED" | "QUERY_BLOCKED" | "INTERNAL_ERROR" | null;
};

type RawCopilotResponse = {
  answer: string;
  reasoning: string;
  reasoning_sections?: CopilotResponse["reasoningSections"];
  data_table?: CopilotResponse["dataTable"];
  chart?: CopilotResponse["chart"];
  page_links?: CopilotResponse["pageLinks"];
  follow_up_suggestions?: string[];
  error_code?: CopilotResponse["errorCode"];
};

type CopilotHealth = {
  status?: string;
  provider?: string;
  provider_configured?: boolean;
};

const copilotChatUrl = buildServiceUrl(8007, "/api/v1/copilot/chat");
const copilotHealthUrl = buildServiceUrl(8007, "/health");

function mapCopilotResponse(payload: RawCopilotResponse): CopilotResponse {
  return {
    answer: payload.answer,
    reasoning: payload.reasoning,
    reasoningSections: payload.reasoning_sections ?? null,
    dataTable: payload.data_table ?? null,
    chart: payload.chart ?? null,
    pageLinks: payload.page_links ?? null,
    followUpSuggestions: payload.follow_up_suggestions ?? [],
    errorCode: payload.error_code ?? null,
  };
}

export async function sendMessage(
  message: string,
  history: CopilotHistoryTurn[]
): Promise<CopilotResponse | null> {
  const payload = await readJson<RawCopilotResponse>(copilotChatUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message,
      conversation_history: history.slice(-5),
    }),
  });

  return payload ? mapCopilotResponse(payload) : null;
}

export async function getCopilotHealth(): Promise<CopilotHealth | null> {
  return readJson<CopilotHealth>(copilotHealthUrl);
}
