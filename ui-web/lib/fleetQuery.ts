import type { DeviceOperationalStatus } from "./deviceStatus";

export function buildFleetSnapshotQuery(args: {
  page: number;
  pageSize: number;
  plantId?: string | null;
  operationalStatus?: DeviceOperationalStatus | null;
  search?: string | null;
}): URLSearchParams {
  const query = new URLSearchParams({
    page: String(args.page),
    page_size: String(args.pageSize),
  });
  if (args.plantId) {
    query.set("plant_id", args.plantId);
  }
  if (args.operationalStatus) {
    query.set("operational_status", args.operationalStatus);
  }
  if (args.search) {
    query.set("search", args.search);
  }
  return query;
}

export function buildFleetStreamQuery(args: {
  pageSize?: number;
  runtimeStatus?: "running" | "stopped";
  operationalStatus?: DeviceOperationalStatus;
  plantId?: string | null;
  search?: string;
  lastEventId?: string;
}): URLSearchParams {
  const query = new URLSearchParams({
    page_size: String(args.pageSize ?? 200),
  });
  if (args.runtimeStatus) {
    query.append("runtime_status", args.runtimeStatus);
  }
  if (args.operationalStatus) {
    query.append("operational_status", args.operationalStatus);
  }
  if (args.plantId) {
    query.append("plant_id", args.plantId);
  }
  if (args.search) {
    query.append("search", args.search);
  }
  if (args.lastEventId) {
    query.append("last_event_id", args.lastEventId);
  }
  return query;
}
