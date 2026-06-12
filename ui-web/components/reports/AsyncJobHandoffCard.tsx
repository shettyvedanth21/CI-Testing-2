"use client";

import Link from "next/link";
import {
  formatJobSeconds,
  formatJobStatusSummary,
  getRunningJobTruthfulnessNote,
  getUserFacingJobStatusLabel,
  shouldShowRunningJobEta,
} from "@/lib/asyncJobPresentation";

type AsyncJobStatus = {
  status: string;
  progress?: number | null;
  phase_label?: string | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
  estimated_completion_seconds?: number | null;
  activity_state?: "active" | "stalled" | "unknown" | null;
  eta_reliable?: boolean | null;
  result_ready?: boolean;
  artifact_ready?: boolean;
  download_ready?: boolean;
  error_code?: string | null;
  error_message?: string | null;
};

interface AsyncJobHandoffCardProps {
  title: string;
  backgroundMessage: string;
  historyLabel: string;
  historyHref: string;
  summary?: string;
  status: AsyncJobStatus | null;
  statusBadges?: string[];
  footerMessage?: string;
  primaryActionLabel?: string;
  onPrimaryAction?: () => void;
  secondaryActionLabel?: string;
  onSecondaryAction?: () => void;
}

export function AsyncJobHandoffCard({
  title,
  backgroundMessage,
  historyLabel,
  historyHref,
  summary,
  status,
  statusBadges,
  footerMessage,
  primaryActionLabel,
  onPrimaryAction,
  secondaryActionLabel,
  onSecondaryAction,
}: AsyncJobHandoffCardProps) {
  const progress = Math.max(0, Math.min(100, Number(status?.progress ?? 0)));
  const statusLine = status ? formatJobStatusSummary(status) : "Waiting for the latest status";
  const etaText = shouldShowRunningJobEta(status ?? { status: "" }) ? formatJobSeconds(status?.estimated_completion_seconds) : "";
  const runningTruthfulnessNote = status ? getRunningJobTruthfulnessNote(status) : null;
  const isReady = Boolean(status?.result_ready);
  const statusLabel = getUserFacingJobStatusLabel(status?.status);
  const hasArtifactIssue = Boolean(
    isReady &&
    !status?.download_ready &&
    !status?.artifact_ready &&
    (status?.error_code === "ARTIFACT_GENERATION_FAILED" || status?.error_code === "ARTIFACT_UPLOAD_FAILED"),
  );
  const isFailed = status?.status === "failed";
  const theme = isFailed
    ? {
        container: "border-rose-200 bg-rose-50 text-rose-950",
        eyebrow: "text-rose-700",
        primary: "border-rose-700 bg-rose-700 text-white",
        secondary: "border-rose-300 bg-white text-rose-900 hover:bg-rose-100",
        tertiary: "text-rose-800 hover:text-rose-950",
        footer: "text-rose-800",
      }
    : hasArtifactIssue
      ? {
          container: "border-amber-200 bg-amber-50 text-amber-950",
          eyebrow: "text-amber-700",
          primary: "border-amber-700 bg-amber-700 text-white",
          secondary: "border-amber-300 bg-white text-amber-900 hover:bg-amber-100",
          tertiary: "text-amber-800 hover:text-amber-950",
          footer: "text-amber-800",
        }
      : {
          container: "border-emerald-200 bg-emerald-50 text-emerald-950",
          eyebrow: "text-emerald-700",
          primary: "border-emerald-700 bg-emerald-700 text-white",
          secondary: "border-emerald-300 bg-white text-emerald-900 hover:bg-emerald-100",
          tertiary: "text-emerald-800 hover:text-emerald-950",
          footer: "text-emerald-800",
        };
  const eyebrowLabel = isFailed ? "Needs attention" : hasArtifactIssue ? "Completed with issues" : "Accepted";

  return (
    <div className={`rounded-xl border p-5 shadow-sm ${theme.container}`}>
      <div className="flex flex-col gap-4">
        <div>
          <div className={`text-xs font-semibold uppercase tracking-[0.16em] ${theme.eyebrow}`}>{eyebrowLabel}</div>
          <h2 className="mt-2 text-lg font-semibold">{title}</h2>
          <p className="mt-1 text-sm">{backgroundMessage}</p>
          {summary ? <p className="mt-2 text-sm opacity-90">{summary}</p> : null}
        </div>

        <div className="rounded-lg border border-emerald-200 bg-white/70 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-slate-900">{statusLabel}</div>
              <div className="mt-1 text-sm text-slate-600">{statusLine}</div>
            </div>
            <div className="text-sm font-semibold text-slate-900">{progress}%</div>
          </div>
          <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-200">
            <div
              className="h-full rounded-full bg-[linear-gradient(135deg,#10b981,#0f766e)] transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">
            {status?.phase_label ? <span>Current step: {status.phase_label}</span> : null}
            {typeof status?.queue_position === "number" ? <span>Queue position: {status.queue_position + 1}</span> : null}
            {etaText ? <span>ETA: {etaText}</span> : null}
            {!etaText && runningTruthfulnessNote ? <span>{runningTruthfulnessNote}</span> : null}
            {isReady ? <span>Result ready</span> : null}
            {status?.artifact_ready ? <span>Download ready</span> : null}
            {statusBadges?.map((badge) => <span key={badge}>{badge}</span>)}
          </div>
        </div>

        <div className="flex flex-wrap gap-3">
          <Link
            href={historyHref}
            className={`inline-flex h-10 items-center justify-center rounded-xl border px-4 text-sm font-semibold transition hover:brightness-105 ${theme.primary}`}
          >
            {historyLabel}
          </Link>
          {primaryActionLabel && onPrimaryAction ? (
            <button
              type="button"
              onClick={onPrimaryAction}
              className={`inline-flex h-10 items-center justify-center rounded-xl border px-4 text-sm font-semibold transition ${theme.secondary}`}
            >
              {primaryActionLabel}
            </button>
          ) : null}
          {secondaryActionLabel && onSecondaryAction ? (
            <button
              type="button"
              onClick={onSecondaryAction}
              className={`inline-flex h-10 items-center justify-center rounded-xl border border-transparent bg-transparent px-1 text-sm font-medium transition ${theme.tertiary}`}
            >
              {secondaryActionLabel}
            </button>
          ) : null}
        </div>

        <p className={`text-xs ${theme.footer}`}>
          {footerMessage
            ? footerMessage
            : isReady
              ? "This job has finished. You can open the result now or revisit it later from history."
              : `You do not need to stay on this page. Track progress in ${historyLabel}.`}
        </p>
      </div>
    </div>
  );
}
