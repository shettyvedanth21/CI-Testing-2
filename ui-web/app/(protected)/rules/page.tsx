"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listRules, createRule, updateRuleStatus, deleteRule, Rule, RuleStatus } from "@/lib/ruleApi";
import { getDevices, Device } from "@/lib/deviceApi";
import { COOLDOWN_MINUTE_PRESETS, formatCooldownLabel } from "@/lib/ruleCooldown";
import {
  getAllDevicesProperties,
  getCommonProperties,
  getDeviceProperties,
  getActivityEvents,
  clearActivityHistory,
  ActivityEvent,
} from "@/lib/dataApi";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select, Checkbox } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge } from "@/components/ui/badge";
import { formatIST } from "@/lib/utils";
import { usePermissions } from "@/hooks/usePermissions";
import { ReadOnlyBanner } from "@/components/auth/ReadOnlyBanner";
import { normalizeSelectedDeviceIds } from "@/lib/deviceSelection";
import { useAuth } from "@/lib/authContext";
import {
  getAllDevicesScopeLabel,
  getRuleDeviceScopeDisplay,
  getRuleScopeOptions,
  getRulesPageSubtitle,
  getRulesScopeHint,
  isPlantScopedRuleRole as isPlantScopedRuleRoleValue,
} from "@/lib/ruleScope";
import { RULE_TYPE_OPTIONS, getRuleTypeBadgeLabel, getRuleTriggerSummary } from "@/lib/rulePresentation";
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

const CONDITION_OPTIONS = [
  { value: ">", label: "Greater than (> )" },
  { value: ">=", label: "Greater than or equal (>=)" },
  { value: "<", label: "Less than (<)" },
  { value: "<=", label: "Less than or equal (<=)" },
  { value: "==", label: "Equal to (==)" },
  { value: "!=", label: "Not equal to (!=)" },
];

const MINUTE_PRESET_VALUES = new Set(COOLDOWN_MINUTE_PRESETS.map((option) => option.value));

const COOLDOWN_TYPE_OPTIONS = [
  { value: "minutes", label: "Minutes" },
  { value: "no_repeat", label: "No repeat" },
];

const METRIC_LABELS: Record<string, string> = {
  power: "Power", voltage: "Voltage", current: "Current", temperature: "Temperature",
  pressure: "Pressure", humidity: "Humidity", vibration: "Vibration", frequency: "Frequency",
  power_factor: "Power Factor", speed: "Speed", torque: "Torque", oil_pressure: "Oil Pressure",
};

