"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { fetchCuratedStarterQuestions, sendCopilotMessage, ChatTurn, CopilotResponse, CuratedContext } from "@/lib/copilotApi";
import {
  COPILOT_EMPTY_STATE_MESSAGE,
  COPILOT_SUBTITLE,
  COPILOT_TITLE,
  CURATED_ONLY_HELPER_TEXT,
  CURATED_ONLY_SECTION_SUBTITLE,
  CURATED_ONLY_SECTION_TITLE,
  getApprovedQuestionsOnlyHint,
} from "@/lib/copilotPresentation";

interface UiMessage {
  role: "user" | "assistant";
  content: string;
  response?: CopilotResponse;
}

function CopilotMobileTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: Array<Array<string | number | null>>;
}) {
  return (
    <div className="space-y-3 md:hidden">
      {rows.map((row, rowIndex) => (
        <article key={rowIndex} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <dl className="grid grid-cols-1 gap-2">
            {headers.map((header, index) => (
              <div key={`${rowIndex}-${header}`} className="rounded-lg bg-white px-3 py-2">
                <dt className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{header}</dt>
                <dd className="mt-1 text-sm font-medium text-slate-900">{String(row[index] ?? "—")}</dd>
              </div>
            ))}
          </dl>
        </article>
      ))}
    </div>
  );
}

function getPlottableSeries(chart: CopilotResponse["chart"] | undefined | null): Array<{ label: string; value: number }> {
  if (!chart || !chart.datasets?.length) return [];
  const source = chart.datasets[0]?.data ?? [];
  return chart.labels
    .map((label, index) => ({ label: String(label), value: Number(source[index]) }))
    .filter((point) => Number.isFinite(point.value));
}

const STORAGE_KEY = "factoryops_copilot_messages";

