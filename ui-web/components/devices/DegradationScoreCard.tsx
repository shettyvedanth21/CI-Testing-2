"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  deriveDegradationDisplay,
  formatScore,
  formatConfidence,
  confidenceDescription,
  baselineQualityDescription,
  formatUpdatedMinutesAgo,
  formatSignalCompleteness,
  buildContributionList,
  contributionBarColor,
  formatObservedBaseline,
  formatRawDriftPct,
  deriveTopReasons,
  STATUS_DESCRIPTIONS,
  SCORE_DIRECTION_LABEL,
  STATUS_THRESHOLD_LINES,
  SIGNAL_OPERATOR_LABELS,
  type DegradationBadgeVariant,
} from "@/lib/degradationDisplay";
import type { DegradationScore, DegradationScoreTrendPoint } from "@/lib/deviceApi";

const badgeVariantMap: Record<DegradationBadgeVariant, "success" | "warning" | "info" | "default" | "error"> = {
  success: "success",
  warning: "warning",
  info: "info",
  default: "default",
  error: "error",
};

interface DegradationScoreCardProps {
  data: DegradationScore | null;
  loading: boolean;
  error: string | null;
  staleRefresh?: boolean;
}

function TrendChart({
  points,
  selectedIndex,
  onSelectPoint,
}: {
  points: DegradationScoreTrendPoint[];
  selectedIndex: number | null;
  onSelectPoint: (index: number | null) => void;
}) {
  if (!points || points.length < 2) return null;
  const scores = points.map((p) => p.score);
  const min = Math.min(...scores, 1);
  const max = Math.max(...scores, 10);
  const range = max - min || 1;
  const w = 320;
  const h = 72;
  const padTop = 8;
  const padBot = 16;
  const padLeft = 28;
  const padRight = 8;
  const chartW = w - padLeft - padRight;
  const chartH = h - padTop - padBot;
  const dataPoints = scores.map((s, i) => {
    const x = padLeft + (i / (scores.length - 1)) * chartW;
    const y = padTop + chartH - ((s - min) / range) * chartH;
    return { x, y, score: s };
  });
  const pathD = `M${dataPoints.map((p) => `${p.x},${p.y}`).join(" L")}`;

  const yTicks = [1, 3, 5, 7, 10].filter((v) => v >= min && v <= max);

  const firstDate = points[0]?.computed_at
    ? new Date(points[0].computed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : "";
  const lastDate = points[points.length - 1]?.computed_at
    ? new Date(points[points.length - 1].computed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : "";
  const midIdx = Math.floor(points.length / 2);
  const midDate = points[midIdx]?.computed_at
    ? new Date(points[midIdx].computed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : "";

  const hasContribs = points.some((p) => p.contributions && p.contributions.length > 0);

  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" className="mt-2">
      {STATUS_THRESHOLD_LINES.filter((line) => line.value >= min && line.value <= max).map((line) => {
        const y = padTop + chartH - ((line.value - min) / range) * chartH;
        return (
          <g key={line.status}>
            <line x1={padLeft} y1={y} x2={w - padRight} y2={y} stroke={line.color} strokeWidth="0.5" strokeDasharray="4,3" opacity="0.4" />
            <text x={padLeft - 4} y={y + 3} textAnchor="end" fill={line.color} fontSize="7" opacity="0.6">{line.value}</text>
          </g>
        );
      })}
      {yTicks.map((v) => {
        const y = padTop + chartH - ((v - min) / range) * chartH;
        return (
          <text key={v} x={padLeft - 4} y={y + 3} textAnchor="end" fill="#94a3b8" fontSize="7">{v}</text>
        );
      })}
      <path d={pathD} fill="none" stroke="#64748b" strokeWidth="1.5" />
      {dataPoints.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={i === selectedIndex ? 4 : 2}
          fill={i === selectedIndex ? "#2563eb" : "#64748b"}
          stroke={i === selectedIndex ? "#1d4ed8" : "none"}
          strokeWidth={i === selectedIndex ? 1.5 : 0}
          style={hasContribs ? { cursor: "pointer" } : undefined}
          onClick={() => hasContribs && onSelectPoint(i === selectedIndex ? null : i)}
        />
      ))}
      <text x={padLeft} y={h} fill="#94a3b8" fontSize="7">{firstDate}</text>
      <text x={padLeft + chartW / 2} y={h} textAnchor="middle" fill="#94a3b8" fontSize="7">{midDate}</text>
      <text x={padLeft + chartW} y={h} textAnchor="end" fill="#94a3b8" fontSize="7">{lastDate}</text>
    </svg>
  );
}

