export interface BackendDeviceShape {
  device_id: string;
  device_name: string;
  device_type: string;
  device_id_class?: "active" | "test" | "virtual" | null;
  plant_id?: string | null;
  data_source_type?: "metered" | "sensor";
  status: string;
  location: string | null;
  runtime_status: string;
  first_telemetry_timestamp: string | null;
  last_seen_timestamp: string | null;
}

export interface DeviceShape {
  id: string;
  name: string;
  type: string;
  device_id_class?: "active" | "test" | "virtual" | null;
  plant_id?: string | null;
  data_source_type?: "metered" | "sensor";
  status: string;
  runtime_status: string;
  first_telemetry_timestamp: string | null;
  last_seen_timestamp: string | null;
  location: string;
}

export function mapBackendDeviceShape(d: BackendDeviceShape): DeviceShape {
  return {
    id: d.device_id,
    name: d.device_name,
    type: d.device_type,
    device_id_class: d.device_id_class ?? null,
    plant_id: d.plant_id ?? null,
    data_source_type: d.data_source_type,
    status: d.status,
    runtime_status: d.runtime_status || "stopped",
    first_telemetry_timestamp: d.first_telemetry_timestamp,
    last_seen_timestamp: d.last_seen_timestamp,
    location: d.location ?? "",
  };
}