export default function CopilotPage() {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [starterQuestions, setStarterQuestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assistantMessageRefs = useRef<Array<HTMLDivElement | null>>([]);
  const shouldFocusLatestAssistantRef = useRef(false);

  useEffect(() => {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as UiMessage[];
      setMessages(parsed);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchCuratedStarterQuestions()
      .then((questions) => {
        if (!cancelled) {
          setStarterQuestions(questions.map((question) => question.text));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setStarterQuestions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  }, [messages]);

  useEffect(() => {
    if (!shouldFocusLatestAssistantRef.current || loading) return;

    const latestAssistantIndex = [...messages]
      .map((message, index) => ({ message, index }))
      .reverse()
      .find(({ message }) => message.role === "assistant")?.index;

    if (latestAssistantIndex === undefined) return;

    const latestAssistantRef = assistantMessageRefs.current[latestAssistantIndex];
    if (!latestAssistantRef) return;

    shouldFocusLatestAssistantRef.current = false;

    requestAnimationFrame(() => {
      latestAssistantRef.scrollIntoView({
        behavior: "smooth",
        block: "start",
        inline: "nearest",
      });
    });
  }, [loading, messages]);

  const history: ChatTurn[] = useMemo(
    () =>
      messages.map((m) => ({
        role: m.role,
        content: m.content,
      })),
    [messages]
  );

  async function ask(question: string, curatedContext?: CuratedContext | null) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    setError(null);
    setLoading(true);
    shouldFocusLatestAssistantRef.current = true;
    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);

    try {
      const response = await sendCopilotMessage(trimmed, history, curatedContext);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          response,
        },
      ]);
    } catch {
      setError("Could not get answer. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function newChat() {
    setMessages([]);
    setError(null);
    shouldFocusLatestAssistantRef.current = false;
    sessionStorage.removeItem(STORAGE_KEY);
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-slate-900 sm:text-3xl">{COPILOT_TITLE}</h1>
            <p className="text-sm text-slate-600">{COPILOT_SUBTITLE}</p>
          </div>
          <button
            onClick={newChat}
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-100"
          >
            New Chat
          </button>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-3 sm:p-4">
          <div
            className={`mb-4 overflow-y-auto rounded-lg border border-slate-100 bg-slate-50 p-3 sm:p-4 ${
              messages.length === 0 ? "min-h-[12rem]" : "h-[56vh] sm:h-[62vh]"
            }`}
          >
            {messages.length === 0 && (
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-slate-700">
                {COPILOT_EMPTY_STATE_MESSAGE}
              </div>
            )}

            <div className="space-y-4">
              {messages.map((msg, idx) => (
                <div
                  key={idx}
                  ref={(element) => {
                    assistantMessageRefs.current[idx] = msg.role === "assistant" ? element : null;
                  }}
                  className={msg.role === "user" ? "text-right" : "scroll-mt-3 text-left sm:scroll-mt-4"}
                >
                  <div
                    className={
                      msg.role === "user"
                        ? "ml-auto inline-block max-w-[90%] rounded-2xl bg-blue-600 px-4 py-2.5 text-white sm:max-w-[80%]"
                        : "inline-block max-w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-slate-800 sm:max-w-[90%]"
                    }
                  >
                    <p className="text-sm">{msg.content}</p>
                  </div>

                  {msg.role === "assistant" && msg.response && (
                    <div className="mt-3 space-y-3 rounded-2xl border border-slate-200 bg-white p-3 sm:p-4">
                      <div className="rounded-xl bg-slate-50 p-3 text-sm text-slate-700">
                        <div className="mb-1 font-medium text-slate-900">Reasoning</div>
                        {msg.response.reasoning_sections ? (
                          <div className="space-y-2">
                            <div>
                              <div className="font-medium text-slate-900">What happened</div>
                              <div>{msg.response.reasoning_sections.what_happened}</div>
                            </div>
                            <div>
                              <div className="font-medium text-slate-900">Why it matters</div>
                              <div>{msg.response.reasoning_sections.why_it_matters}</div>
                            </div>
                            <div>
                              <div className="font-medium text-slate-900">How calculated</div>
                              <div>{msg.response.reasoning_sections.how_calculated}</div>
                            </div>
                          </div>
                        ) : (
                          <div className="whitespace-pre-line">{msg.response.reasoning}</div>
                        )}
                      </div>
                      {getApprovedQuestionsOnlyHint(msg.response) && (
                        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                          {getApprovedQuestionsOnlyHint(msg.response)}
                        </div>
                      )}

                      {msg.response.data_table && (
                        <>
                        <CopilotMobileTable
                          headers={msg.response.data_table.headers}
                          rows={msg.response.data_table.rows}
                        />
                        <div className="hidden overflow-x-auto md:block">
                          <table className="w-full border-collapse text-left text-sm">
                            <thead>
                              <tr className="bg-slate-100">
                                {msg.response.data_table.headers.map((h) => (
                                  <th key={h} className="border border-slate-200 px-3 py-2 font-medium text-slate-700">
                                    {h}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {msg.response.data_table.rows.map((row, i) => (
                                <tr key={i}>
                                  {row.map((cell, j) => (
                                    <td key={`${i}-${j}`} className="border border-slate-200 px-3 py-2 text-slate-700">
                                      {cell as string | number | null}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        </>
                      )}

                      {msg.response.chart && (
                        <div className="rounded-xl border border-slate-200 p-3">
                          <div className="mb-2 text-sm font-medium text-slate-800">{msg.response.chart.title}</div>
                          {(() => {
                            const series = getPlottableSeries(msg.response.chart);
                            if (series.length === 0) {
                              return <div className="text-sm text-slate-600">No plottable numeric data for chart.</div>;
                            }
                            return (
                              <div className="h-[220px] w-full sm:h-[280px]">
                                <ResponsiveContainer width="100%" height="100%">
                                  {msg.response.chart.type === "bar" ? (
                                    <BarChart data={series}>
                                      <CartesianGrid strokeDasharray="3 3" />
                                      <XAxis dataKey="label" />
                                      <YAxis />
                                      <Tooltip />
                                      <Bar dataKey="value" fill="#2563eb" />
                                    </BarChart>
                                  ) : (
                                    <LineChart data={series}>
                                      <CartesianGrid strokeDasharray="3 3" />
                                      <XAxis dataKey="label" />
                                      <YAxis />
                                      <Tooltip />
                                      <Line type="monotone" dataKey="value" stroke="#2563eb" dot={false} />
                                    </LineChart>
                                  )}
                                </ResponsiveContainer>
                              </div>
                            );
                          })()}
                        </div>
                      )}

                      {msg.response.page_links && msg.response.page_links.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {msg.response.page_links.map((pl) => (
                            <Link key={pl.route + pl.label} href={pl.route} className="inline-flex min-h-10 items-center rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-sm text-blue-700">
                              {pl.label}
                            </Link>
                          ))}
                        </div>
                      )}

                      {msg.response.follow_up_suggestions.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {msg.response.follow_up_suggestions.map((q) => (
                            <button
                              key={q}
                              onClick={() => void ask(q, msg.response?.curated_context ?? null)}
                              className="inline-flex min-h-10 items-center rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-100"
                            >
                              {q}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {loading && (
              <div className="mt-4 inline-block rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
                Thinking...
              </div>
            )}
          </div>

          {error && <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700">{error}</div>}

          <div className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {CURATED_ONLY_HELPER_TEXT}
          </div>

          <div className="mb-3 rounded-md border border-slate-200 bg-white px-4 py-3">
            <div className="text-sm font-medium text-slate-900">{CURATED_ONLY_SECTION_TITLE}</div>
            <div className="mt-1 text-xs text-slate-600">{CURATED_ONLY_SECTION_SUBTITLE}</div>
          </div>

          <div className="flex flex-wrap gap-2">
            {starterQuestions.map((q) => (
              <button
                key={q}
                onClick={() => void ask(q)}
                className="inline-flex min-h-10 items-center rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-100"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
