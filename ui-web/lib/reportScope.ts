export type ReportScopedRole = "super_admin" | "org_admin" | "plant_manager" | "operator" | "viewer" | string;

export function isPlantScopedReportRole(role: ReportScopedRole | null | undefined): boolean {
  return role === "plant_manager" || role === "operator" || role === "viewer";
}

export function getReportScopeLabel(role: ReportScopedRole | null | undefined): string {
  return isPlantScopedReportRole(role) ? "All Accessible Devices" : "All Devices";
}

export function getReportScopeHint(role: ReportScopedRole | null | undefined): string | null {
  return isPlantScopedReportRole(role)
    ? "Report generation, history, and schedules are limited to devices from your assigned plants."
    : null;
}

export function getReportPageSubtitle(role: ReportScopedRole | null | undefined): string {
  return isPlantScopedReportRole(role)
    ? "Generate and review energy reports for your accessible plants"
    : "Generate and analyze energy reports";
}

export function getEmptyReportHistoryMessage(role: ReportScopedRole | null | undefined): string {
  return isPlantScopedReportRole(role)
    ? "No reports found for your accessible devices"
    : "No reports generated yet";
}

export function getEmptyScheduleMessage(role: ReportScopedRole | null | undefined): string {
  return isPlantScopedReportRole(role)
    ? "No schedules configured for your accessible devices"
    : "No schedules configured yet";
}
