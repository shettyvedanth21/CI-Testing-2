import { RULE_ENGINE_SERVICE_BASE } from "./api";
import { apiFetch } from "./apiFetch";
import { readResponseError } from "./responseError";
import {
  buildCreateRuleRequestBody,
  mapRawRule as mapRawRuleContract,
  type CooldownMode,
  type CooldownUnit,
  type CreateRulePayload as CreateRulePayloadContract,
  type RawRuleLike,
  type RuleLike,
  type RuleNotificationRecipient as RuleNotificationRecipientContract,
  type RuleScope as RuleScopeContract,
  type RuleType as RuleTypeContract,
} from "./ruleApiContract";

export { buildCreateRuleRequestBody } from "./ruleApiContract";

/* ---------- types ---------- */

export type RuleStatus = "active" | "paused" | "archived";
export type RuleScope = RuleScopeContract;
export type RuleType = RuleTypeContract;
export type RuleNotificationRecipient = RuleNotificationRecipientContract;
export type CreateRulePayload = CreateRulePayloadContract;

export interface Rule extends RuleLike {
  status: RuleStatus;
}

interface RawRule extends RawRuleLike {
  status: RuleStatus;
}

async function readRuleApiError(res: Response): Promise<string> {
  return readResponseError(res);
}

export function mapRawRule(r: RawRule): Rule {
  return mapRawRuleContract(r) as Rule;
}

/* ---------- list ---------- */

export async function listRules(params?: {
  deviceId?: string;
  status?: RuleStatus;
  page?: number;
  pageSize?: number;
}) {
  const query = new URLSearchParams();

  if (params?.deviceId) query.append("device_id", params.deviceId);
  if (params?.status) query.append("status", params.status);

  query.append("page", String(params?.page ?? 1));
  query.append("page_size", String(params?.pageSize ?? 20));

  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules?${query.toString()}`
  );

  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  const json = await res.json();
  const rows: RawRule[] = Array.isArray(json.data) ? json.data : [];

  return {
    data: rows.map((r) => mapRawRule(r as RawRule)),
    total: json.total,
  };
}

export async function getRule(ruleId: string): Promise<Rule> {
  const res = await apiFetch(`${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}`);
  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  const json = await res.json();
  const r: RawRule = json.data;

  return mapRawRule(r as RawRule);
}

/* ---------- create ---------- */

export async function createRule(payload: CreateRulePayload) {
  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildCreateRuleRequestBody(payload)),
    }
  );

  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  const json = await res.json();
  return json.data;
}

export async function updateRule(
  ruleId: string,
  payload: {
    ruleName?: string;
    description?: string;
    ruleType?: RuleType;
    scope?: RuleScope;
    property?: string;
    condition?: string;
    threshold?: number;
    timeWindowStart?: string;
    timeWindowEnd?: string;
    timezone?: string;
    timeCondition?: string;
    durationMinutes?: number;
    notificationChannels?: string[];
    notificationRecipients?: RuleNotificationRecipient[];
    cooldownMinutes?: number;
    cooldownSeconds?: number;
    cooldownUnit?: CooldownUnit;
    cooldownMode?: CooldownMode;
    deviceIds?: string[];
  }
) {
  const body: Record<string, unknown> = {};
  if (payload.ruleName !== undefined) body.rule_name = payload.ruleName;
  if (payload.description !== undefined) body.description = payload.description;
  if (payload.ruleType !== undefined) body.rule_type = payload.ruleType;
  if (payload.scope !== undefined) body.scope = payload.scope;
  if (payload.property !== undefined) body.property = payload.property;
  if (payload.condition !== undefined) body.condition = payload.condition;
  if (payload.threshold !== undefined) body.threshold = payload.threshold;
  if (payload.timeWindowStart !== undefined) body.time_window_start = payload.timeWindowStart;
  if (payload.timeWindowEnd !== undefined) body.time_window_end = payload.timeWindowEnd;
  if (payload.timezone !== undefined) body.timezone = payload.timezone;
  if (payload.timeCondition !== undefined) body.time_condition = payload.timeCondition;
  if (payload.durationMinutes !== undefined) body.duration_minutes = payload.durationMinutes;
  if (payload.notificationChannels !== undefined) body.notification_channels = payload.notificationChannels;
  if (payload.notificationRecipients !== undefined) body.notification_recipients = payload.notificationRecipients;
  if (payload.cooldownMinutes !== undefined) body.cooldown_minutes = payload.cooldownMinutes;
  if (payload.cooldownSeconds !== undefined) body.cooldown_seconds = payload.cooldownSeconds;
  if (payload.cooldownUnit !== undefined) body.cooldown_unit = payload.cooldownUnit;
  if (payload.cooldownMode !== undefined) body.cooldown_mode = payload.cooldownMode;
  if (payload.deviceIds !== undefined) body.device_ids = payload.deviceIds;

  const res = await apiFetch(`${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  const json = await res.json();
  return json.data;
}

/* ---------- pause / resume ---------- */

export async function updateRuleStatus(
  ruleId: string,
  status: RuleStatus
) {
  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }
  );

  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  return res.json();
}

/* ---------- delete ---------- */

export async function deleteRule(ruleId: string) {
  const res = await apiFetch(
    `${RULE_ENGINE_SERVICE_BASE}/api/v1/rules/${ruleId}`,
    { method: "DELETE" }
  );

  if (!res.ok) {
    throw new Error(await readRuleApiError(res));
  }

  return res.json();
}
