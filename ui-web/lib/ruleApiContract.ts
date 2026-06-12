export type RuleType = "threshold" | "time_based" | "continuous_idle_duration";
export type RuleScope = "all_devices" | "selected_devices";
export type CooldownMode = "interval" | "no_repeat";
export type CooldownUnit = "minutes" | "seconds";

export interface RuleNotificationRecipient {
  channel: string;
  value: string;
}

export interface CreateRulePayload {
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
  durationMinutes?: number;
  notificationChannels: string[];
  notificationRecipients?: RuleNotificationRecipient[];
  cooldownMinutes?: number;
  cooldownSeconds?: number;
  cooldownUnit?: CooldownUnit;
  cooldownMode?: CooldownMode;
  deviceIds: string[];
}

export interface RawRuleLike {
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
  duration_minutes?: number | null;
  notification_channels: string[];
  notification_recipients?: RuleNotificationRecipient[];
  cooldown_minutes: number;
  cooldown_seconds?: number;
  cooldown_unit?: CooldownUnit;
  cooldown_mode?: CooldownMode;
  triggered_once?: boolean;
  device_ids: string[];
  status: string;
  created_at: string;
  updated_at?: string | null;
  last_triggered_at?: string | null;
}

export interface RuleLike {
  ruleId: string;
  ruleName: string;
  description?: string | null;
  ruleType: RuleType;
  scope: RuleScope;
  property?: string | null;
  condition?: string | null;
  threshold?: number | null;
  timeWindowStart?: string | null;
  timeWindowEnd?: string | null;
  timezone?: string | null;
  timeCondition?: string | null;
  durationMinutes?: number | null;
  notificationChannels: string[];
  notificationRecipients: RuleNotificationRecipient[];
  cooldownMinutes: number;
  cooldownSeconds: number;
  cooldownUnit: CooldownUnit;
  cooldownMode: CooldownMode;
  triggeredOnce: boolean;
  deviceIds: string[];
  status: string;
  createdAt: string;
  updatedAt?: string | null;
  lastTriggeredAt?: string | null;
}

function normalizeCooldownFromRaw(r: RawRuleLike) {
  const cooldownMode = r.cooldown_mode ?? "interval";
  const cooldownSeconds =
    r.cooldown_seconds ?? (cooldownMode === "no_repeat" ? 0 : (r.cooldown_minutes ?? 15) * 60);
  const cooldownUnit =
    r.cooldown_unit ?? (cooldownMode === "no_repeat" ? "minutes" : (r.cooldown_seconds != null ? "seconds" : "minutes"));
  const cooldownMinutes =
    r.cooldown_minutes ??
    (cooldownMode === "no_repeat"
      ? 0
      : cooldownUnit === "seconds"
        ? (cooldownSeconds === 0 ? 0 : Math.max(1, Math.ceil(cooldownSeconds / 60)))
        : Math.max(0, Math.floor(cooldownSeconds / 60)));

  return { cooldownMode, cooldownUnit, cooldownSeconds, cooldownMinutes };
}

export function mapRawRule(r: RawRuleLike): RuleLike {
  return {
    ruleId: r.rule_id,
    ruleName: r.rule_name,
    description: r.description,
    ruleType: r.rule_type ?? "threshold",
    scope: r.scope,
    property: r.property,
    condition: r.condition,
    threshold: r.threshold,
    timeWindowStart: r.time_window_start,
    timeWindowEnd: r.time_window_end,
    timezone: r.timezone ?? "Asia/Kolkata",
    timeCondition: r.time_condition,
    durationMinutes: r.duration_minutes ?? null,
    notificationChannels: r.notification_channels,
    notificationRecipients: r.notification_recipients ?? [],
    ...normalizeCooldownFromRaw(r),
    triggeredOnce: Boolean(r.triggered_once),
    deviceIds: r.device_ids,
    status: r.status,
    createdAt: r.created_at,
    updatedAt: r.updated_at ?? null,
    lastTriggeredAt: r.last_triggered_at ?? null,
  };
}

export function buildCreateRuleRequestBody(payload: CreateRulePayload): Record<string, unknown> {
  const cooldownMode = payload.cooldownMode ?? "interval";
  const cooldownUnit = payload.cooldownUnit ?? "minutes";
  const cooldownMinutes =
    cooldownMode === "no_repeat"
      ? 0
      : cooldownUnit === "seconds"
        ? Math.max(
            0,
            payload.cooldownSeconds != null
              ? Math.max(1, Math.ceil(payload.cooldownSeconds / 60))
              : payload.cooldownMinutes ?? 15,
          )
        : Math.max(0, payload.cooldownMinutes ?? (payload.cooldownSeconds != null ? Math.max(1, Math.ceil(payload.cooldownSeconds / 60)) : 15));
  const cooldownSeconds =
    cooldownMode === "no_repeat"
      ? 0
      : cooldownUnit === "seconds"
        ? Math.max(0, payload.cooldownSeconds ?? ((payload.cooldownMinutes ?? 0) * 60))
        : cooldownMinutes * 60;

  const body: Record<string, unknown> = {
    rule_name: payload.ruleName,
    description: payload.description,
    rule_type: payload.ruleType ?? "threshold",
    scope: payload.scope,
    timezone: payload.timezone ?? "Asia/Kolkata",
    notification_channels: payload.notificationChannels,
    notification_recipients: payload.notificationRecipients ?? [],
    cooldown_minutes: cooldownMinutes,
    cooldown_seconds: cooldownSeconds,
    cooldown_unit: cooldownUnit,
    cooldown_mode: cooldownMode,
    device_ids: payload.deviceIds,
  };

  if (payload.ruleType === "threshold" || payload.ruleType === undefined) {
    body.property = payload.property;
    body.condition = payload.condition;
    body.threshold = payload.threshold;
  }

  if (payload.ruleType === "time_based") {
    body.time_window_start = payload.timeWindowStart;
    body.time_window_end = payload.timeWindowEnd;
    body.time_condition = payload.timeCondition;
  }

  if (payload.ruleType === "continuous_idle_duration") {
    body.duration_minutes = payload.durationMinutes;
  }

  return body;
}
