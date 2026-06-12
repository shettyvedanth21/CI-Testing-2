import type { DeviceScopeCatalog, DeviceScopeSelection } from "./deviceScopeSelection.ts";
import {
  normalizeDeviceScopeSelection,
  resolveDeviceIdsForSelection,
} from "./deviceScopeSelection.ts";
import type { WasteGranularity, WasteRunParams, WasteScope } from "./wasteApi.ts";

export interface WasteAnalysisFormValues {
  job_name?: string;
  start_date: string;
  end_date: string;
  granularity: WasteGranularity;
}

export function buildWasteRunParams(
  formValues: WasteAnalysisFormValues,
  selection: DeviceScopeSelection,
  catalog: DeviceScopeCatalog,
): WasteRunParams {
  const normalizedSelection = normalizeDeviceScopeSelection(selection, catalog);
  const resolvedDeviceIds = resolveDeviceIdsForSelection(normalizedSelection, catalog);
  const scope: WasteScope = normalizedSelection.mode === "all" ? "all" : "selected";

  return {
    job_name: formValues.job_name,
    scope,
    device_ids: scope === "all" ? null : resolvedDeviceIds,
    start_date: formValues.start_date,
    end_date: formValues.end_date,
    granularity: formValues.granularity,
  };
}
