import test from "node:test";
import assert from "node:assert/strict";

import { buildFleetSnapshotQuery, buildFleetStreamQuery } from "../../lib/fleetQuery.ts";

test("fleet snapshot query forwards device-name search with other filters", () => {
  const query = buildFleetSnapshotQuery({
    page: 2,
    pageSize: 60,
    plantId: "PLANT-1",
    operationalStatus: "running",
    search: "Press Alpha",
  });

  assert.equal(query.get("page"), "2");
  assert.equal(query.get("page_size"), "60");
  assert.equal(query.get("plant_id"), "PLANT-1");
  assert.equal(query.get("operational_status"), "running");
  assert.equal(query.get("search"), "Press Alpha");
});

test("fleet stream query forwards device-name search for reconnectable live updates", () => {
  const query = buildFleetStreamQuery({
    pageSize: 200,
    plantId: "PLANT-2",
    operationalStatus: "idle",
    search: "lathe",
    lastEventId: "17",
  });

  assert.equal(query.get("page_size"), "200");
  assert.equal(query.get("plant_id"), "PLANT-2");
  assert.equal(query.get("operational_status"), "idle");
  assert.equal(query.get("search"), "lathe");
  assert.equal(query.get("last_event_id"), "17");
});
