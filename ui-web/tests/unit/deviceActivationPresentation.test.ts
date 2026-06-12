import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ActivationTimestampField } from "../../components/devices/ActivationTimestampField.ts";
import { mapBackendDeviceShape } from "../../lib/deviceMapping.ts";
import { formatIST } from "../../lib/utils.ts";

test("device mapping preserves first telemetry timestamp from the backend response", () => {
  const device = mapBackendDeviceShape({
    device_id: "dev-1",
    device_name: "Compressor Line A",
    device_type: "compressor",
    device_id_class: "active",
    plant_id: "plant-1",
    data_source_type: "metered",
    status: "active",
    location: "Building A",
    runtime_status: "running",
    first_telemetry_timestamp: "2026-04-10T05:15:00Z",
    last_seen_timestamp: "2026-04-11T05:15:00Z",
  });

  assert.equal(device.first_telemetry_timestamp, "2026-04-10T05:15:00Z");
  assert.equal(device.last_seen_timestamp, "2026-04-11T05:15:00Z");
  assert.notEqual(device.first_telemetry_timestamp, device.last_seen_timestamp);
});

test("activation timestamp field renders the backend timestamp", () => {
  const timestamp = "2026-04-10T05:15:00Z";
  const html = renderToStaticMarkup(
    createElement(ActivationTimestampField, {
      label: "Activated",
      timestamp,
    }),
  );

  assert.match(html, /Activated/);
  assert.match(html, new RegExp(formatIST(timestamp, "Not activated yet").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("activation timestamp field renders a safe fallback when missing", () => {
  const html = renderToStaticMarkup(
    createElement(ActivationTimestampField, {
      label: "Activated",
      timestamp: null,
    }),
  );

  assert.match(html, /Activated/);
  assert.match(html, /Not activated yet/);
});
