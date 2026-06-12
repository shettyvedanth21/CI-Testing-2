"use client";

import { useEffect, useState } from "react";
import { listRules, createRule, updateRule, updateRuleStatus, deleteRule, Rule, RuleStatus } from "@/lib/ruleApi";
import { getDeviceFields } from "@/lib/dataApi";
import { COOLDOWN_MINUTE_PRESETS, formatCooldownLabel } from "@/lib/ruleCooldown";
import {
  dedupeRuleRecipientEmails,
  dedupeRuleRecipientPhones,
  isValidRuleRecipientEmail,
  isValidRuleRecipientPhone,
  normalizeRuleRecipientEmail,
  normalizeRuleRecipientPhoneInput,
  normalizeRuleRecipientPhone,
} from "@/lib/ruleRecipients";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select, Checkbox } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge } from "@/components/ui/badge";
import { usePermissions } from "@/hooks/usePermissions";
import { getRuleTypeBadgeLabel, getRuleTriggerSummary, RULE_TYPE_OPTIONS } from "@/lib/rulePresentation";
import { useAuth } from "@/lib/authContext";
import {
  buildUnavailableSelectedChannelMessage,
  getRuleNotificationChannelStates,
} from "@/lib/ruleNotificationChannels";

interface MachineRulesViewProps {
  deviceId: string;
}

const CONDITION_OPTIONS = [
  { value: ">", label: "Greater than (> )" },
  { value: ">=", label: "Greater than or equal (>=)" },
  { value: "<", label: "Less than (<)" },
  { value: "<=", label: "Less than or equal (<=)" },
  { value: "==", label: "Equal to (==)" },
  { value: "!=", label: "Not equal to (!=)" },
];

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

const COOLDOWN_TYPE_OPTIONS = [
  { value: "minutes", label: "Minutes" },
  { value: "no_repeat", label: "No repeat" },
];

const MINUTE_PRESET_VALUES = new Set(COOLDOWN_MINUTE_PRESETS.map((option) => option.value));

