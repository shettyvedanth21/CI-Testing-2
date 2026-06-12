import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMaintenanceFormValues,
  formatMaintenanceCostInput,
  normalizeMaintenanceApiError,
  truncateDescription,
  validateMaintenanceForm,
} from "../../lib/maintenanceLog.ts";

test("maintenance form validation accepts zero cost and clears optional blanks", () => {
  const result = validateMaintenanceForm({
    maintenance_date: "2026-04-24",
    title: "  Belt check  ",
    description: "  Tightened drive belt and verified alignment.  ",
    cost: "0",
    performed_by: "   ",
    status: "   ",
    next_due_date: "",
  });

  assert.equal(result.error, null);
  assert.deepEqual(result.payload, {
    maintenance_date: "2026-04-24",
    title: "Belt check",
    description: "Tightened drive belt and verified alignment.",
    cost: 0,
    performed_by: null,
    status: null,
    next_due_date: null,
  });
});

test("maintenance form validation rejects invalid next due date ordering", () => {
  const result = validateMaintenanceForm({
    maintenance_date: "2026-04-24",
    title: "Inspection",
    description: "Checked motor housing.",
    cost: "120.50",
    performed_by: "",
    status: "",
    next_due_date: "2026-04-23",
  });

  assert.equal(result.payload, null);
  assert.equal(result.error, "Choose a next due date that is the same as or later than the maintenance date.");
});

test("maintenance form validation rejects malformed cost input", () => {
  const result = validateMaintenanceForm({
    maintenance_date: "2026-04-24",
    title: "Inspection",
    description: "Checked motor housing.",
    cost: "12.999",
    performed_by: "",
    status: "",
    next_due_date: "",
  });

  assert.equal(result.payload, null);
  assert.equal(result.error, "Enter the cost as a valid amount, for example 1250 or 1250.50.");
});

test("maintenance helpers format and normalize values for older records", () => {
  assert.equal(formatMaintenanceCostInput("INR 1,250.50"), "1250.50");
  assert.equal(
    truncateDescription("   This is a longer maintenance note that should be shortened for list display.   ", 38),
    "This is a longer maintenance note tha…",
  );
  assert.equal(
    normalizeMaintenanceApiError("MAINTENANCE_LOG_NOT_FOUND"),
    "This maintenance record is no longer available. Please refresh the list.",
  );

  assert.deepEqual(
    buildMaintenanceFormValues({
      id: 8,
      tenant_id: "SH00000001",
      device_id: "M-200",
      maintenance_date: "2026-04-22",
      title: "Filter change",
      description: "Changed filter and cleared debris.",
      cost: 450,
      performed_by: null,
      status: null,
      next_due_date: null,
      created_by: null,
      created_at: "2026-04-22T10:00:00Z",
      updated_at: "2026-04-22T10:00:00Z",
    }),
    {
      maintenance_date: "2026-04-22",
      title: "Filter change",
      description: "Changed filter and cleared debris.",
      cost: "450.00",
      performed_by: "",
      status: "",
      next_due_date: "",
    },
  );
});
