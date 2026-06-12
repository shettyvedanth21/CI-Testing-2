import test from "node:test";
import assert from "node:assert/strict";

import {
  buildNotificationUsageExportPath,
  buildNotificationUsageQuery,
  buildNotificationUsageRequestKey,
  buildSummaryCards,
  formatNotificationFailureReason,
  getCurrentMonthLocal,
  getNotificationUsageErrorMessage,
  shouldShowNotificationUsageEmptyState,
  type NotificationUsageSummaryResponse,
} from "../../lib/adminNotificationUsage.ts";
import { buildAdminOrgTabs } from "../../lib/hardwareAdmin.ts";

test("super admin org tabs include Notification Usage while non-super-admin tabs do not", () => {
  const superAdminTabs = buildAdminOrgTabs({
    plants: 2,
    users: 1,
    hardware: 3,
    includeNotificationUsage: true,
    notificationUsage: 0,
  });
  const nonSuperAdminTabs = buildAdminOrgTabs({
    plants: 2,
    users: 1,
    hardware: 3,
    includeNotificationUsage: false,
  });

  assert.equal(superAdminTabs.some((tab) => tab.key === "notification_usage"), true);
  assert.equal(nonSuperAdminTabs.some((tab) => tab.key === "notification_usage"), false);
});

test("summary cards map backend contract counts for billing-focused UI cards", () => {
  const payload: NotificationUsageSummaryResponse = {
    success: true,
    tenant_id: "SH00000001",
    month: "2026-04",
    totals: {
      attempted_count: 18,
      accepted_count: 14,
      delivered_count: 11,
      failed_count: 3,
      skipped_count: 1,
      billable_count: 14,
    },
    by_channel: {
      email: { attempted_count: 6, accepted_count: 5, delivered_count: 4, failed_count: 1, skipped_count: 0, billable_count: 5 },
      sms: { attempted_count: 8, accepted_count: 6, delivered_count: 5, failed_count: 2, skipped_count: 0, billable_count: 6 },
      whatsapp: { attempted_count: 4, accepted_count: 3, delivered_count: 2, failed_count: 0, skipped_count: 1, billable_count: 3 },
    },
    first_attempt_at: "2026-04-01T00:00:00Z",
    last_attempt_at: "2026-04-29T23:00:00Z",
  };

  const cards = buildSummaryCards(payload);
  const byLabel = new Map(cards.map((card) => [card.label, card.value]));

  assert.equal(byLabel.get("SMS Billable"), "6");
  assert.equal(byLabel.get("WhatsApp Billable"), "3");
  assert.equal(byLabel.get("Email Billable"), "5");
  assert.equal(byLabel.get("Total Billable"), "14");
  assert.equal(byLabel.get("Failed"), "3");
  assert.equal(byLabel.get("Attempted"), "18");
});

test("month key and request key change when month changes to trigger refetch", () => {
  assert.equal(getCurrentMonthLocal(new Date("2026-04-16T10:00:00+05:30")), "2026-04");
  const aprilKey = buildNotificationUsageRequestKey("SH00000001", "2026-04", { page: 1, pageSize: 50 });
  const mayKey = buildNotificationUsageRequestKey("SH00000001", "2026-05", { page: 1, pageSize: 50 });
  assert.notEqual(aprilKey, mayKey);
});

test("logs filters are encoded cleanly in query params", () => {
  const query = buildNotificationUsageQuery("2026-04", {
    channel: "sms",
    status: "failed",
    ruleId: "rule-123",
    deviceId: "DEV-9",
    search: "MSG-8899",
    page: 2,
    pageSize: 100,
  });

  assert.equal(query.get("month"), "2026-04");
  assert.equal(query.get("channel"), "sms");
  assert.equal(query.get("status"), "failed");
  assert.equal(query.get("rule_id"), "rule-123");
  assert.equal(query.get("device_id"), "DEV-9");
  assert.equal(query.get("search"), "MSG-8899");
  assert.equal(query.get("page"), "2");
  assert.equal(query.get("page_size"), "100");
});

test("empty-state and failure-state helper semantics are explicit", () => {
  assert.equal(
    shouldShowNotificationUsageEmptyState({
      success: true,
      tenant_id: "SH00000001",
      month: "2026-04",
      page: 1,
      page_size: 50,
      total: 0,
      data: [],
    }),
    true,
  );
  assert.equal(
    getNotificationUsageErrorMessage(new Error("Backend unavailable")),
    "Backend unavailable",
  );
  assert.equal(
    formatNotificationFailureReason("63016", "Template rejected"),
    "63016: Template rejected",
  );
});

test("csv export path keeps selected month and active filters", () => {
  const path = buildNotificationUsageExportPath("SH00000001", "2026-04", {
    channel: "whatsapp",
    status: "failed",
    ruleId: "rule-w1",
    deviceId: "DEV-2",
    search: "MSG-WA",
    page: 3,
    pageSize: 25,
  });

  assert.equal(path.includes("/api/v1/admin/notification-usage/SH00000001/export.csv"), true);
  assert.equal(path.includes("month=2026-04"), true);
  assert.equal(path.includes("channel=whatsapp"), true);
  assert.equal(path.includes("status=failed"), true);
  assert.equal(path.includes("rule_id=rule-w1"), true);
  assert.equal(path.includes("device_id=DEV-2"), true);
  assert.equal(path.includes("search=MSG-WA"), true);
});