export function MachineRulesView({ deviceId }: MachineRulesViewProps) {
  const { canCreateRule } = usePermissions();
  const { me } = useAuth();
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [availableProperties, setAvailableProperties] = useState<{value: string, label: string}[]>([]);
  const [propertiesLoading, setPropertiesLoading] = useState(true);
  
  const [formData, setFormData] = useState({
    ruleName: "",
    ruleType: "threshold" as "threshold" | "time_based" | "continuous_idle_duration",
    property: "",
    condition: ">",
    threshold: "",
    timeWindowStart: "20:00",
    timeWindowEnd: "06:00",
    durationMinutes: "40",
    cooldownType: "minutes" as "minutes" | "no_repeat",
    cooldownValue: "15",
    enabled: true,
    email: false,
    sms: false,
    whatsapp: false,
    emailRecipients: [] as string[],
    emailRecipientInput: "",
    phoneRecipients: [] as string[],
    phoneRecipientInput: "",
  });
  const selectedNotificationChannels = [
    ...(formData.email ? ["email"] : []),
    ...(formData.sms ? ["sms"] : []),
    ...(formData.whatsapp ? ["whatsapp"] : []),
  ];
  const notificationChannelStates = getRuleNotificationChannelStates(me, selectedNotificationChannels);
  const unavailableSelectedChannelMessage = buildUnavailableSelectedChannelMessage(notificationChannelStates);
  const hasPhoneChannelSelected = notificationChannelStates.some(
    (state) => state.checked && (state.channel === "sms" || state.channel === "whatsapp"),
  );

  const handleCooldownTypeChange = (nextType: "minutes" | "no_repeat") => {
    setFormData((prev) => {
      if (nextType === prev.cooldownType) return prev;

      if (nextType === "minutes" && !MINUTE_PRESET_VALUES.has(prev.cooldownValue)) {
        return {
          ...prev,
          cooldownType: nextType,
          cooldownValue: "15",
        };
      }

      return {
        ...prev,
        cooldownType: nextType,
        cooldownValue: prev.cooldownValue || "15",
      };
    });
  };

  // Fetch available properties from device telemetry
  useEffect(() => {
    async function fetchProperties() {
      try {
        const fields = await getDeviceFields(deviceId);
        const properties = fields
          .filter((field): field is string => typeof field === "string" && field.trim().length > 0)
          .map(field => ({
          value: field,
          label: formatFieldLabel(field)
        }));
        setAvailableProperties(properties);
        if (fields.length > 0 && !formData.property) {
          setFormData(prev => ({ ...prev, property: fields[0] }));
        }
      } catch (err) {
        console.error("Failed to fetch device fields:", err);
        setAvailableProperties([]);
      } finally {
        setPropertiesLoading(false);
      }
    }
    
    fetchProperties();
  }, [deviceId]);

  // Update property when available properties change
  useEffect(() => {
    if (availableProperties.length > 0 && !availableProperties.find(p => p.value === formData.property)) {
      setFormData(prev => ({ ...prev, property: availableProperties[0].value }));
    }
  }, [availableProperties, formData.property]);

  useEffect(() => {
    fetchRules();
  }, [deviceId]);

  const openCreateForm = () => {
    resetForm();
    setShowForm(true);
  };

  const openEditForm = (rule: Rule) => {
    const emailRecipients = dedupeRuleRecipientEmails(
      rule.notificationRecipients
        .filter((recipient) => recipient.channel === "email")
        .map((recipient) => recipient.value),
    );
    const phoneRecipients = dedupeRuleRecipientPhones(
      rule.notificationRecipients
        .filter((recipient) => recipient.channel === "sms" || recipient.channel === "whatsapp")
        .map((recipient) => recipient.value),
    );

    setActionError(null);
    setFormError(null);
    setEditingRule(rule);
    setShowForm(true);
    setFormData({
      ruleName: rule.ruleName,
      ruleType: rule.ruleType,
      property: rule.property || availableProperties[0]?.value || "power",
      condition: rule.condition || ">",
      threshold: rule.threshold != null ? String(rule.threshold) : "",
      timeWindowStart: rule.timeWindowStart || "20:00",
      timeWindowEnd: rule.timeWindowEnd || "06:00",
      durationMinutes: rule.durationMinutes != null ? String(rule.durationMinutes) : "40",
      cooldownType:
        rule.cooldownMode === "no_repeat"
          ? "no_repeat"
          : "minutes",
      cooldownValue:
        rule.cooldownMode === "no_repeat"
          ? "15"
          : rule.cooldownUnit === "seconds"
            ? String(Math.max(1, Math.ceil((rule.cooldownSeconds || 60) / 60)))
            : String(rule.cooldownMinutes || 15),
      enabled: rule.status === "active",
      email: rule.notificationChannels.includes("email"),
      sms: rule.notificationChannels.includes("sms"),
      whatsapp: rule.notificationChannels.includes("whatsapp"),
      emailRecipients,
      emailRecipientInput: "",
      phoneRecipients,
      phoneRecipientInput: "",
    });
  };

  const fetchRules = async () => {
    setLoading(true);
    setActionError(null);
    try {
      const response = await listRules({ deviceId });
      setRules(response.data);
    } catch (err) {
      console.error("Failed to fetch rules:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) {
      return;
    }
    setFormError(null);
    if (formData.ruleType === "threshold" && (formData.threshold === "" || Number.isNaN(Number(formData.threshold)))) {
      setFormError("Please enter a valid threshold value.");
      return;
    }
    if (formData.ruleType === "time_based" && (!formData.timeWindowStart || !formData.timeWindowEnd)) {
      setFormError("Please configure restricted time window.");
      return;
    }
    if (formData.ruleType === "continuous_idle_duration") {
      const numericDuration = Number(formData.durationMinutes);
      if (formData.durationMinutes === "" || Number.isNaN(numericDuration) || numericDuration <= 0 || !Number.isInteger(numericDuration)) {
        setFormError("Please provide a valid duration in minutes.");
        return;
      }
    }
    
    const channels: string[] = [];
    if (formData.email) channels.push("email");
    if (formData.sms) channels.push("sms");
    if (formData.whatsapp) channels.push("whatsapp");

    if (unavailableSelectedChannelMessage) {
      setFormError(unavailableSelectedChannelMessage);
      return;
    }
    if (channels.length === 0) {
      setFormError("Please select at least one notification channel.");
      return;
    }
    if (formData.email && formData.emailRecipients.length === 0) {
      setFormError("Add at least one email recipient when email notifications are enabled.");
      return;
    }
    if (hasPhoneChannelSelected && formData.phoneRecipients.length === 0) {
      setFormError("Add at least one phone recipient when SMS or WhatsApp notifications are enabled.");
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = {
        ruleName: formData.ruleName,
        ruleType: formData.ruleType,
        property: formData.ruleType === "threshold" ? formData.property : undefined,
        condition: formData.ruleType === "threshold" ? formData.condition : undefined,
        threshold: formData.ruleType === "threshold" ? parseFloat(formData.threshold) : undefined,
        timeWindowStart: formData.ruleType === "time_based" ? formData.timeWindowStart : undefined,
        timeWindowEnd: formData.ruleType === "time_based" ? formData.timeWindowEnd : undefined,
        timezone: "Asia/Kolkata",
        timeCondition: formData.ruleType === "time_based" ? "running_in_window" : undefined,
        durationMinutes: formData.ruleType === "continuous_idle_duration" ? Number(formData.durationMinutes) : undefined,
        scope: "selected_devices" as const,
        deviceIds: [deviceId],
        notificationChannels: channels,
        notificationRecipients: [
          ...(formData.email ? formData.emailRecipients.map((value) => ({ channel: "email", value })) : []),
          ...(formData.sms ? formData.phoneRecipients.map((value) => ({ channel: "sms", value })) : []),
          ...(formData.whatsapp ? formData.phoneRecipients.map((value) => ({ channel: "whatsapp", value })) : []),
        ],
        cooldownMode: formData.cooldownType === "no_repeat" ? "no_repeat" as const : "interval" as const,
        cooldownUnit: "minutes" as const,
        cooldownMinutes:
          formData.cooldownType === "no_repeat"
            ? 0
            : Number(formData.cooldownValue),
        cooldownSeconds:
          formData.cooldownType === "no_repeat"
            ? 0
            : Number(formData.cooldownValue) * 60,
      };

      if (editingRule) {
        await updateRule(editingRule.ruleId, payload);
      } else {
        await createRule(payload);
      }
      
      setShowForm(false);
      resetForm();
      await fetchRules();
    } catch (err) {
      console.error("Failed to create rule:", err);
      setFormError(err instanceof Error ? err.message : "Failed to create rule.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleToggleStatus = async (ruleId: string, currentStatus: RuleStatus) => {
    const newStatus = currentStatus === "active" ? "paused" : "active";
    try {
      setActionError(null);
      await updateRuleStatus(ruleId, newStatus);
      await fetchRules();
    } catch (err) {
      console.error("Failed to update rule status:", err);
      setActionError(err instanceof Error ? err.message : "Failed to update rule status.");
    }
  };

  const handleDelete = async (ruleId: string) => {
    if (!confirm("Are you sure you want to delete this rule?")) return;
    
    try {
      setActionError(null);
      await deleteRule(ruleId);
      await fetchRules();
    } catch (err) {
      console.error("Failed to delete rule:", err);
      setActionError(err instanceof Error ? err.message : "Failed to delete rule.");
    }
  };

  const resetForm = () => {
    setFormError(null);
    setFormData({
      ruleName: "",
      ruleType: "threshold",
      property: "power",
      condition: ">",
      threshold: "",
      timeWindowStart: "20:00",
      timeWindowEnd: "06:00",
      durationMinutes: "40",
      cooldownType: "minutes",
      cooldownValue: "15",
      enabled: true,
      email: false,
      sms: false,
      whatsapp: false,
      emailRecipients: [],
      emailRecipientInput: "",
      phoneRecipients: [],
      phoneRecipientInput: "",
    });
    setEditingRule(null);
  };

  const handleAddEmailRecipient = () => {
    const normalized = normalizeRuleRecipientEmail(formData.emailRecipientInput);
    if (!normalized) {
      setFormError("Enter an email recipient.");
      return;
    }
    if (!isValidRuleRecipientEmail(normalized)) {
      setFormError("Enter a valid email recipient.");
      return;
    }
    setFormError(null);
    setFormData((prev) => ({
      ...prev,
      email: true,
      emailRecipients: dedupeRuleRecipientEmails([...prev.emailRecipients, normalized]),
      emailRecipientInput: "",
    }));
  };

  const handleAddPhoneRecipient = () => {
    const normalized = normalizeRuleRecipientPhone(formData.phoneRecipientInput);
    if (!normalized) {
      setFormError("Enter a phone recipient.");
      return;
    }
    if (!isValidRuleRecipientPhone(normalized)) {
      setFormError("Enter a valid phone recipient.");
      return;
    }
    setFormError(null);
    setFormData((prev) => ({
      ...prev,
      phoneRecipients: dedupeRuleRecipientPhones([...prev.phoneRecipients, normalized]),
      phoneRecipientInput: "",
    }));
  };

  const handleRemoveEmailRecipient = (email: string) => {
    setFormData((prev) => ({
      ...prev,
      emailRecipients: prev.emailRecipients.filter((value) => value !== email),
    }));
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Machine Rules</h2>
          <p className="text-sm text-slate-500">Configure monitoring rules for this machine</p>
        </div>
        {canCreateRule ? (
          <Button onClick={() => {
            if (showForm) {
              setShowForm(false);
              resetForm();
              return;
            }
            openCreateForm();
          }}>
            {showForm ? "Cancel" : "Add Rule"}
          </Button>
        ) : null}
      </div>

      {actionError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {actionError}
        </div>
      ) : null}

      {showForm && canCreateRule && (
        <Card>
          <CardHeader>
            <CardTitle>{editingRule ? "Edit Rule" : "Create New Rule"}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Input
                  label="Rule Name"
                  value={formData.ruleName}
                  onChange={(e) => setFormData({ ...formData, ruleName: e.target.value })}
                  required
                />

                  <Select
                    label="Rule Type"
                    value={formData.ruleType}
                    onChange={(e) => setFormData({ ...formData, ruleType: e.target.value as "threshold" | "time_based" | "continuous_idle_duration" })}
                    options={[...RULE_TYPE_OPTIONS]}
                  />
                
                {formData.ruleType === "threshold" ? (
                  <>
                    {propertiesLoading ? (
                      <div className="text-sm text-slate-500 py-2">Loading properties...</div>
                    ) : availableProperties.length === 0 ? (
                      <div className="text-sm text-red-500 py-2">No numeric properties found</div>
                    ) : (
                      <Select
                        label="Property"
                        value={formData.property}
                        onChange={(e) => setFormData({ ...formData, property: e.target.value })}
                        options={availableProperties}
                      />
                    )}

                    <Select
                      label="Condition"
                      value={formData.condition}
                      onChange={(e) => setFormData({ ...formData, condition: e.target.value })}
                      options={CONDITION_OPTIONS}
                    />

                    <Input
                      label="Threshold Value"
                      type="number"
                      step="0.01"
                      value={formData.threshold}
                      onChange={(e) => setFormData({ ...formData, threshold: e.target.value })}
                      required
                    />
                  </>
                ) : formData.ruleType === "time_based" ? (
                  <>
                    <Input
                      label="Parameter"
                      value="Power Status (running)"
                      onChange={() => undefined}
                      disabled
                    />
                    <Input
                      label="Restricted From (IST)"
                      type="time"
                      value={formData.timeWindowStart}
                      onChange={(e) => setFormData({ ...formData, timeWindowStart: e.target.value })}
                      required
                    />
                    <Input
                      label="Restricted To (IST)"
                      type="time"
                      value={formData.timeWindowEnd}
                      onChange={(e) => setFormData({ ...formData, timeWindowEnd: e.target.value })}
                      required
                    />
                    <Input
                      label="Timezone"
                      value="Asia/Kolkata"
                      onChange={() => undefined}
                      disabled
                    />
                  </>
                ) : (
                  <>
                    <Input
                      label="Duration (minutes)"
                      type="number"
                      min={1}
                      step={1}
                      value={formData.durationMinutes}
                      onChange={(e) => setFormData({ ...formData, durationMinutes: e.target.value })}
                      required
                    />
                    <div className="md:col-span-2 text-sm text-slate-500">
                      Alert when the machine stays idle continuously for N minutes.
                    </div>
                  </>
                )}

                <div className="space-y-4">
                  <Select
                    label="Cooldown Type"
                    value={formData.cooldownType}
                    onChange={(e) => handleCooldownTypeChange(e.target.value as "minutes" | "no_repeat")}
                    options={COOLDOWN_TYPE_OPTIONS}
                  />

                  {formData.cooldownType === "minutes" ? (
                    <Select
                      label="Cooldown Duration"
                      value={formData.cooldownValue}
                      onChange={(e) => setFormData({ ...formData, cooldownValue: e.target.value })}
                      options={COOLDOWN_MINUTE_PRESETS}
                    />
                  ) : null}
                </div>
              </div>
              
                <div className="space-y-2">
                <p className="text-sm font-medium text-slate-700">Notification Channels</p>
                <div className="space-y-3">
                  {notificationChannelStates.map((state) => (
                    <div key={state.channel} className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                      <Checkbox
                        label={state.label}
                        checked={state.checked}
                        disabled={state.disabled}
                        onChange={(e) => {
                          const checked = e.target.checked;
                          if (state.channel === "email") {
                            setFormData({
                              ...formData,
                              email: checked,
                              emailRecipients: checked ? formData.emailRecipients : [],
                              emailRecipientInput: checked ? formData.emailRecipientInput : "",
                            });
                            return;
                          }
                          if (state.channel === "sms") {
                            setFormData({
                              ...formData,
                              sms: checked,
                              phoneRecipients: checked ? formData.phoneRecipients : formData.whatsapp ? formData.phoneRecipients : [],
                              phoneRecipientInput: checked ? formData.phoneRecipientInput : formData.whatsapp ? formData.phoneRecipientInput : "",
                            });
                            return;
                          }
                          setFormData({
                            ...formData,
                            whatsapp: checked,
                            phoneRecipients: checked ? formData.phoneRecipients : formData.sms ? formData.phoneRecipients : [],
                            phoneRecipientInput: checked ? formData.phoneRecipientInput : formData.sms ? formData.phoneRecipientInput : "",
                          });
                        }}
                      />
                      <p
                        className={`mt-2 text-sm ${
                          state.legacyUnavailable
                            ? "text-amber-700"
                            : state.available
                              ? "text-slate-500"
                              : "text-slate-600"
                        }`}
                      >
                        {state.helperText}
                      </p>
                    </div>
                  ))}
                </div>
                {formData.email ? (
                  <div className="mt-4 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                    <div className="flex gap-2">
                      <Input
                        label="Email Recipients"
                        type="email"
                        value={formData.emailRecipientInput}
                        onChange={(e) => setFormData({ ...formData, emailRecipientInput: e.target.value })}
                        placeholder="alerts@planta.com"
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddEmailRecipient();
                          }
                        }}
                      />
                      <div className="pt-6">
                        <Button type="button" variant="outline" onClick={handleAddEmailRecipient}>
                          Add Email
                        </Button>
                      </div>
                    </div>
                    {formData.emailRecipients.length === 0 ? (
                      <p className="text-sm text-amber-700">
                        Add the recipients who should receive alerts for this rule.
                      </p>
                    ) : (
                      <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
                        {formData.emailRecipients.map((email) => (
                          <div key={email} className="flex items-center justify-between px-3 py-2 text-sm text-slate-800">
                            <span>{email}</span>
                            <button
                              type="button"
                              className="rounded-md px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                              onClick={() => handleRemoveEmailRecipient(email)}
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
                  <div className="mt-4 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                    <div className="flex gap-2">
                      <Input
                        label="Phone Recipients"
                        type="tel"
                        value={formData.phoneRecipientInput}
                        onChange={(e) => setFormData({ ...formData, phoneRecipientInput: normalizeRuleRecipientPhoneInput(e.target.value) })}
                        placeholder="9876543210"
                        prefix="+91"
                        helperText="Enter a 10-digit mobile number. +91 is added automatically."
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleAddPhoneRecipient();
                          }
                        }}
                      />
                      <div className="pt-6">
                        <Button type="button" variant="outline" onClick={handleAddPhoneRecipient}>
                          Add Phone
                        </Button>
                      </div>
                    </div>
                    {formData.phoneRecipients.length === 0 ? (
                      <p className="text-sm text-amber-700">Add the phone numbers who should receive SMS or WhatsApp alerts for this rule.</p>
                    ) : (
                      <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
                        {formData.phoneRecipients.map((phone) => (
                          <div key={phone} className="flex items-center justify-between px-3 py-2 text-sm text-slate-800">
                            <span>{phone}</span>
                            <button
                              type="button"
                              className="rounded-md px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                              onClick={() => setFormData((prev) => ({
                                ...prev,
                                phoneRecipients: prev.phoneRecipients.filter((value) => value !== phone),
                              }))}
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
              
              <div className="flex gap-3 pt-4">
                {canCreateRule ? (
                  <Button type="submit" isLoading={isSubmitting} disabled={isSubmitting}>
                    {isSubmitting ? (editingRule ? "Updating Rule..." : "Creating Rule...") : (editingRule ? "Update Rule" : "Create Rule")}
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="outline"
                  disabled={isSubmitting}
                  onClick={() => { setShowForm(false); resetForm(); }}
                >
                  Cancel
                </Button>
              </div>
              {formError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {formError}
                </div>
              ) : null}
            </form>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Active Rules</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto"></div>
              <p className="mt-2 text-sm text-slate-500">Loading rules...</p>
            </div>
          ) : rules.length === 0 ? (
            <div className="text-center py-8 text-slate-500">
              <p>No rules configured for this machine</p>
              <p className="text-sm mt-1">Add a rule to start monitoring</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rule Name</TableHead>
                  <TableHead>Property</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule) => (
                  <TableRow key={rule.ruleId}>
                    <TableCell className="font-medium">{rule.ruleName}</TableCell>
                    <TableCell className="capitalize">
                      {rule.ruleType === "time_based"
                        ? "Power Status (running)"
                        : rule.ruleType === "continuous_idle_duration"
                          ? "Idle State"
                          : (rule.property || "N/A")}
                    </TableCell>
                    <TableCell>
                      {getRuleTriggerSummary(rule)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={rule.status} />
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
                          {getRuleTypeBadgeLabel(rule.ruleType)}
                        </span>
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
                          {formatCooldownLabel(rule)}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        {canCreateRule ? (
                          <button
                            type="button"
                            onClick={() => openEditForm(rule)}
                            className="text-sm text-slate-700 hover:text-slate-900 px-3 py-1 hover:bg-slate-100 rounded"
                          >
                            Edit
                          </button>
                        ) : null}
                        {canCreateRule ? (
                          <button
                            type="button"
                            onClick={() => handleToggleStatus(rule.ruleId, rule.status)}
                            className={`text-sm px-3 py-1 rounded ${
                              rule.status === "active"
                                ? "text-amber-600 hover:bg-amber-50"
                                : "text-green-600 hover:bg-green-50"
                            }`}
                          >
                            {rule.status === "active" ? "Pause" : "Enable"}
                          </button>
                        ) : null}
                        {canCreateRule ? (
                          <button
                            type="button"
                            onClick={() => handleDelete(rule.ruleId)}
                            className="text-sm text-red-600 hover:text-red-800 px-3 py-1 hover:bg-red-50 rounded"
                          >
                            Delete
                          </button>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
