import test from "node:test";
import assert from "node:assert/strict";

import { getPermissionsForRole } from "../../lib/permissions.ts";

test("operator can create rules but cannot manage devices", () => {
  const permissions = getPermissionsForRole("operator");

  assert.equal(permissions.canCreateRule, true);
  assert.equal(permissions.canAcknowledgeAlert, true);
  assert.equal(permissions.canCreateDevice, false);
  assert.equal(permissions.canEditDevice, false);
  assert.equal(permissions.canDeleteDevice, false);
  assert.equal(permissions.isReadOnly, false);
});

test("viewer remains read-only without rule creation access", () => {
  const permissions = getPermissionsForRole("viewer");

  assert.equal(permissions.canCreateRule, false);
  assert.equal(permissions.canAcknowledgeAlert, false);
  assert.equal(permissions.isReadOnly, true);
});

test("plant manager retains rule creation access", () => {
  const permissions = getPermissionsForRole("plant_manager");

  assert.equal(permissions.canCreateRule, true);
  assert.equal(permissions.canCreateDevice, true);
});
