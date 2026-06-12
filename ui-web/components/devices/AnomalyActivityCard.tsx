"use client";

import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  deriveAnomalyDisplay,
  formatWeekOverWeekTone,
  deriveSeverityTone,
  deriveWeekOverWeekLabel,
  formatLastAnomalySummary,
  formatAnomalyTimeAgo,
  formatBaselineContext,
  formatTimeWindowCell,
  formatWeekOverWeekExpanded,
  formatSignalBreakdown,
  formatBaselineSignalStatus,
  formatAnomalyEventDisplay,
  SEVERITY_LABELS,
  TIME_WINDOW_LABELS,
  type AnomalyBadgeVariant,
} from "@/lib/anomalyDisplay";
import { formatUpdatedMinutesAgo } from "@/lib/degradationDisplay";
import { getAnomalyEvents, type AnomalyActivity, type AnomalyEventItem } from "@/lib/deviceApi";

const badgeVariantMap: Record<AnomalyBadgeVariant, "success" | "warning" | "info" | "default" | "error"> = {
  success: "success",
  warning: "warning",
  info: "info",
  default: "default",
  error: "error",
};

interface AnomalyActivityCardProps {
  data: AnomalyActivity | null;
  loading: boolean;
  error: string | null;
  staleRefresh?: boolean;
}

function SeverityDot({ severity }: { severity: string }) {
  const colorMap: Record<string, string> = {
    severe: "bg-red-500",
    strong: "bg-amber-500",
    mild: "bg-blue-400",
  };
  return <span className={cn("inline-block h-2 w-2 rounded-full", colorMap[severity] ?? "bg-slate-300")} />;
}

