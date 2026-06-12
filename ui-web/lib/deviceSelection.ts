export function normalizeSelectedDeviceIds(
  selectedIds: string[],
  availableIds: string[],
): string[] {
  if (selectedIds.length === 0 || availableIds.length === 0) {
    return [];
  }
  const available = new Set(availableIds);
  return selectedIds.filter((deviceId) => available.has(deviceId));
}

export function areAllSelectableDevicesSelected(
  selectedIds: string[],
  availableIds: string[],
): boolean {
  if (availableIds.length === 0) {
    return false;
  }
  const normalized = normalizeSelectedDeviceIds(selectedIds, availableIds);
  return normalized.length === availableIds.length;
}
