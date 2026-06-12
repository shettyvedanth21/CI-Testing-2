import assert from "node:assert/strict";
import test from "node:test";

import {
  chooseVisiblePlatformMaintenanceAnnouncements,
  validatePlatformMaintenanceForm,
  type PlatformMaintenanceFormState,
} from "../../lib/platformMaintenance.ts";


const baseForm: PlatformMaintenanceFormState = {
  title: "Planned maintenance",
  severity: "warning",
  message: "A short maintenance notice.",
  startsAt: "2026-05-01T18:00",
  estimatedDurationMinutes: "60",
  broadcastAllTenants: false,
  targetTenantIds: [],
  status: "scheduled",
};


test("platform maintenance form rejects suspended target organisations", () => {
  const errors = validatePlatformMaintenanceForm(
    {
      ...baseForm,
      targetTenantIds: ["SH00000002"],
    },
    [
      { id: "SH00000002", name: "Suspended Org", slug: "suspended-org", is_active: false, created_at: "2026-04-01T00:00:00Z" },
    ],
  );

  assert.equal(errors.targetTenantIds, "Remove suspended organisations before saving this notice.");
});


test("banner helper prioritizes active notices and limits the list to two items", () => {
  const visible = chooseVisiblePlatformMaintenanceAnnouncements([
    {
      id: "scheduled-2",
      title: "Scheduled later",
      severity: "warning",
      message: "Later message",
      starts_at: "2026-05-01T14:00:00Z",
      estimated_duration_minutes: 30,
      ends_at: "2026-05-01T14:30:00Z",
      status: "scheduled",
      effective_status: "scheduled",
      broadcast_all_tenants: true,
      target_tenant_ids: [],
      created_by: "super-1",
      updated_by: "super-1",
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    },
    {
      id: "active-1",
      title: "Live now",
      severity: "critical",
      message: "Active message",
      starts_at: "2026-05-01T12:00:00Z",
      estimated_duration_minutes: 60,
      ends_at: "2026-05-01T13:00:00Z",
      status: "scheduled",
      effective_status: "active",
      broadcast_all_tenants: false,
      target_tenant_ids: ["SH00000001"],
      created_by: "super-1",
      updated_by: "super-1",
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    },
    {
      id: "scheduled-1",
      title: "Scheduled sooner",
      severity: "info",
      message: "Soon message",
      starts_at: "2026-05-01T13:00:00Z",
      estimated_duration_minutes: 30,
      ends_at: "2026-05-01T13:30:00Z",
      status: "scheduled",
      effective_status: "scheduled",
      broadcast_all_tenants: false,
      target_tenant_ids: ["SH00000001"],
      created_by: "super-1",
      updated_by: "super-1",
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    },
  ]);

  assert.deepEqual(visible.map((item) => item.id), ["active-1", "scheduled-1"]);
});
