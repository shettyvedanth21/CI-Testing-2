import type { ScheduleParams } from "./reportApi.ts";
import {
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
  type DeviceScopeCatalog,
  type DeviceScopeSelection,
} from "./deviceScopeSelection.ts";

export interface ReportScheduleFormValues {
  report_type: "consumption" | "comparison";
  frequency: "daily" | "weekly" | "monthly";
  group_by: "daily" | "weekly";
}

export function buildReportScheduleParams(
  formValues: ReportScheduleFormValues,
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): ScheduleParams {
  const normalizedSelection = normalizeDeviceScopeSelection(selection, catalog);
  const deviceIds = resolveDeviceIdsForSelection(normalizedSelection, catalog);

  return {
    report_type: formValues.report_type,
    frequency: formValues.frequency,
    params_template: {
      device_ids: deviceIds,
      group_by: formValues.group_by,
    },
  };
}