function TrendPointContributions({ point }: { point: DegradationScoreTrendPoint }) {
  if (!point.contributions || point.contributions.length === 0) return null;
  const active = point.contributions
    .filter((c) => c.available && c.drift > 0)
    .sort((a, b) => (b.drift * b.weight) - (a.drift * a.weight))
    .slice(0, 3);
  if (active.length === 0) return null;
  const ts = point.computed_at
    ? new Date(point.computed_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
  return (
    <div className="rounded-lg border border-blue-100 bg-blue-50/50 p-2 mt-2">
      <p className="text-[10px] font-semibold text-blue-700 mb-1">
        Score {point.score.toFixed(1)} at {ts}
      </p>
      <div className="space-y-1">
        {active.map((c) => {
          const label = SIGNAL_OPERATOR_LABELS[c.signal] || c.signal.replace(/_/g, " ");
          const pct = Math.min(100, Math.round((Math.abs(c.drift) / 3.0) * 100));
          return (
            <div key={c.signal} className="flex items-center gap-1.5">
              <span className="text-[10px] text-slate-600 truncate flex-1">{label}</span>
              <span className="text-[10px] text-slate-400">{pct}%</span>
              <div className="w-16 h-1.5 rounded-full bg-slate-100 overflow-hidden">
                <div
                  className={cn("h-full rounded-full", pct > 66 ? "bg-red-500" : pct > 33 ? "bg-amber-500" : "bg-blue-400")}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MiniSparkline({ points }: { points: DegradationScoreTrendPoint[] }) {
  if (!points || points.length < 2) return null;
  const scores = points.map((p) => p.score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const range = max - min || 1;
  const w = 180;
  const h = 36;
  const pts = scores.map((s, i) => {
    const x = (i / (scores.length - 1)) * w;
    const y = h - ((s - min) / range) * h;
    return `${x},${y}`;
  });
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="opacity-50 self-center" preserveAspectRatio="none">
      <path d={`M${pts.join(" L")}`} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-slate-500" />
    </svg>
  );
}

export function DegradationScoreCard({ data, loading, error, staleRefresh }: DegradationScoreCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [selectedTrendIdx, setSelectedTrendIdx] = useState<number | null>(null);

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Risk Assessment</p>
        <p className="mt-2 text-sm text-slate-500">Unavailable</p>
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Risk Assessment</p>
        <div className="mt-3 h-8 w-28 animate-pulse rounded bg-slate-200" />
        <div className="mt-2 h-3 w-40 animate-pulse rounded bg-slate-100" />
      </div>
    );
  }

  const display = deriveDegradationDisplay(data?.state, data?.status, data?.confidence);
  const statusDesc = data?.status ? STATUS_DESCRIPTIONS[data.status.toLowerCase()] ?? "" : "";
  const trendPoints = data?.score_trend && data.score_trend.length >= 2 ? data.score_trend : null;
  const contributions = buildContributionList(data?.contributions ?? []);
  const topReasons = deriveTopReasons(data?.contributions ?? []);

  const isLearningOrUnavailable = !display.showScore;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Risk Assessment</p>
        <div className="flex items-center gap-1.5">
          {display.statusLabel && <Badge variant={badgeVariantMap[display.statusBadgeVariant]}>{display.statusLabel}</Badge>}
          {display.staleNote && <Badge variant="warning">Stale</Badge>}
        </div>
      </div>

      {display.showScore && data?.score != null ? (
        <div className="mt-2">
          <div className="flex items-end gap-4">
            <p className={cn("text-5xl font-extrabold leading-none", display.scoreTone)}>
              {formatScore(data.score)}
            </p>
            {trendPoints && <MiniSparkline points={trendPoints} />}
          </div>
          <p className="mt-0.5 text-[10px] text-slate-400">{SCORE_DIRECTION_LABEL}</p>
          {statusDesc && <p className="mt-1 text-sm text-slate-600">{statusDesc}</p>}
        </div>
      ) : isLearningOrUnavailable ? (
        <div className="mt-2">
          <p className="text-2xl font-bold text-slate-400">—</p>
          {data?.state === "learning" && data?.status === "insufficient_signals" && (
            <p className="mt-1 text-sm text-amber-600">Insufficient signal coverage — required sensors not reporting</p>
          )}
          {data?.state === "learning" && data?.status !== "insufficient_signals" && (
            <p className="mt-1 text-sm text-slate-500">Building baseline — this may take several days</p>
          )}
          {data?.state === "unavailable" && (
            <p className="mt-1 text-sm text-slate-500">No data available yet</p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-2xl font-bold text-slate-400">—</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">
        {data?.signal_completeness != null && display.showScore && data.signal_completeness < 1 && (
          <span className="text-amber-600">{formatSignalCompleteness(data.signal_completeness)}</span>
        )}
        {data?.signal_completeness != null && !display.showScore && data.signal_completeness < 1 && (
          <span>{formatSignalCompleteness(data.signal_completeness)}</span>
        )}
        {display.lowConfidenceNote && (
          <span className="text-amber-600">{display.lowConfidenceNote}</span>
        )}
        {display.staleNote && (
          <span className="text-amber-600">{display.staleNote}</span>
        )}
        {staleRefresh && data && !display.staleNote && (
          <span className="text-amber-500">Refresh stalled</span>
        )}
        {data?.updated_minutes_ago != null && !display.staleNote && !display.lowConfidenceNote && !staleRefresh && (
          <span className="text-slate-400">{formatUpdatedMinutesAgo(data.updated_minutes_ago)}</span>
        )}
      </div>

      {display.showDetails && data && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors"
          >
            {expanded ? "Hide breakdown" : "View breakdown"}
          </button>

          {expanded && (
            <div className="mt-3 border-t border-slate-100 pt-4 space-y-4">
              {topReasons.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-slate-700 mb-1.5">Top reasons</p>
                  <ul className="space-y-1">
                    {topReasons.map((reason, i) => (
                      <li key={i} className="text-sm text-slate-600 flex items-start gap-1.5">
                        <span className="text-slate-300 mt-0.5">•</span>
                        <span>{reason}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div>
                <p className="text-xs font-semibold text-slate-700 mb-2">Signal breakdown</p>
                <div className="space-y-2.5">
                  {contributions.map((c) => (
                    <div key={c.signal}>
                      <div className="flex items-center justify-between text-sm mb-0.5">
                        <span className="text-slate-700">
                          {c.operatorLabel}
                          {c.available && c.weightPct > 0 && (
                            <span className="text-slate-400 ml-1 text-xs">({c.weightPct}% weight)</span>
                          )}
                        </span>
                        {!c.available ? (
                          <span className="text-xs text-slate-400 italic">No data</span>
                        ) : (
                          <span className="text-xs text-slate-400">{c.barPct}%</span>
                        )}
                      </div>
                      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                        {!c.available ? (
                          <div className="h-full w-full rounded-full border border-dashed border-slate-300 bg-slate-50" />
                        ) : (
                          <div
                            className={cn("h-full rounded-full transition-all", contributionBarColor(c.driftMagnitude, c.available))}
                            style={{ width: `${c.barPct}%` }}
                          />
                        )}
                      </div>
                      {c.available && (c.observedValue != null || c.baselineValue != null) && (
                        <p className="text-[11px] text-slate-500 mt-0.5">
                          {formatObservedBaseline(c.signal, c.observedValue, c.baselineValue)}
                          {c.rawDrift != null && Number.isFinite(c.rawDrift) && (
                            <span className="ml-1.5">{formatRawDriftPct(c.rawDrift)}</span>
                          )}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {trendPoints && (
                <div>
                  <p className="text-xs font-semibold text-slate-700 mb-1">7-Day trend</p>
                  <TrendChart points={trendPoints} selectedIndex={selectedTrendIdx} onSelectPoint={setSelectedTrendIdx} />
                  {selectedTrendIdx != null && trendPoints[selectedTrendIdx] && (
                    <TrendPointContributions point={trendPoints[selectedTrendIdx]} />
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                {data.baseline_quality && (
                  <span>Baseline: {baselineQualityDescription(data.baseline_quality)}</span>
                )}
                {data.confidence != null && data.confidence < 1 && (
                  <span>Confidence: {formatConfidence(data.confidence)} — {confidenceDescription(data.confidence)}</span>
                )}
                {data.signal_completeness != null && data.signal_completeness < 1 && (
                  <span>{formatSignalCompleteness(data.signal_completeness)}</span>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
