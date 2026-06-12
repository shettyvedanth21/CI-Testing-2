import type {
  PerformanceTrendData,
  PerformanceTrendMetric,
} from "./deviceApi.ts";

export interface PerformanceTrendChartPoint {
  timestamp: string;
  value: number;
  actualTimestamp: string;
  stale: boolean;
}

export interface PerformanceTrendDisplayModel {
  chartData: PerformanceTrendChartPoint[];
  staleChartData: PerformanceTrendChartPoint[];
  hasMeasuredData: boolean;
  hasFallbackOnly: boolean;
  empty: boolean;
  message: string;
  staleLabel: string | null;
}

export function buildPerformanceTrendDisplayModel(
  trendData: PerformanceTrendData | null,
  metric: PerformanceTrendMetric,
): PerformanceTrendDisplayModel {
  const metricKey = metric === "health" ? "health_score" : "uptime_percentage";
  const measured = (trendData?.points ?? [])
    .map((point) => ({
      timestamp: point.timestamp,
      value: point[metricKey],
      actualTimestamp: point.timestamp,
      stale: false,
    }))
    .filter((point): point is PerformanceTrendChartPoint => typeof point.value === "number");

  const message =
    trendData?.metric_message ||
    trendData?.message ||
    `No ${metric} trend data available.`;

  if (measured.length > 0) {
    return {
      chartData: measured,
      staleChartData: [],
      hasMeasuredData: true,
      hasFallbackOnly: false,
      empty: false,
      message,
      staleLabel: null,
    };
  }

  const fallback = trendData?.fallback_point;
  if (fallback && trendData?.range_start && trendData?.range_end) {
    return {
      chartData: [],
      staleChartData: [
        {
          timestamp: trendData.range_start,
          value: fallback.value,
          actualTimestamp: fallback.timestamp,
          stale: true,
        },
        {
          timestamp: trendData.range_end,
          value: fallback.value,
          actualTimestamp: fallback.timestamp,
          stale: true,
        },
      ],
      hasMeasuredData: false,
      hasFallbackOnly: true,
      empty: false,
      message,
      staleLabel: trendData.last_actual_timestamp
        ? `Last actual point at ${new Date(trendData.last_actual_timestamp).toLocaleString()}`
        : "Showing last known value",
    };
  }

  return {
    chartData: [],
    staleChartData: [],
    hasMeasuredData: false,
    hasFallbackOnly: false,
    empty: true,
    message,
    staleLabel: null,
  };
}