function formatPropertyLabel(property: unknown): string {
  if (typeof property !== "string") {
    return "Unknown";
  }
  const normalized = property.trim();
  if (!normalized) {
    return "Unknown";
  }
  if (METRIC_LABELS[normalized]) {
    return METRIC_LABELS[normalized];
  }
  return normalized
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function RulesEmptyState({
  canCreateRule,
  isPlantScopedRuleRole,
  onCreate,
}: {
  canCreateRule: boolean;
  isPlantScopedRuleRole: boolean;
  onCreate: () => void;
}) {
  return (
    <div className="text-center py-12 text-slate-500">
      <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
        <svg className="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      </div>
      <h3 className="text-lg font-medium text-slate-900 mb-2">No rules found</h3>
      <p className="text-sm mb-4">
        {isPlantScopedRuleRole
          ? "Create your first rule to start monitoring your accessible machines"
          : "Create your first rule to start monitoring"}
      </p>
      {canCreateRule ? <Button className="w-full sm:w-auto" onClick={onCreate}>Create Rule</Button> : null}
    </div>
  );
}

function RuleMobileCard({
  rule,
  canCreateRule,
  deviceNames,
  onToggleStatus,
  onDelete,
}: {
  rule: Rule;
  canCreateRule: boolean;
  deviceNames: string;
  onToggleStatus: (ruleId: string, status: RuleStatus) => void;
  onDelete: (ruleId: string) => void;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h4 className="text-base font-semibold text-slate-900">{rule.ruleName}</h4>
          <p className="mt-1 text-sm text-slate-500">{getRuleTriggerSummary(rule)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={rule.status} />
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
            {getRuleTypeBadgeLabel(rule.ruleType)}
          </span>
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
            {formatCooldownLabel(rule)}
          </span>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">Property</dt>
          <dd className="mt-1 text-sm text-slate-900 capitalize">
            {rule.ruleType === "time_based"
              ? "Power Status (running)"
              : rule.ruleType === "continuous_idle_duration"
                ? "Idle State"
                : formatPropertyLabel(rule.property || "N/A")}
          </dd>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">Devices</dt>
          <dd className="mt-1 text-sm text-slate-900">{deviceNames}</dd>
        </div>
      </dl>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {canCreateRule ? (
          <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => onToggleStatus(rule.ruleId, rule.status)}>
            {rule.status === "active" ? "Pause" : "Enable"}
          </Button>
        ) : null}
        <Link href={`/rules/${rule.ruleId}`} className="w-full sm:w-auto">
          <Button size="sm" variant="outline" className="w-full">View</Button>
        </Link>
        {canCreateRule ? (
          <Button size="sm" variant="danger" className="w-full sm:w-auto" onClick={() => onDelete(rule.ruleId)}>
            Delete
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function AlertHistoryMobileCard({ event }: { event: ActivityEvent }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold uppercase tracking-[0.08em] text-slate-500">{event.eventType.replace(/_/g, " ")}</h4>
          <p className="mt-1 text-base font-semibold text-slate-900">{event.title}</p>
        </div>
        <p className="text-xs text-slate-500">{formatIST(event.createdAt, "N/A")}</p>
      </div>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">Device</dt>
          <dd className="mt-1 break-all font-mono text-xs text-slate-900">{event.deviceId || "GLOBAL"}</dd>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 sm:col-span-2">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-500">Message</dt>
          <dd className="mt-1 text-sm text-slate-900">{event.message}</dd>
        </div>
      </dl>
    </div>
  );
}

export default function RulesPage() {
  const { canCreateRule, currentRole } = usePermissions();
  const { me } = useAuth();
  const [rules, setRules] = useState<Rule[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  
  const [allDeviceProperties, setAllDeviceProperties] = useState<Record<string, string[]>>({});
  const [availableProperties, setAvailableProperties] = useState<{value: string, label: string}[]>([]);
  const [propertiesLoading, setPropertiesLoading] = useState(true);
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([]);
  const [selectedAlertDevice, setSelectedAlertDevice] = useState<string>("all");
  
  const [formData, setFormData] = useState<{
    ruleName: string;
    ruleType: "threshold" | "time_based" | "continuous_idle_duration";
    scope: "all_devices" | "selected_devices";
    selectedDevices: string[];
    property: string;
    condition: string;
    threshold: string;
    timeWindowStart: string;
    timeWindowEnd: string;
    durationMinutes: string;
    cooldownType: "minutes" | "no_repeat";
    cooldownValue: string;
    enabled: boolean;
    email: boolean;
    sms: boolean;
    whatsapp: boolean;
    emailRecipients: string[];
    phoneRecipients: string[];
    emailRecipientInput: string;
    phoneRecipientInput: string;
  }>({
    ruleName: "",
    ruleType: "threshold",
    scope: "all_devices",
    selectedDevices: [],
    property: "",
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

  const isPlantScopedRuleRole = isPlantScopedRuleRoleValue(currentRole);
  const scopeOptions = getRuleScopeOptions(currentRole);
  const allDevicesScopeLabel = getAllDevicesScopeLabel(currentRole);
  const rulesPageSubtitle = getRulesPageSubtitle(currentRole);
  const rulesScopeHint = getRulesScopeHint(currentRole);
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

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    if (selectedAlertDevice === "all") return;
    if (devices.some((device) => device.id === selectedAlertDevice)) return;
    setSelectedAlertDevice("all");
  }, [devices, selectedAlertDevice]);

  useEffect(() => {
    if (formData.scope !== "selected_devices") return;
    const normalized = normalizeSelectedDeviceIds(
      formData.selectedDevices,
      devices.map((device) => device.id),
    );
    if (
      normalized.length !== formData.selectedDevices.length ||
      normalized.some((deviceId, index) => deviceId !== formData.selectedDevices[index])
    ) {
      setFormData((prev) => ({ ...prev, selectedDevices: normalized }));
    }
  }, [devices, formData.scope, formData.selectedDevices]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [rulesResult, devicesResult, propsResult, eventsResult] = await Promise.allSettled([
        listRules(),
        getDevices(),
        getAllDevicesProperties(),
        getActivityEvents({ page: 1, pageSize: 50 }),
      ]);

      if (rulesResult.status === "fulfilled") {
        setRules(rulesResult.value.data);
      } else {
        console.error("Failed to load rules:", rulesResult.reason);
        setRules([]);
      }

      if (devicesResult.status === "fulfilled") {
        setDevices(devicesResult.value);
      } else {
        console.error("Failed to load devices:", devicesResult.reason);
        setDevices([]);
      }

      if (propsResult.status === "fulfilled") {
        setAllDeviceProperties(propsResult.value.devices);
        const allProps = (propsResult.value.all_properties || []).filter(
          (p: unknown): p is string => typeof p === "string" && p.trim().length > 0
        );
        setAvailableProperties(allProps.map(p => ({
          value: p,
          label: formatPropertyLabel(p)
        })));

        if (allProps.length > 0 && !formData.property) {
          setFormData(prev => ({ ...prev, property: allProps[0] }));
        }
      } else {
        console.error("Failed to load device properties:", propsResult.reason);
        setAllDeviceProperties({});
      }

      if (eventsResult.status === "fulfilled") {
        setActivityEvents(eventsResult.value.data);
      } else {
        console.error("Failed to load activity events:", eventsResult.reason);
        setActivityEvents([]);
      }
    } catch (err) {
      console.error("Failed to load data:", err);
    } finally {
      setLoading(false);
      setPropertiesLoading(false);
    }
  };

  const handleClearRulesHistory = async () => {
    const targetDevice = selectedAlertDevice === "all" ? undefined : selectedAlertDevice;
    const label = targetDevice ? `for ${targetDevice}` : "for all devices";
    if (!confirm(`Clear alert history ${label}?`)) return;

    try {
      await clearActivityHistory(targetDevice);
      loadData();
    } catch (err) {
      console.error("Failed to clear activity history:", err);
    }
  };

  const filteredEvents = selectedAlertDevice === "all"
    ? activityEvents
    : activityEvents.filter((e) => e.deviceId === selectedAlertDevice);

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

  useEffect(() => {
    async function updateAvailableProperties() {
      if (formData.scope === "all_devices") {
        const activeDevices = devices.filter(d => d.runtime_status === "running").map(d => d.id);
        if (activeDevices.length > 0) {
          try {
            const common = await getCommonProperties(activeDevices);
            const props = (common.properties || [])
              .filter((p: unknown): p is string => typeof p === "string" && p.trim().length > 0)
              .map(p => ({
              value: p,
              label: formatPropertyLabel(p)
            }));
            setAvailableProperties(props);
            if (!props.find(p => p.value === formData.property)) {
              setFormData(prev => ({ 
                ...prev, 
                property: props.length > 0 ? props[0].value : "" 
              }));
            }
          } catch (err) {
            console.error("Failed to get common properties:", err);
          }
        }
      } else if (formData.selectedDevices.length === 1) {
        try {
          const deviceId = formData.selectedDevices[0];
          const props = (await getDeviceProperties(deviceId)).filter(
            (p: unknown): p is string => typeof p === "string" && p.trim().length > 0
          );
          const formattedProps = props.map(p => ({
            value: p,
            label: formatPropertyLabel(p)
          }));
          setAvailableProperties(formattedProps);
          if (!formattedProps.find(p => p.value === formData.property)) {
            setFormData(prev => ({ 
              ...prev, 
              property: formattedProps.length > 0 ? formattedProps[0].value : "" 
            }));
          }
        } catch (err) {
          console.error("Failed to get device properties:", err);
          setAvailableProperties([]);
          setFormData(prev => ({ ...prev, property: "" }));
        }
      } else if (formData.selectedDevices.length > 1) {
        try {
          const common = await getCommonProperties(formData.selectedDevices);
            const props = (common.properties || [])
              .filter((p: unknown): p is string => typeof p === "string" && p.trim().length > 0)
              .map(p => ({
              value: p,
              label: formatPropertyLabel(p)
            }));
          setAvailableProperties(props);
          if (!props.find(p => p.value === formData.property)) {
            setFormData(prev => ({ 
              ...prev, 
              property: props.length > 0 ? props[0].value : "" 
            }));
          }
        } catch (err) {
          console.error("Failed to get common properties:", err);
        }
      } else {
        setAvailableProperties([]);
        setFormData(prev => ({ ...prev, property: "" }));
      }
    }
    
    updateAvailableProperties();
  }, [formData.scope, formData.selectedDevices, devices]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) {
      return;
    }
    setFormError(null);

    if (!formData.ruleName.trim()) {
      setFormError("Please provide a rule name.");
      return;
    }
    if (formData.scope === "selected_devices" && formData.selectedDevices.length === 0) {
      setFormError("Please select at least one accessible device.");
      return;
    }
    
    if (formData.ruleType === "threshold" && !formData.property) {
      setFormError("Please select a property.");
      return;
    }
    if (formData.ruleType === "threshold" && (formData.threshold === "" || Number.isNaN(Number(formData.threshold)))) {
      setFormError("Please provide a valid threshold value.");
      return;
    }
    if (formData.ruleType === "time_based" && (!formData.timeWindowStart || !formData.timeWindowEnd)) {
      setFormError("Please provide restricted time window.");
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
      await createRule({
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
        scope: formData.scope,
        deviceIds: formData.scope === "selected_devices" ? formData.selectedDevices : [],
        notificationChannels: channels,
        notificationRecipients: [
          ...(formData.email ? formData.emailRecipients.map((value) => ({ channel: "email", value })) : []),
          ...(formData.sms ? formData.phoneRecipients.map((value) => ({ channel: "sms", value })) : []),
          ...(formData.whatsapp ? formData.phoneRecipients.map((value) => ({ channel: "whatsapp", value })) : []),
        ],
        cooldownMode: formData.cooldownType === "no_repeat" ? "no_repeat" : "interval",
        cooldownUnit: "minutes",
        cooldownMinutes:
          formData.cooldownType === "no_repeat"
            ? 0
            : Number(formData.cooldownValue),
        cooldownSeconds:
          formData.cooldownType === "no_repeat"
            ? 0
            : Number(formData.cooldownValue) * 60,
      });
      
      setShowForm(false);
      resetForm();
      await loadData();
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
      await updateRuleStatus(ruleId, newStatus);
      loadData();
    } catch (err) {
      console.error("Failed to update rule status:", err);
    }
  };

  const handleDelete = async (ruleId: string) => {
    if (!confirm("Are you sure you want to delete this rule?")) return;
    
    try {
      await deleteRule(ruleId);
      loadData();
    } catch (err) {
      console.error("Failed to delete rule:", err);
    }
  };

  const resetForm = () => {
    setFormError(null);
    setFormData({
      ruleName: "",
      ruleType: "threshold",
      scope: "all_devices",
      selectedDevices: [],
      property: availableProperties.length > 0 ? availableProperties[0].value : "",
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

  const getDeviceNames = (deviceIds: string[]) => {
    return getRuleDeviceScopeDisplay(deviceIds, currentRole, (id) => devices.find((d) => d.id === id)?.name || id);
  };

  return (
    <div className="p-4 sm:p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <ReadOnlyBanner />
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-slate-900">Rules</h1>
            <p className="text-slate-500 mt-1">
              {rulesPageSubtitle}
            </p>
          </div>
          {canCreateRule ? (
            <Button className="w-full sm:w-auto" onClick={() => setShowForm(!showForm)}>
              {showForm ? "Cancel" : "Add Rule"}
            </Button>
          ) : null}
        </div>

        {showForm && canCreateRule && (
          <Card>
            <CardHeader>
              <CardTitle>Create New Rule</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-6">
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
                  
                  <Select
                    label="Scope"
                    value={formData.scope}
                    onChange={(e) => setFormData({ ...formData, scope: e.target.value as "all_devices" | "selected_devices", selectedDevices: [] })}
                    options={scopeOptions}
                  />

                  {formData.scope === "all_devices" && rulesScopeHint ? (
                    <div className="md:col-span-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      {rulesScopeHint}
                    </div>
                  ) : null}
                  
                  {formData.scope === "selected_devices" && (
                    <div className="md:col-span-2">
                      <p className="text-sm font-medium text-slate-700 mb-2">
                        {isPlantScopedRuleRole ? "Select Accessible Devices" : "Select Devices"}
                      </p>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        {devices.map((device) => (
                          <label
                            key={device.id}
                            className="flex min-h-11 items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 cursor-pointer hover:bg-slate-100"
                          >
                            <input
                              type="checkbox"
                              checked={formData.selectedDevices.includes(device.id)}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  setFormData({
                                    ...formData,
                                    selectedDevices: [...formData.selectedDevices, device.id],
                                  });
                                } else {
                                  setFormData({
                                    ...formData,
                                    selectedDevices: formData.selectedDevices.filter(
                                      (id) => id !== device.id
                                    ),
                                  });
                                }
                              }}
                              className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-slate-700">{device.name}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}

                  {formData.ruleType === "threshold" ? (
                    <>
                      {propertiesLoading ? (
                        <div className="md:col-span-2">
                          <p className="text-sm font-medium text-slate-700 mb-1">Property</p>
                          <div className="text-sm text-slate-500 py-2">Loading properties...</div>
                        </div>
                      ) : availableProperties.length === 0 ? (
                        <div className="md:col-span-2">
                          <p className="text-sm font-medium text-slate-700 mb-1">Property</p>
                          <div className="text-sm text-red-500 py-2">
                            {formData.scope === "selected_devices" && formData.selectedDevices.length === 0
                              ? "Select devices to see available properties"
                              : "No common properties available. Devices may have different telemetry fields."}
                          </div>
                        </div>
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
                    <div className="md:col-span-2 space-y-2">
                      <Input
                        label="Duration (minutes)"
                        type="number"
                        min={1}
                        step={1}
                        value={formData.durationMinutes}
                        onChange={(e) => setFormData({ ...formData, durationMinutes: e.target.value })}
                        required
                      />
                      <p className="text-xs text-slate-500">
                        Alert when the machine stays idle continuously for N minutes.
                      </p>
                    </div>
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
                      <div className="flex flex-col gap-2 sm:flex-row">
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
                        <div className="pt-0 sm:pt-6">
                          <Button type="button" variant="outline" className="w-full sm:w-auto" onClick={handleAddEmailRecipient}>
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
                    <div className="flex flex-col gap-2 sm:flex-row">
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
                      <div className="pt-0 sm:pt-6">
                        <Button type="button" variant="outline" className="w-full sm:w-auto" onClick={handleAddPhoneRecipient}>
                          Add Phone
                        </Button>
                      </div>
                    </div>
                    {formData.phoneRecipients.length === 0 ? (
                      <p className="text-sm text-amber-700">
                        Add the phone numbers who should receive SMS or WhatsApp alerts for this rule.
                      </p>
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
                
                <div className="flex flex-col gap-3 pt-4 sm:flex-row">
                  <Button
                    type="submit"
                    className="w-full sm:w-auto"
                    isLoading={isSubmitting}
                    disabled={
                      isSubmitting ||
                      (formData.ruleType === "threshold" &&
                        (propertiesLoading || availableProperties.length === 0))
                    }
                  >
                    {isSubmitting ? "Creating Rule..." : "Create Rule"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full sm:w-auto"
                    disabled={isSubmitting}
                    onClick={() => {
                      setShowForm(false);
                      resetForm();
                    }}
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
            <CardTitle>All Rules ({rules.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
                <div className="text-center py-8">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto"></div>
                  <p className="mt-2 text-sm text-slate-500">Loading rules...</p>
                </div>
              ) : rules.length === 0 ? (
                <RulesEmptyState canCreateRule={canCreateRule} isPlantScopedRuleRole={isPlantScopedRuleRole} onCreate={() => setShowForm(true)} />
              ) : (
                <>
                  <div className="space-y-3 md:hidden">
                    {rules.map((rule) => (
                      <RuleMobileCard
                        key={rule.ruleId}
                        rule={rule}
                        canCreateRule={canCreateRule}
                        deviceNames={getDeviceNames(rule.deviceIds)}
                        onToggleStatus={(ruleId, status) => void handleToggleStatus(ruleId, status)}
                        onDelete={(ruleId) => void handleDelete(ruleId)}
                      />
                    ))}
                  </div>
                  <div className="hidden md:block">
                    <div className="w-full overflow-x-auto -mx-0">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Rule Name</TableHead>
                            <TableHead>Property</TableHead>
                            <TableHead>Trigger</TableHead>
                            <TableHead>Devices</TableHead>
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
                                    : formatPropertyLabel(rule.property || "N/A")}
                              </TableCell>
                              <TableCell>
                                {getRuleTriggerSummary(rule)}
                              </TableCell>
                              <TableCell>
                                <span className="text-sm text-slate-500">
                                  {getDeviceNames(rule.deviceIds)}
                                </span>
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
                                      onClick={() => handleToggleStatus(rule.ruleId, rule.status)}
                                      className={`text-sm px-3 py-1 rounded transition-colors ${
                                        rule.status === "active"
                                          ? "text-amber-600 hover:bg-amber-50"
                                          : "text-green-600 hover:bg-green-50"
                                      }`}
                                    >
                                      {rule.status === "active" ? "Pause" : "Enable"}
                                    </button>
                                  ) : null}
                                  <Link
                                    href={`/rules/${rule.ruleId}`}
                                    className="text-sm text-blue-600 hover:text-blue-800 px-3 py-1 hover:bg-blue-50 rounded"
                                  >
                                    View
                                  </Link>
                                  {canCreateRule ? (
                                    <button
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
                    </div>
                  </div>
                </>
              )}
          </CardContent>
        </Card>

        <Card className="mt-6">
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
            <div>
              <CardTitle>Alert History</CardTitle>
              <p className="text-sm text-slate-500 mt-1">Rule created, triggered, acknowledged, resolved history</p>
            </div>
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center">
              <Select
                value={selectedAlertDevice}
                onChange={(e) => setSelectedAlertDevice(e.target.value)}
                className="w-full sm:w-64"
                options={[
                  { value: "all", label: allDevicesScopeLabel },
                  ...devices.map((d) => ({ value: d.id, label: `${d.name} (${d.id})` })),
                ]}
              />
              <Button variant="danger" className="w-full sm:w-auto" onClick={handleClearRulesHistory}>
                Clear History
              </Button>
            </div>
          </CardHeader>
          <CardContent>
          {filteredEvents.length === 0 ? (
            <div className="text-center py-10 text-slate-500">
              {isPlantScopedRuleRole
                ? "No alert events found for your accessible devices"
                : "No alert events found"}
            </div>
          ) : (
            <>
              <div className="space-y-3 md:hidden">
                {filteredEvents.map((event) => (
                  <AlertHistoryMobileCard key={event.eventId} event={event} />
                ))}
              </div>
              <div className="hidden md:block">
                <div className="w-full overflow-x-auto -mx-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Time</TableHead>
                        <TableHead>Device</TableHead>
                        <TableHead>Event</TableHead>
                        <TableHead>Title</TableHead>
                        <TableHead>Message</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredEvents.map((event) => (
                        <TableRow key={event.eventId}>
                          <TableCell>{formatIST(event.createdAt, "N/A")}</TableCell>
                          <TableCell className="font-mono text-xs">{event.deviceId || "GLOBAL"}</TableCell>
                          <TableCell className="capitalize">{event.eventType.replace(/_/g, " ")}</TableCell>
                          <TableCell>{event.title}</TableCell>
                          <TableCell className="max-w-md truncate">{event.message}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </>
          )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
