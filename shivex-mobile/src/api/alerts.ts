import { mobileFetch } from "./authApi";
import { API_CONFIG } from "../constants/api";

export type AlertSeverity = "HIGH" | "MEDIUM" | "LOW";

export type AlertRecord = {
  id: string;
  deviceId: string;
  machineName: string;
  status: string;
  severity: AlertSeverity;
  ruleName: string;
  triggeredValue: number | null;
  triggeredAt: string | null;
  acknowledgedBy: string | null;
  acknowledgedAt: string | null;
  title: string;
  message: string;
};

type AlertApiRecord = {
  alert_id?: string;
  device_id?: string;
  status?: string;
  severity?: string;
  rule_name?: string;
  rule_id?: string;
  metric_name?: string;
  observed_value?: number;
  threshold_value?: number;
  created_at?: string;
  acknowledged_by?: string;
  acknowledged_at?: string;
  title?: string;
  message?: string;
};

type AlertEnvelope = {
  success?: boolean;
  data?: AlertApiRecord[];
};

type AlertSingleEnvelope = {
  success?: boolean;
  data?: AlertApiRecord;
};

const ruleEngineBase = `${API_CONFIG.RULE_ENGINE_SERVICE}/api/v1/alerts`;

function normalizeSeverity(value?: string | null): AlertSeverity {
  const normalized = (value ?? "").toUpperCase();

  if (normalized === "HIGH") {
    return "HIGH";
  }

  if (normalized === "LOW") {
    return "LOW";
  }

  return "MEDIUM";
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatRuleName(alert: AlertApiRecord) {
  return alert.rule_name ?? alert.metric_name ?? alert.rule_id ?? "Alert rule";
}

function normalizeAlert(record?: AlertApiRecord | null): AlertRecord {
  const triggeredValue = asNumber(record?.observed_value) ?? asNumber(record?.threshold_value);

  return {
    id: record?.alert_id ?? "unknown-alert",
    deviceId: record?.device_id ?? "unknown-device",
    machineName: record?.device_id ?? "Unknown machine",
    status: record?.status ?? "active",
    severity: normalizeSeverity(record?.severity),
    ruleName: formatRuleName(record ?? {}),
    triggeredValue,
    triggeredAt: record?.created_at ?? null,
    acknowledgedBy: record?.acknowledged_by ?? null,
    acknowledgedAt: record?.acknowledged_at ?? null,
    title: record?.title ?? formatRuleName(record ?? {}),
    message: record?.message ?? "Alert triggered",
  };
}

async function readJson<T>(input: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await mobileFetch(input, init);

    if (!response.ok) {
      console.error("[shivex api]", input, response.status);
      return null;
    }

    return (await response.json()) as T;
  } catch (error) {
    console.error("[shivex api]", error);
    return null;
  }
}

export async function getAlerts(params?: {
  device_id?: string;
  status?: string;
}): Promise<AlertRecord[] | null> {
  const query = new URLSearchParams({ page: "1", page_size: "100" });

  if (params?.device_id) {
    query.set("device_id", params.device_id);
  }

  if (params?.status) {
    query.set("status", params.status);
  }

  const payload = await readJson<AlertEnvelope>(`${ruleEngineBase}?${query.toString()}`);
  return payload?.data?.map((item) => normalizeAlert(item)) ?? [];
}

export async function getAlert(alertId: string): Promise<AlertRecord | null> {
  const alerts = await getAlerts();
  return alerts?.find((item) => item.id === alertId) ?? null;
}

export async function acknowledgeAlert(
  alertId: string,
  acknowledgedBy: string,
  note?: string
): Promise<AlertRecord | null> {
  if (note) {
    console.info("[shivex api] acknowledge note kept locally", note);
  }

  const payload = await readJson<AlertSingleEnvelope>(`${ruleEngineBase}/${alertId}/acknowledge`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ acknowledged_by: acknowledgedBy }),
  });

  return payload?.data ? normalizeAlert(payload.data) : null;
}
