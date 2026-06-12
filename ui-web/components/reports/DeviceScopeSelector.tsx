"use client";

import type { DeviceScopeCatalog, DeviceScopeMode, DeviceScopeSelection } from "@/lib/deviceScopeSelection";

interface DeviceScopeSelectorProps {
  catalog: DeviceScopeCatalog;
  value: DeviceScopeSelection;
  onChange: (selection: DeviceScopeSelection) => void;
  disabled?: boolean;
  helperText?: string | null;
  allModeTitle?: string;
}

const MODE_COPY: Array<{
  id: DeviceScopeMode;
  title: string;
  description: string;
}> = [
  {
    id: "all",
    title: "All Machines",
    description: "Run across every accessible machine.",
  },
  {
    id: "plant",
    title: "Plants",
    description: "Pick one plant and include all accessible machines in it.",
  },
  {
    id: "devices",
    title: "Select Machines",
    description: "Pick one or more individual machines.",
  },
];

export function DeviceScopeSelector({
  catalog,
  value,
  onChange,
  disabled = false,
  helperText = null,
  allModeTitle = "All Machines",
}: DeviceScopeSelectorProps) {
  const totalDeviceCount = catalog.allDeviceIds.length;

  const handleModeChange = (mode: DeviceScopeMode) => {
    if (mode === "all") {
      onChange({ mode, plantId: null, deviceIds: [] });
      return;
    }
    if (mode === "plant") {
      onChange({
        mode,
        plantId: value.plantId ?? catalog.plantOptions[0]?.id ?? null,
        deviceIds: [],
      });
      return;
    }
    onChange({
      mode,
      plantId: null,
      deviceIds: value.deviceIds,
    });
  };

  const handlePlantSelect = (plantId: string) => {
    onChange({
      mode: "plant",
      plantId,
      deviceIds: [],
    });
  };

  const handleDeviceToggle = (deviceId: string, checked: boolean) => {
    const selected = new Set(value.deviceIds);
    if (checked) {
      selected.add(deviceId);
    } else {
      selected.delete(deviceId);
    }
    onChange({
      mode: "devices",
      plantId: null,
      deviceIds: Array.from(selected),
    });
  };

  return (
    <div className="space-y-4" data-testid="device-scope-selector">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {MODE_COPY.map((mode) => {
          const active = value.mode === mode.id;
          const title = mode.id === "all" ? allModeTitle : mode.title;
          return (
            <button
              key={mode.id}
              type="button"
              onClick={() => handleModeChange(mode.id)}
              disabled={disabled}
              data-testid={`device-scope-mode-${mode.id}`}
              className={`min-h-24 rounded-xl border px-4 py-3 text-left transition ${
                active
                  ? "border-blue-500 bg-blue-50 text-blue-950"
                  : "border-slate-200 bg-white text-slate-900 hover:border-slate-300"
              } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <div className="text-sm font-semibold">{title}</div>
              <div className="mt-1 text-xs text-slate-600">{mode.description}</div>
              {mode.id === "all" ? (
                <div className="mt-3 inline-flex rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">
                  {totalDeviceCount} accessible
                </div>
              ) : null}
            </button>
          );
        })}
      </div>

      {helperText ? (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {helperText}
        </p>
      ) : null}

      {value.mode === "plant" ? (
        <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3 sm:p-4" data-testid="device-scope-plant-panel">
          {catalog.plantOptions.map((plant) => {
            const active = value.plantId === plant.id;
            return (
              <button
                key={plant.id}
                type="button"
                onClick={() => handlePlantSelect(plant.id)}
                disabled={disabled || plant.deviceCount === 0}
                data-testid="device-scope-plant-option"
                className={`flex w-full flex-col items-start gap-2 rounded-lg border px-3 py-3 text-left transition sm:flex-row sm:items-center sm:justify-between ${
                  active
                    ? "border-blue-500 bg-blue-50 text-blue-950"
                    : "border-slate-200 bg-white text-slate-900 hover:border-slate-300"
                } ${(disabled || plant.deviceCount === 0) ? "cursor-not-allowed opacity-60" : ""}`}
              >
                <div>
                  <div className="text-sm font-medium">{plant.name}</div>
                  <div className="mt-1 text-xs text-slate-500">{plant.label}</div>
                </div>
                <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">
                  {plant.deviceCount}
                </span>
              </button>
            );
          })}
          {catalog.plantOptions.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500" data-testid="device-scope-plant-empty">No accessible plants found.</p>
          ) : null}
        </div>
      ) : null}

      {value.mode === "devices" ? (
        <div className="max-h-72 space-y-2 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3 sm:p-4" data-testid="device-scope-device-panel">
          {catalog.deviceOptions.map((device) => (
            <label
              key={device.id}
              data-testid="device-scope-device-option"
              className="flex min-h-20 cursor-pointer items-start gap-3 rounded-lg border border-slate-200 bg-white px-3 py-3"
            >
              <input
                type="checkbox"
                checked={value.deviceIds.includes(device.id)}
                onChange={(event) => handleDeviceToggle(device.id, event.target.checked)}
                disabled={disabled}
                className="mt-0.5 h-4 w-4 rounded border-slate-300"
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-slate-900" data-testid="device-scope-device-label">{device.label}</span>
                <span className="mt-1 block text-xs text-slate-500" data-testid="device-scope-device-description">{device.description}</span>
              </span>
            </label>
          ))}
          {catalog.deviceOptions.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500" data-testid="device-scope-device-empty">No accessible machines found.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
