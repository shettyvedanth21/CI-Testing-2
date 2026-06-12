import type { DeviceOperationalStatus } from "./deviceStatus";

export type MachinesFilterState = {
  plantId: string | null;
  operationalStatus: DeviceOperationalStatus | "all";
  search: string;
};

export function normalizeMachinesSearchInput(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function buildMachinesFilterKey(filters: MachinesFilterState): string {
  return JSON.stringify(filters);
}

export function shouldResetMachinesPage(args: {
  currentPage: number;
  previousFilterKey: string;
  nextFilterKey: string;
}): boolean {
  return args.currentPage !== 1 && args.previousFilterKey !== args.nextFilterKey;
}

export function getMachinesEmptyStateCopy(args: {
  search: string;
  hasPlantFilter: boolean;
  hasOperationalStatusFilter: boolean;
}): { title: string; message: string } {
  if (args.search) {
    return {
      title: "No machines match your search",
      message: `No devices match "${args.search}". Try a different device name or clear the search.`,
    };
  }

  if (args.hasPlantFilter || args.hasOperationalStatusFilter) {
    return {
      title: "No machines match the current filters",
      message: "Try a different plant or operational status filter.",
    };
  }

  return {
    title: "No machines found",
    message: "Get started by adding your first machine to the platform.",
  };
}