function AnomalyEventTimeline({ deviceId, isEmptyState }: { deviceId: string; isEmptyState: boolean }) {
  const [events, setEvents] = useState<AnomalyEventItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAnomalyEvents(deviceId, { limit: 20, offset: 0 });
      setEvents(result.items);
      setTotal(result.total);
    } catch {
      setError("Failed to load event history");
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  useEffect(() => {
    if (!isEmptyState) {
      loadEvents();
    }
  }, [isEmptyState, loadEvents]);

  if (isEmptyState) {
    return (
      <div className="rounded-lg border border-emerald-100 bg-emerald-50 p-3 text-center">
        <p className="text-sm text-emerald-700">No anomalies detected in the retention period</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-12 animate-pulse rounded bg-slate-100" />
        ))}
      </div>
    );
  }

  if (error) {
    return <p className="text-xs text-red-600">{error}</p>;
  }

  if (events.length === 0) {
    return <p className="text-xs text-slate-500">No anomaly events recorded</p>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <p className="text-xs font-semibold text-slate-700">Event history</p>
        {total > events.length && (
          <p className="text-[10px] text-slate-400">Showing {events.length} of {total}</p>
        )}
      </div>
      <div className="space-y-2 max-h-72 overflow-y-auto">
        {events.map((event, i) => {
          const d = formatAnomalyEventDisplay(event);
          return (
            <div key={i} className="rounded-lg border border-slate-200 p-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <SeverityDot severity={event.severity} />
                  <span className={cn("text-xs font-medium", d.severityTone)}>{d.signalLabel}</span>
                  <span className="text-[10px] text-slate-400">· {d.severityLabel}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  {d.ongoing && (
                    <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-red-600">
                      <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                      Ongoing
                    </span>
                  )}
                  <span className="text-[10px] text-slate-400">{d.timeAgo}</span>
                </div>
              </div>
              <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
                <p>{d.anomalyTypeLabel}</p>
                {d.observedVsBaseline && <p>{d.observedVsBaseline}</p>}
                {d.zScoreLabel && <p>{d.zScoreLabel}</p>}
                {d.durationLabel && <p>Duration: {d.durationLabel}</p>}
                {d.contextTags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {d.contextTags.map((tag) => (
                      <span key={tag} className="inline-block rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-600">{tag}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AnomalyActivityCard({ data, loading, error, staleRefresh }: AnomalyActivityCardProps) {
  const [expanded, setExpanded] = useState(false);

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Anomaly Activity</p>
        <p className="mt-2 text-sm text-slate-500">Unavailable</p>
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Anomaly Activity</p>
        <div className="mt-3 h-8 w-32 animate-pulse rounded bg-slate-200" />
        <div className="mt-2 h-3 w-24 animate-pulse rounded bg-slate-100" />
      </div>
    );
  }

  const display = deriveAnomalyDisplay(data?.state);
  const severityTone = deriveSeverityTone(data?.today_counts ?? null);
  const wowLabel = deriveWeekOverWeekLabel(data?.week_over_week_change);
  const lastAnomalySummary = formatLastAnomalySummary(data?.last_anomaly ?? null);
  const lastAnomalyTimeAgo = formatAnomalyTimeAgo(data?.last_anomaly?.occurred_at ?? null);
  const baselineContext = formatBaselineContext(data?.baseline_status, data?.baseline_field_count);

  const todayTotal = data?.today_counts?.total ?? null;
  const isEmptyState = display.showCounts && todayTotal != null && todayTotal === 0 &&
    (data?.this_week_counts?.total ?? 0) === 0 && (data?.this_month_counts?.total ?? 0) === 0;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-[0.14em] text-slate-500 font-semibold">Anomaly Activity</p>
        <div className="flex items-center gap-1.5">
          {display.stateLabel && <Badge variant={badgeVariantMap[display.stateBadgeVariant]}>{display.stateLabel}</Badge>}
          {display.staleNote && <Badge variant="warning">Stale</Badge>}
        </div>
      </div>

      {display.showCounts && data?.today_counts != null ? (
        <div className="mt-2">
          <p className={cn("text-4xl font-extrabold leading-none", severityTone.countTone)}>
            {todayTotal === 0 ? "No anomalies today" : `${todayTotal} anomal${todayTotal === 1 ? "y" : "ies"} today`}
          </p>
          {baselineContext && (
            <p className="mt-1 text-xs text-slate-500">{baselineContext}</p>
          )}
        </div>
      ) : (
        <div className="mt-2">
          <p className="text-2xl font-bold text-slate-400">—</p>
          {data?.state === "learning" && (
            <p className="mt-1 text-sm text-blue-600">{baselineContext || "Building baseline — this may take several days"}</p>
          )}
          {data?.state === "unavailable" && (
            <p className="mt-1 text-sm text-slate-500">No anomaly data available yet</p>
          )}
        </div>
      )}

      {display.showCounts && !isEmptyState && lastAnomalySummary && (
        <div className="mt-2 text-sm text-slate-600">
          <span className="text-slate-500">Last: </span>
          <span className={SEVERITY_LABELS[data?.last_anomaly?.severity ?? ""]?.tone ?? "text-slate-600"}>
            {lastAnomalySummary}
          </span>
          {lastAnomalyTimeAgo && <span className="text-slate-400"> — {lastAnomalyTimeAgo}</span>}
        </div>
      )}

      {display.showCounts && isEmptyState && (
        <p className="mt-2 text-sm text-emerald-600">No anomalies detected</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">
        {display.showCounts && data?.week_over_week_change != null && wowLabel && (
          <span className={formatWeekOverWeekTone(data.week_over_week_change)}>
            {wowLabel}
          </span>
        )}
        {display.staleNote && (
          <span className="text-amber-600">{display.staleNote}</span>
        )}
        {staleRefresh && data && !display.staleNote && (
          <span className="text-amber-500">Refresh stalled</span>
        )}
        {data?.updated_minutes_ago != null && !display.staleNote && !staleRefresh && (
          <span className="text-slate-400">{formatUpdatedMinutesAgo(data.updated_minutes_ago)}</span>
        )}
      </div>

      {display.showDetails && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors"
          >
            {expanded ? "Hide details" : "View details"}
          </button>

          {expanded && (
            <div className="mt-3 border-t border-slate-100 pt-4 space-y-4">
              {!isEmptyState && (
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: TIME_WINDOW_LABELS.today, counts: data?.today_counts ?? null },
                    { label: TIME_WINDOW_LABELS.week, counts: data?.this_week_counts ?? null },
                    { label: TIME_WINDOW_LABELS.month, counts: data?.this_month_counts ?? null },
                  ].map(({ label, counts }) => {
                    const cell = formatTimeWindowCell(counts);
                    return (
                      <div key={label} className="rounded-lg border border-slate-200 p-3">
                        <p className="text-xs text-slate-500 font-medium">{label}</p>
                        <p className={cn("text-lg font-bold mt-1", counts && counts.total > 0 ? severityTone.countTone : "text-emerald-600")}>
                          {cell.total}
                        </p>
                        {counts && counts.total > 0 && (
                          <p className="text-[11px] text-slate-500 mt-0.5">{cell.breakdown}</p>
                        )}
                        {cell.supplyNote && (
                          <p className="text-[11px] text-amber-700 mt-0.5">{cell.supplyNote} (not machine-driven)</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {data?.signal_breakdown && data.signal_breakdown.length > 0 && !isEmptyState && (
                <div>
                  <p className="text-xs font-semibold text-slate-700 mb-1.5">Breakdown by signal</p>
                  <div className="space-y-1">
                    {formatSignalBreakdown(data.signal_breakdown).map((s) => (
                      <div key={s.label} className="flex items-center justify-between text-sm">
                        <span className="text-slate-700">{s.label}</span>
                        <span className="text-slate-500">
                          <span className="font-medium">{s.count}</span>
                          {s.detail && <span className="ml-1 text-xs text-slate-400">({s.detail})</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {data?.week_over_week_change != null && !isEmptyState && (
                <p className={cn("text-xs", formatWeekOverWeekTone(data.week_over_week_change))}>
                  {formatWeekOverWeekExpanded(data.week_over_week_change)}
                </p>
              )}

              {data?.last_anomaly && !isEmptyState && (
                <div className="rounded-lg border border-slate-200 p-3">
                  <p className="text-xs font-semibold text-slate-700 mb-1">Last anomaly</p>
                  {(() => {
                    const ld = formatAnomalyEventDisplay(data.last_anomaly);
                    return (
                      <>
                        <div className="flex items-center gap-2">
                          <SeverityDot severity={data.last_anomaly.severity} />
                          <span className={cn("text-sm font-medium", ld.severityTone)}>
                            {ld.signalLabel}
                          </span>
                          <span className="text-xs text-slate-400">—</span>
                          <span className="text-sm text-slate-600">{ld.severityLabel}</span>
                          {ld.ongoing && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-red-600">
                              <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                              Ongoing
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-500 mt-1 space-y-0.5">
                          <p>{ld.anomalyTypeLabel}</p>
                          {ld.observedVsBaseline && <p>{ld.observedVsBaseline}</p>}
                          {ld.zScoreLabel && <p>{ld.zScoreLabel}</p>}
                          {ld.durationLabel && <p>Duration: {ld.durationLabel}</p>}
                          {data.last_anomaly.confidence != null && (
                            <p>Confidence: {Math.round(data.last_anomaly.confidence * 100)}%</p>
                          )}
                          {ld.contextTags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-0.5">
                              {ld.contextTags.map((tag) => (
                                <span key={tag} className="inline-block rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-600">{tag}</span>
                              ))}
                            </div>
                          )}
                          <p>{ld.timeAgo}</p>
                        </div>
                      </>
                    );
                  })()}
                </div>
              )}

              <AnomalyEventTimeline deviceId={data?.device_id ?? ""} isEmptyState={isEmptyState} />

              {data?.baseline_signals && data.baseline_signals.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-slate-700 mb-1.5">Monitoring status</p>
                  <div className="space-y-1">
                    {formatBaselineSignalStatus(data.baseline_signals).map((s) => (
                      <div key={s.label} className="flex items-center justify-between text-sm">
                        <span className="text-slate-700">{s.label}</span>
                        <span className={cn("text-xs", s.status === "Active" ? "text-emerald-600" : "text-blue-600")}>
                          {s.status}
                          {s.qualityLabel && <span className="ml-1 text-slate-400">· {s.qualityLabel}</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {baselineContext && !data?.baseline_signals?.length && (
                <p className="text-xs text-slate-500">{baselineContext}</p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
