import type { MaintenanceLogMutationInput, MaintenanceLogRecord } from "./deviceApi.ts";

export function formatMaintenanceDate(value: string | null | undefined, emptyText = "Not scheduled"): string {
  if (!value) return emptyText;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return emptyText;
  return date.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function truncateDescription(value: string, maxLength = 132): string {
  const normalized = value.trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

export function formatMaintenanceCostInput(value: string): string {
  return value.replace(/[^\d.]/g, "");
}

export function normalizeMaintenanceApiError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("maintenance_log_validation_error") || normalized.includes("next_due_date")) {
    return "Choose a next due date that is the same as or later than the maintenance date.";
  }
  if (normalized.includes("device_not_found")) {
    return "This machine could not be found anymore. Please refresh the page.";
  }
  if (normalized.includes("maintenance_log_not_found")) {
    return "This maintenance record is no longer available. Please refresh the list.";
  }
  if (normalized.includes("forbidden")) {
    return "You do not have permission to change maintenance records for this machine.";
  }
  return message;
}

export type MaintenanceLogFormValues = {
  maintenance_date: string;
  title: string;
  description: string;
  cost: string;
  performed_by: string;
  status: string;
  next_due_date: string;
};

export const MAINTENANCE_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "No status" },
  { value: "completed", label: "Completed" },
  { value: "scheduled", label: "Scheduled" },
  { value: "in_progress", label: "In progress" },
  { value: "follow_up_required", label: "Follow-up required" },
];

export function buildMaintenanceFormValues(record: MaintenanceLogRecord | null): MaintenanceLogFormValues {
  if (!record) {
    return {
      maintenance_date: "",
      title: "",
      description: "",
      cost: "",
      performed_by: "",
      status: "",
      next_due_date: "",
    };
  }
  return {
    maintenance_date: record.maintenance_date ?? "",
    title: record.title ?? "",
    description: record.description ?? "",
    cost: record.cost != null ? record.cost.toFixed(2) : "",
    performed_by: record.performed_by ?? "",
    status: record.status ?? "",
    next_due_date: record.next_due_date ?? "",
  };
}

export function validateMaintenanceForm(values: MaintenanceLogFormValues): {
  payload: MaintenanceLogMutationInput | null;
  error: string | null;
} {
  const maintenanceDate = values.maintenance_date.trim();
  const title = values.title.trim();
  const description = values.description.trim();
  const costText = values.cost.trim();
  const performedBy = values.performed_by.trim();
  const status = values.status.trim();
  const nextDueDate = values.next_due_date.trim();

  if (!maintenanceDate) {
    return { payload: null, error: "Choose the maintenance date." };
  }
  if (!title) {
    return { payload: null, error: "Enter a short issue title so this record is easy to recognise." };
  }
  if (!description) {
    return { payload: null, error: "Add a few notes about what was done." };
  }
  if (!costText) {
    return { payload: null, error: "Enter the maintenance cost." };
  }
  if (!/^\d+(?:\.\d{1,2})?$/.test(costText)) {
    return { payload: null, error: "Enter the cost as a valid amount, for example 1250 or 1250.50." };
  }

  const cost = Number(costText);
  if (!Number.isFinite(cost) || cost < 0) {
    return { payload: null, error: "Enter a cost that is zero or greater." };
  }
  if (nextDueDate && nextDueDate < maintenanceDate) {
    return { payload: null, error: "Choose a next due date that is the same as or later than the maintenance date." };
  }

  return {
    payload: {
      maintenance_date: maintenanceDate,
      title,
      description,
      cost,
      performed_by: performedBy || null,
      status: status || null,
      next_due_date: nextDueDate || null,
    },
    error: null,
  };
}
