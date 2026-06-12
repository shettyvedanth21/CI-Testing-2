export type DeviceDetailTabId = "overview" | "telemetry" | "maintenance" | "parameters" | "rules";

export type DeviceDetailTab = {
  id: DeviceDetailTabId;
  label: string;
};

export function getVisibleDeviceDetailTabs(input: {
  isReadOnly: boolean;
  canEditDevice: boolean;
  canCreateRule: boolean;
}): DeviceDetailTab[] {
  const tabs: DeviceDetailTab[] = [
    { id: "overview", label: "Overview" },
    { id: "telemetry", label: "Telemetry" },
    { id: "maintenance", label: "Maintenance Log" },
  ];

  if (input.isReadOnly) {
    return tabs;
  }
  if (input.canEditDevice) {
    tabs.push({ id: "parameters", label: "Parameter Configuration" });
  }
  if (input.canCreateRule) {
    tabs.push({ id: "rules", label: "Configure Rules" });
  }
  return tabs;
}
