import type {
  CurrentPlatformMaintenanceResponse,
  PlatformMaintenanceAnnouncement,
  PlatformMaintenanceAnnouncementWritePayload,
  PlatformMaintenanceSeverity,
  PlatformMaintenanceStatus,
  TenantProfile,
} from "./authApi";

export type PlatformMaintenanceFormState = {
  title: string;
  severity: PlatformMaintenanceSeverity;
  message: string;
  startsAt: string;
  estimatedDurationMinutes: string;
  broadcastAllTenants: boolean;
  targetTenantIds: string[];
  status: PlatformMaintenanceStatus;
};

export type PlatformMaintenanceFormErrors = Partial<
  Record<"title" | "message" | "startsAt" | "estimatedDurationMinutes" | "targetTenantIds", string>
>;

export const PLATFORM_MAINTENANCE_SEVERITY_OPTIONS: Array<{
  value: PlatformMaintenanceSeverity;
  label: string;
  helper: string;
}> = [
  { value: "info", label: "Heads-up", helper: "General advance notice with low urgency." },
  { value: "warning", label: "Important", helper: "Service impact is expected for the selected organisations." },
  { value: "critical", label: "Critical", helper: "High urgency maintenance with strong user attention needed." },
];

export const PLATFORM_MAINTENANCE_STATUS_OPTIONS: Array<{
  value: PlatformMaintenanceStatus;
  label: string;
}> = [
  { value: "draft", label: "Draft" },
  { value: "scheduled", label: "Scheduled" },
  { value: "active", label: "Live now" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

function parsePlatformMaintenanceApiDate(value: string): Date {
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  return new Date(normalized);
}

export function createDefaultPlatformMaintenanceForm(): PlatformMaintenanceFormState {
  return {
    title: "",
    severity: "warning",
    message: "",
    startsAt: "",
    estimatedDurationMinutes: "60",
    broadcastAllTenants: false,
    targetTenantIds: [],
    status: "draft",
  };
}

export function formatPlatformMaintenanceDateTimeInput(value: string | null): string {
  if (!value) {
    return "";
  }

  const date = parsePlatformMaintenanceApiDate(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const offsetMinutes = date.getTimezoneOffset();
  const localDate = new Date(date.getTime() - offsetMinutes * 60_000);
  return localDate.toISOString().slice(0, 16);
}

export function formFromAnnouncement(
  announcement: PlatformMaintenanceAnnouncement,
): PlatformMaintenanceFormState {
  return {
    title: announcement.title,
    severity: announcement.severity,
    message: announcement.message,
    startsAt: formatPlatformMaintenanceDateTimeInput(announcement.starts_at),
    estimatedDurationMinutes: String(announcement.estimated_duration_minutes),
    broadcastAllTenants: announcement.broadcast_all_tenants,
    targetTenantIds: [...announcement.target_tenant_ids],
    status: announcement.effective_status,
  };
}

export function validatePlatformMaintenanceForm(
  form: PlatformMaintenanceFormState,
  availableTenants: TenantProfile[],
): PlatformMaintenanceFormErrors {
  const errors: PlatformMaintenanceFormErrors = {};

  if (!form.title.trim()) {
    errors.title = "Enter a clear title for this maintenance notice.";
  }

  if (!form.message.trim()) {
    errors.message = "Add a short message that explains what people should expect.";
  }

  if (!form.startsAt.trim()) {
    errors.startsAt = "Choose when the maintenance window begins.";
  } else if (Number.isNaN(new Date(form.startsAt).getTime())) {
    errors.startsAt = "Enter a valid start time.";
  }

  const duration = Number.parseInt(form.estimatedDurationMinutes, 10);
  if (!Number.isFinite(duration) || duration <= 0) {
    errors.estimatedDurationMinutes = "Enter a duration in minutes greater than zero.";
  }

  if (!form.broadcastAllTenants) {
    if (form.targetTenantIds.length === 0) {
      errors.targetTenantIds = "Choose at least one organisation.";
    } else {
      const availableIds = new Set(availableTenants.map((tenant) => tenant.id));
      const invalidIds = form.targetTenantIds.filter((tenantId) => !availableIds.has(tenantId));
      if (invalidIds.length > 0) {
        errors.targetTenantIds = "One or more selected organisations are no longer available.";
      } else {
        const inactiveSelected = availableTenants.filter(
          (tenant) => form.targetTenantIds.includes(tenant.id) && !tenant.is_active,
        );
        if (inactiveSelected.length > 0) {
          errors.targetTenantIds = "Remove suspended organisations before saving this notice.";
        }
      }
    }
  }

  return errors;
}

export function buildPlatformMaintenancePayload(
  form: PlatformMaintenanceFormState,
): PlatformMaintenanceAnnouncementWritePayload {
  return {
    title: form.title.trim(),
    severity: form.severity,
    message: form.message.trim(),
    starts_at: new Date(form.startsAt).toISOString(),
    estimated_duration_minutes: Number.parseInt(form.estimatedDurationMinutes, 10),
    status: form.status,
    broadcast_all_tenants: form.broadcastAllTenants,
    target_tenant_ids: form.broadcastAllTenants ? [] : [...new Set(form.targetTenantIds)],
  };
}

export function formatPlatformMaintenanceDuration(minutes: number): string {
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (!remainingMinutes) {
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  return `${hours}h ${remainingMinutes}m`;
}

export function getPlatformMaintenanceAudienceSummary(
  form: PlatformMaintenanceFormState,
  tenants: TenantProfile[],
): string {
  if (form.broadcastAllTenants) {
    return tenants.length > 0
      ? `This notice will reach every organisation in the directory (${tenants.length} total).`
      : "This notice is set to reach all organisations.";
  }

  if (form.targetTenantIds.length === 0) {
    return "Choose one or more organisations for this notice.";
  }

  const selectedTenants = tenants.filter((tenant) => form.targetTenantIds.includes(tenant.id));
  if (selectedTenants.length === 1) {
    return `1 organisation selected: ${selectedTenants[0]?.name ?? "Selected organisation"}.`;
  }

  return `${selectedTenants.length} organisations selected.`;
}

export function getPlatformMaintenanceSeverityBadgeVariant(
  severity: PlatformMaintenanceSeverity,
): "info" | "warning" | "critical" {
  if (severity === "critical") {
    return "critical";
  }
  return severity === "warning" ? "warning" : "info";
}

export function getPlatformMaintenanceStatusBadgeVariant(
  status: PlatformMaintenanceStatus,
): "default" | "success" | "warning" | "error" | "info" {
  switch (status) {
    case "active":
      return "success";
    case "scheduled":
      return "info";
    case "cancelled":
      return "error";
    case "completed":
      return "default";
    case "draft":
    default:
      return "warning";
  }
}

export function getPlatformMaintenanceStatusLabel(status: PlatformMaintenanceStatus): string {
  return PLATFORM_MAINTENANCE_STATUS_OPTIONS.find((option) => option.value === status)?.label ?? status;
}

export function toLocalDateTimeInputValue(date: Date): string {
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const offsetMinutes = date.getTimezoneOffset();
  const localDate = new Date(date.getTime() - offsetMinutes * 60_000);
  return localDate.toISOString().slice(0, 16);
}

export function chooseVisiblePlatformMaintenanceAnnouncements(
  items: CurrentPlatformMaintenanceResponse["announcements"],
): PlatformMaintenanceAnnouncement[] {
  return [...items]
    .sort((left, right) => {
      const leftPriority = left.effective_status === "active" ? 0 : 1;
      const rightPriority = right.effective_status === "active" ? 0 : 1;
      if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
      }
      return new Date(left.starts_at).getTime() - new Date(right.starts_at).getTime();
    })
    .slice(0, 2);
}
