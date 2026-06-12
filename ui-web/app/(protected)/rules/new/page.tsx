"use client";

import { useState, Suspense, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";

import { createRule, updateRuleStatus } from "@/lib/ruleApi";
import { COOLDOWN_MINUTE_PRESETS } from "@/lib/ruleCooldown";
import { RULE_TYPE_OPTIONS, getRuleTypeHelperText } from "@/lib/rulePresentation";
import { getDeviceFields } from "@/lib/dataApi";
import { ApiError } from "@/components/ApiError";
import { useAuth } from "@/lib/authContext";
import {
  dedupeRuleRecipientEmails,
  dedupeRuleRecipientPhones,
  isValidRuleRecipientEmail,
  isValidRuleRecipientPhone,
  normalizeRuleRecipientEmail,
  normalizeRuleRecipientPhoneInput,
  normalizeRuleRecipientPhone,
} from "@/lib/ruleRecipients";
import {
  buildUnavailableSelectedChannelMessage,
  getRuleNotificationChannelStates,
} from "@/lib/ruleNotificationChannels";

const conditions = [
  { value: ">", label: "Greater than (>)" },
  { value: "<", label: "Less than (<)" },
  { value: "=", label: "Equal to (=)" },
  { value: "!=", label: "Not equal (!=)" },
  { value: ">=", label: "Greater or equal (>=)" },
  { value: "<=", label: "Less or equal (<=)" },
];

const cooldownTypeOptions = [
  { value: "minutes", label: "Minutes" },
  { value: "no_repeat", label: "No repeat" },
];

const MINUTE_PRESET_VALUES = new Set(COOLDOWN_MINUTE_PRESETS.map((option) => option.value));

const METRIC_LABELS: Record<string, string> = {
  power: "Power", voltage: "Voltage", current: "Current", temperature: "Temperature",
  pressure: "Pressure", humidity: "Humidity", vibration: "Vibration", frequency: "Frequency",
  power_factor: "Power Factor", speed: "Speed", torque: "Torque", oil_pressure: "Oil Pressure",
};

function formatFieldLabel(field: unknown): string {
  if (typeof field !== "string") return "Unknown";
  const normalized = field.trim();
  if (!normalized) return "Unknown";
  if (METRIC_LABELS[normalized]) return METRIC_LABELS[normalized];
  return normalized
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function CreateRuleContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { me } = useAuth();
  const deviceIdFromUrl = searchParams.get("device_id") ?? "D1";

  const [ruleName, setRuleName] = useState("");
  const [ruleType, setRuleType] = useState<"threshold" | "time_based" | "continuous_idle_duration">("threshold");
  const [property, setProperty] = useState("");
  const [condition, setCondition] = useState(">");
  const [threshold, setThreshold] = useState("");
  const [timeWindowStart, setTimeWindowStart] = useState("20:00");
  const [timeWindowEnd, setTimeWindowEnd] = useState("06:00");
  const [durationMinutes, setDurationMinutes] = useState("40");
  const [cooldownType, setCooldownType] = useState<"minutes" | "no_repeat">("minutes");
  const [cooldownValue, setCooldownValue] = useState("15");
  const [notificationChannels, setNotificationChannels] = useState<string[]>([]);
  const [emailRecipients, setEmailRecipients] = useState<string[]>([]);
  const [emailRecipientInput, setEmailRecipientInput] = useState("");
  const [phoneRecipients, setPhoneRecipients] = useState<string[]>([]);
  const [phoneRecipientInput, setPhoneRecipientInput] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [availableProperties, setAvailableProperties] = useState<{value: string, label: string}[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [propertiesLoading, setPropertiesLoading] = useState(true);
  const notificationChannelStates = getRuleNotificationChannelStates(me, notificationChannels);
  const unavailableSelectedChannelMessage = buildUnavailableSelectedChannelMessage(notificationChannelStates);
  const hasPhoneChannelSelected = notificationChannelStates.some(
    (state) => state.checked && (state.channel === "sms" || state.channel === "whatsapp"),
  );

  const handleCooldownTypeChange = (nextType: "minutes" | "no_repeat") => {
    setCooldownType((prev) => {
      if (nextType === prev) return prev;

      if (nextType === "minutes" && !MINUTE_PRESET_VALUES.has(cooldownValue)) {
        setCooldownValue("15");
      }

      if (nextType === "no_repeat") {
        return "no_repeat";
      }
      return nextType;
    });
  };

  // Fetch available properties from device telemetry
  useEffect(() => {
    async function fetchProperties() {
      try {
        const fields = await getDeviceFields(deviceIdFromUrl);
        const properties = fields
          .filter((field): field is string => typeof field === "string" && field.trim().length > 0)
          .map(field => ({
          value: field,
          label: formatFieldLabel(field)
        }));
        setAvailableProperties(properties);
        if (fields.length > 0 && !property) {
          setProperty(fields[0]);
        }
      } catch (err) {
        console.error("Failed to fetch device fields:", err);
        setAvailableProperties([]);
      } finally {
        setPropertiesLoading(false);
      }
    }
    
    fetchProperties();
  }, [deviceIdFromUrl]);

  // Update property when device changes
  useEffect(() => {
    if (availableProperties.length > 0 && !availableProperties.find(p => p.value === property)) {
      setProperty(availableProperties[0].value);
    }
  }, [availableProperties, property]);

  const handleNotificationChange = (channel: string) => {
    setNotificationChannels((prev) => {
      const next = prev.includes(channel) ? prev.filter((c) => c !== channel) : [...prev, channel];
      if (!next.includes("email")) {
        setEmailRecipients([]);
        setEmailRecipientInput("");
      }
      if (!next.some((value) => value === "sms" || value === "whatsapp")) {
        setPhoneRecipients([]);
        setPhoneRecipientInput("");
      }
      return next;
    });
  };

  const handleAddEmailRecipient = () => {
    const normalized = normalizeRuleRecipientEmail(emailRecipientInput);
    if (!normalized) {
      setError("Email recipient is required");
      return;
    }
    if (!isValidRuleRecipientEmail(normalized)) {
      setError("Enter a valid email recipient");
      return;
    }
    setError(null);
    setEmailRecipients((prev) => dedupeRuleRecipientEmails([...prev, normalized]));
    setEmailRecipientInput("");
  };

  const handleAddPhoneRecipient = () => {
    const normalized = normalizeRuleRecipientPhone(phoneRecipientInput);
    if (!normalized) {
      setError("Phone recipient is required");
      return;
    }
    if (!isValidRuleRecipientPhone(normalized)) {
      setError("Enter a valid phone recipient");
      return;
    }
    setError(null);
    setPhoneRecipients((prev) => dedupeRuleRecipientPhones([...prev, normalized]));
    setPhoneRecipientInput("");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!ruleName.trim()) {
      setError("Rule name is required");
      return;
    }

    if (ruleType === "threshold" && (threshold === "" || isNaN(Number(threshold)))) {
      setError("Threshold must be a valid number");
      return;
    }
    if (ruleType === "continuous_idle_duration") {
      const numericDuration = Number(durationMinutes);
      if (durationMinutes === "" || Number.isNaN(numericDuration) || numericDuration <= 0 || !Number.isInteger(numericDuration)) {
        setError("Duration minutes must be a valid positive whole number");
        return;
      }
    }

    if (notificationChannels.length === 0) {
      setError("At least one notification channel is required");
      return;
    }
    if (unavailableSelectedChannelMessage) {
      setError(unavailableSelectedChannelMessage);
      return;
    }
    if (notificationChannels.includes("email") && emailRecipients.length === 0) {
      setError("Add at least one email recipient when email notifications are enabled");
      return;
    }
    if (hasPhoneChannelSelected && phoneRecipients.length === 0) {
      setError("Add at least one phone recipient when SMS or WhatsApp notifications are enabled");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const created = await createRule({
        ruleName: ruleName.trim(),
        ruleType,
        scope: "selected_devices",
        property: ruleType === "threshold" ? property : undefined,
        condition: ruleType === "threshold" ? condition : undefined,
        threshold: ruleType === "threshold" ? Number(threshold) : undefined,
        timeWindowStart: ruleType === "time_based" ? timeWindowStart : undefined,
        timeWindowEnd: ruleType === "time_based" ? timeWindowEnd : undefined,
        timezone: "Asia/Kolkata",
        timeCondition: ruleType === "time_based" ? "running_in_window" : undefined,
        durationMinutes: ruleType === "continuous_idle_duration" ? Number(durationMinutes) : undefined,
        notificationChannels,
        notificationRecipients: [
          ...(notificationChannels.includes("email") ? emailRecipients.map((value) => ({ channel: "email", value })) : []),
          ...(notificationChannels.includes("sms") ? phoneRecipients.map((value) => ({ channel: "sms", value })) : []),
          ...(notificationChannels.includes("whatsapp") ? phoneRecipients.map((value) => ({ channel: "whatsapp", value })) : []),
        ],
        cooldownMode: cooldownType === "no_repeat" ? "no_repeat" : "interval",
        cooldownUnit: "minutes",
        cooldownMinutes:
          cooldownType === "no_repeat"
            ? 0
            : Number(cooldownValue),
        cooldownSeconds:
          cooldownType === "no_repeat"
            ? 0
            : Number(cooldownValue) * 60,
        deviceIds: [deviceIdFromUrl],
      });

      // 🔴 important fix:
      // backend always creates rule as ACTIVE
      // if user disabled it → immediately pause it
      if (!enabled && created?.rule_id) {
        await updateRuleStatus(created.rule_id, "paused");
      }

      router.push("/rules");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    } finally {
      setLoading(false);
    }
  };

  if (error && !loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Create Rule
          </h2>
        </div>
        <ApiError message={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          Create Rule
        </h2>
      </div>

      <div className="bg-white dark:bg-zinc-900 rounded-lg shadow overflow-hidden">
        <form onSubmit={handleSubmit} className="p-6 space-y-6">
          <div className="space-y-2">
            <label
              htmlFor="ruleName"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Rule Name
            </label>
            <input
              type="text"
              id="ruleName"
              value={ruleName}
              onChange={(e) => setRuleName(e.target.value)}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                         bg-white dark:bg-zinc-900
                         px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Enter rule name"
              disabled={loading}
            />
          </div>

          <div className="space-y-2">
            <label
              htmlFor="ruleType"
              className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Rule Type
            </label>
            <select
              id="ruleType"
              value={ruleType}
              onChange={(e) => setRuleType(e.target.value as "threshold" | "time_based" | "continuous_idle_duration")}
              className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                         bg-white dark:bg-zinc-900
                         px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={loading}
            >
              {[...RULE_TYPE_OPTIONS].map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-zinc-500">{getRuleTypeHelperText(ruleType)}</p>
          </div>

          {ruleType === "threshold" ? (
            <>
              <div className="space-y-2">
                <label
                  htmlFor="property"
                  className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Property
                </label>
                {propertiesLoading ? (
                  <div className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-3 py-2 text-sm text-zinc-500">
                    Loading properties...
                  </div>
                ) : availableProperties.length === 0 ? (
                  <div className="w-full rounded-md border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-3 py-2 text-sm text-red-600">
                    No numeric properties found for this device
                  </div>
                ) : (
                  <select
                    id="property"
                    value={property}
                    onChange={(e) => setProperty(e.target.value)}
                    className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                               bg-white dark:bg-zinc-900
                               px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                               focus:outline-none focus:ring-2 focus:ring-blue-500"
                    disabled={loading}
                  >
                    {availableProperties.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                )}
                <p className="text-xs text-zinc-500">
                  Properties are fetched dynamically from device telemetry
                </p>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label
                  htmlFor="condition"
                  className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Condition
                </label>
                <select
                  id="condition"
                  value={condition}
                  onChange={(e) => setCondition(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                >
                  {conditions.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="threshold"
                  className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Threshold
                </label>
                <input
                  type="number"
                  id="threshold"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Enter threshold"
                  disabled={loading}
                  step="any"
                />
              </div>
              </div>
            </>
          ) : ruleType === "time_based" ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Restricted From (IST)</label>
                <input
                  type="time"
                  value={timeWindowStart}
                  onChange={(e) => setTimeWindowStart(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                />
              </div>
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Restricted To (IST)</label>
                <input
                  type="time"
                  value={timeWindowEnd}
                  onChange={(e) => setTimeWindowEnd(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                />
              </div>
            </div>
          ) : (
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Duration (minutes)</label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={durationMinutes}
                  onChange={(e) => setDurationMinutes(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                />
                <p className="text-xs text-zinc-500">
                  Alert when the machine stays idle continuously for N minutes.
                </p>
              </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Cooldown Type</label>
              <select
                value={cooldownType}
                onChange={(e) => handleCooldownTypeChange(e.target.value as "minutes" | "no_repeat")}
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                disabled={loading}
              >
                {cooldownTypeOptions.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            {cooldownType === "minutes" ? (
              <div className="space-y-2">
                <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">Cooldown Duration</label>
                <select
                  value={cooldownValue}
                  onChange={(e) => setCooldownValue(e.target.value)}
                  className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={loading}
                >
                  {COOLDOWN_MINUTE_PRESETS.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
          </div>

          <div className="space-y-2">
            <span className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Notification Channels
            </span>

            <div className="space-y-3">
              {notificationChannelStates.map((state) => (
                <div
                  key={state.channel}
                  className="rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/50 px-4 py-3"
                >
                  <label
                    className={`flex items-center space-x-3 ${state.disabled ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
                  >
                    <input
                      type="checkbox"
                      checked={state.checked}
                      onChange={() => handleNotificationChange(state.channel)}
                      className="rounded border-zinc-300 dark:border-zinc-700 text-blue-600 focus:ring-blue-500"
                      disabled={loading || state.disabled}
                    />
                    <span className="text-sm text-zinc-700 dark:text-zinc-300">
                      {state.label}
                    </span>
                  </label>
                  <p
                    className={`mt-2 text-sm ${
                      state.legacyUnavailable
                        ? "text-amber-700"
                        : state.available
                          ? "text-zinc-500 dark:text-zinc-400"
                          : "text-zinc-600 dark:text-zinc-300"
                    }`}
                  >
                    {state.helperText}
                  </p>
                </div>
              ))}
            </div>
            {notificationChannels.includes("email") ? (
              <div className="space-y-3 rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/50 p-4">
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={emailRecipientInput}
                    onChange={(e) => setEmailRecipientInput(e.target.value)}
                    className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="alerts@planta.com"
                    disabled={loading}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddEmailRecipient();
                      }
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleAddEmailRecipient}
                    className="px-3 py-2 rounded-md border border-zinc-300 dark:border-zinc-700 text-sm"
                    disabled={loading}
                  >
                    Add Email
                  </button>
                </div>
                {emailRecipients.length === 0 ? (
                  <p className="text-xs text-amber-700">Add the recipients who should receive alerts for this rule.</p>
                ) : (
                  <div className="rounded-md border border-zinc-200 dark:border-zinc-700 divide-y divide-zinc-200 dark:divide-zinc-700">
                    {emailRecipients.map((email) => (
                      <div key={email} className="flex items-center justify-between px-3 py-2 text-sm">
                        <span>{email}</span>
                        <button
                          type="button"
                          onClick={() => setEmailRecipients((prev) => prev.filter((value) => value !== email))}
                          className="text-red-600"
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
                {hasPhoneChannelSelected ? (
                  <div className="space-y-3 rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/50 p-4">
                    <div className="flex gap-2">
                      <div className="w-full space-y-1">
                        <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">
                          Phone Recipients
                        </label>
                        <div className="relative">
                          <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center rounded-l-md border-r border-zinc-300 bg-zinc-100 px-3 text-sm font-medium text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                            +91
                          </span>
                          <input
                            type="tel"
                            value={phoneRecipientInput}
                            onChange={(e) => setPhoneRecipientInput(normalizeRuleRecipientPhoneInput(e.target.value))}
                            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700
                           bg-white dark:bg-zinc-900
                           px-3 py-2 pl-16 text-sm text-zinc-900 dark:text-zinc-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500"
                            placeholder="9876543210"
                            disabled={loading}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                handleAddPhoneRecipient();
                              }
                            }}
                          />
                        </div>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">
                          Enter a 10-digit mobile number. +91 is added automatically.
                        </p>
                      </div>
                  <button
                    type="button"
                    onClick={handleAddPhoneRecipient}
                    className="px-3 py-2 rounded-md border border-zinc-300 dark:border-zinc-700 text-sm"
                    disabled={loading}
                  >
                    Add Phone
                  </button>
                </div>
                {phoneRecipients.length === 0 ? (
                  <p className="text-xs text-amber-700">Add the phone numbers who should receive SMS or WhatsApp alerts for this rule.</p>
                ) : (
                  <div className="rounded-md border border-zinc-200 dark:border-zinc-700 divide-y divide-zinc-200 dark:divide-zinc-700">
                    {phoneRecipients.map((phone) => (
                      <div key={phone} className="flex items-center justify-between px-3 py-2 text-sm">
                        <span>{phone}</span>
                        <button
                          type="button"
                          onClick={() => setPhoneRecipients((prev) => prev.filter((value) => value !== phone))}
                          className="text-red-600"
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <div className="flex items-center space-x-3">
            <input
              type="checkbox"
              id="enabled"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="rounded border-zinc-300 dark:border-zinc-700
                         text-blue-600 focus:ring-blue-500"
              disabled={loading}
            />
            <label
              htmlFor="enabled"
              className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Enable rule
            </label>
          </div>

          <div className="flex items-center gap-3 pt-4 border-t border-zinc-200 dark:border-zinc-700">
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium
                         hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {loading ? "Creating..." : "Create Rule"}
            </button>

            <button
              type="button"
              onClick={() => router.push("/rules")}
              disabled={loading}
              className="px-4 py-2 border border-zinc-300 dark:border-zinc-700
                         text-zinc-700 dark:text-zinc-300 rounded-md text-sm font-medium
                         hover:bg-zinc-50 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function CreateRulePage() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <CreateRuleContent />
    </Suspense>
  );
}
