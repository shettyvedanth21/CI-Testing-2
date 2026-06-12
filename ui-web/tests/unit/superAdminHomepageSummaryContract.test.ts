import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const homePagePath = path.resolve(__dirname, "../../app/(protected)/page.tsx");
const summaryCardsPath = path.resolve(__dirname, "../../components/home/SuperAdminSummaryCards.tsx");
const authApiPath = path.resolve(__dirname, "../../lib/authApi.ts");

const homePageSource = readFileSync(homePagePath, "utf-8");
const summaryCardsSource = readFileSync(summaryCardsPath, "utf-8");
const authApiSource = readFileSync(authApiPath, "utf-8");

test("homepage mounts the dedicated super-admin summary component", () => {
  assert.equal(homePageSource.includes("SuperAdminSummaryCards"), true);
});

test("super-admin summary component fetches the backend summary contract and gates by role", () => {
  assert.equal(summaryCardsSource.includes('me.user.role !== "super_admin"'), true);
  assert.equal(summaryCardsSource.includes("authApi.getSuperAdminSummary()"), true);
  assert.equal(summaryCardsSource.includes("Total Organisations"), true);
  assert.equal(summaryCardsSource.includes("Active Devices"), true);
  assert.equal(summaryCardsSource.includes("total_organisations"), true);
  assert.equal(summaryCardsSource.includes("total_active_devices"), true);
});

test("auth api exposes the super-admin summary endpoint", () => {
  assert.equal(authApiSource.includes("export interface SuperAdminSummary"), true);
  assert.equal(authApiSource.includes('authFetch<SuperAdminSummary>("/api/admin/summary"'), true);
});
