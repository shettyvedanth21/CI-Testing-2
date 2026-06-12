import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const machinesPagePath = path.resolve(__dirname, "../../app/(protected)/machines/page.tsx");
const deviceApiPath = path.resolve(__dirname, "../../lib/deviceApi.ts");
const machinesPageSource = readFileSync(machinesPagePath, "utf-8");
const deviceApiSource = readFileSync(deviceApiPath, "utf-8");

test("machines summary reads health configuration counts from dashboard summary contract", () => {
  assert.equal(
    machinesPageSource.includes("devices_with_health_configured"),
    true,
  );
  assert.equal(
    machinesPageSource.includes("devices_missing_health_config"),
    true,
  );
  assert.equal(
    machinesPageSource.includes("Configured: ${configuredHealthCount}/${machines.length}"),
    false,
  );
});

test("dashboard summary parsing supports stable health configuration counts", () => {
  assert.equal(
    deviceApiSource.includes("devices_with_health_configured"),
    true,
  );
  assert.equal(
    deviceApiSource.includes("devices_missing_health_config"),
    true,
  );
});
