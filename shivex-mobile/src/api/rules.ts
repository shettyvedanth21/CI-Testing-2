import { buildServiceUrl, readJson } from "./base";

export type RuleStatus = "active" | "paused" | "archived";
export type RuleScope = "all_devices" | "selected_devices";
export type RuleType = "threshold" | "time_based";
export type CooldownMode = "interval" | "no_repeat";

export type RuleRecord = {
  id: string;
  name: string;
  description: string | null;
  ruleType: RuleType;
  scope: RuleScope;
  property: string | null;
  condition: string | null;
  threshold: number | null;
  timeWindowStart: string | null;
  timeWindowEnd: string | null;
  timezone: string | null;
  timeCondition: string | null;
  notificationChannels: string[];
  cooldownMinutes: number;
  cooldownMode: CooldownMode;
  deviceIds: string[];
  status: RuleStatus;
  createdAt: string;
  updatedAt: string | null;
};

export type CreateRulePayload = {
  ruleName: string;
  description?: string;
  ruleType?: RuleType;
  scope: RuleScope;
  property?: string;
  condition?: string;
  threshold?: number;
  timeWindowStart?: string;
  timeWindowEnd?: string;
  timezone?: string;
  timeCondition?: string;
  notificationChannels: string[];
  cooldownMinutes?: number;
  cooldownMode?: CooldownMode;
  deviceIds: string[];
};

type RawRule = {
  rule_id: string;
  rule_name: string;
  description?: string | null;
  rule_type?: RuleType;
  scope: RuleScope;
  property?: string | null;
  condition?: string | null;
  threshold?: number | null;
  time_window_start?: string | null;
  time_window_end?: string | null;
  timezone?: string | null;
  time_condition?: string | null;
  notification_channels: string[];
  cooldown_minutes: number;
  cooldown_mode?: CooldownMode;
  device_ids: string[];
  status: RuleStatus;
  created_at: string;
  updated_at?: string | null;
};

type RuleListResponse = {
  data?: RawRule[];
  total?: number;
};

type RuleSingleResponse = {
  data?: RawRule;
};

const rulesBaseUrl = buildServiceUrl(8002, "/api/v1/rules");
const dataBaseUrl = buildServiceUrl(8081, "/api/v1/data");

function mapRule(rule: RawRule): RuleRecord {
  return {
    id: rule.rule_id,
    name: rule.rule_name,
    description: rule.description ?? null,
    ruleType: rule.rule_type ?? "threshold",
    scope: rule.scope,
    property: rule.property ?? null,
    condition: rule.condition ?? null,
    threshold: typeof rule.threshold === "number" ? rule.threshold : null,
    timeWindowStart: rule.time_window_start ?? null,
    timeWindowEnd: rule.time_window_end ?? null,
    timezone: rule.timezone ?? "Asia/Kolkata",
    timeCondition: rule.time_condition ?? null,
    notificationChannels: rule.notification_channels ?? [],
    cooldownMinutes: rule.cooldown_minutes ?? 15,
    cooldownMode: rule.cooldown_mode ?? "interval",
    deviceIds: rule.device_ids ?? [],
    status: rule.status,
    createdAt: rule.created_at,
    updatedAt: rule.updated_at ?? null,
  };
}

export async function getRules(params?: {
  deviceId?: string;
  status?: RuleStatus;
  page?: number;
  pageSize?: number;
}): Promise<{ data: RuleRecord[]; total: number } | null> {
  const query = new URLSearchParams({
    page: String(params?.page ?? 1),
    page_size: String(params?.pageSize ?? 50),
  });

  if (params?.deviceId) {
    query.set("device_id", params.deviceId);
  }

  if (params?.status) {
    query.set("status", params.status);
  }

  const payload = await readJson<RuleListResponse>(`${rulesBaseUrl}?${query.toString()}`);
  if (!payload) {
    return null;
  }

  return {
    data: (payload.data ?? []).map(mapRule),
    total: payload.total ?? 0,
  };
}

export async function getRule(ruleId: string): Promise<RuleRecord | null> {
  const payload = await readJson<RuleSingleResponse>(`${rulesBaseUrl}/${ruleId}`);
  return payload?.data ? mapRule(payload.data) : null;
}

export async function createRule(payload: CreateRulePayload): Promise<RuleRecord | null> {
  const response = await readJson<RuleSingleResponse>(rulesBaseUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      rule_name: payload.ruleName,
      description: payload.description,
      rule_type: payload.ruleType ?? "threshold",
      scope: payload.scope,
      property: payload.property,
      condition: payload.condition,
      threshold: payload.threshold,
      time_window_start: payload.timeWindowStart,
      time_window_end: payload.timeWindowEnd,
      timezone: payload.timezone ?? "Asia/Kolkata",
      time_condition: payload.timeCondition,
      notification_channels: payload.notificationChannels,
      cooldown_minutes: payload.cooldownMinutes ?? 15,
      cooldown_mode: payload.cooldownMode ?? "interval",
      device_ids: payload.deviceIds,
    }),
  });

  return response?.data ? mapRule(response.data) : null;
}

export async function toggleRule(ruleId: string, status: RuleStatus): Promise<boolean> {
  const payload = await readJson<{ success?: boolean }>(`${rulesBaseUrl}/${ruleId}/status`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ status }),
  });

  return payload !== null;
}

export async function getDeviceFields(deviceId: string): Promise<string[]> {
  const payload = await readJson<{ data?: { items?: Array<Record<string, unknown>> } }>(
    `${dataBaseUrl}/telemetry/${deviceId}?limit=1`
  );

  const item = payload?.data?.items?.[0];
  if (!item) {
    return [];
  }

  return Object.entries(item)
    .filter(([key, value]) => {
      return (
        key !== "timestamp" &&
        key !== "device_id" &&
        key !== "schema_version" &&
        key !== "enrichment_status" &&
        typeof value === "number"
      );
    })
    .map(([key]) => key);
}
