import { colors } from "../constants/colors";
import type { AlertSeverity } from "../api/alerts";
import type { DeviceStatus } from "../api/devices";

export function getStatusColor(status: DeviceStatus | string) {
  const normalized = status.toUpperCase();

  if (normalized === "RUNNING") {
    return colors.running;
  }

  if (normalized === "IDLE") {
    return colors.idle;
  }

  if (normalized === "OFFLINE") {
    return colors.offline;
  }

  return colors.stopped;
}

export function getSeverityColor(severity: AlertSeverity | string) {
  const normalized = severity.toUpperCase();

  if (normalized === "HIGH") {
    return colors.error;
  }

  if (normalized === "LOW") {
    return colors.primary;
  }

  return colors.warning;
}

export function getSeverityEmoji(severity: AlertSeverity | string) {
  const normalized = severity.toUpperCase();

  if (normalized === "HIGH") {
    return "🔴";
  }

  if (normalized === "LOW") {
    return "🔵";
  }

  return "🟠";
}
