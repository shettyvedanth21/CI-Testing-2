import test from "node:test";
import assert from "node:assert/strict";

import {
  ACTIVE_HIDDEN_STATUS_POLL_MS,
  ACTIVE_PENDING_STATUS_POLL_MS,
  ACTIVE_RUNNING_STATUS_POLL_MS,
  HISTORY_REFRESH_HIDDEN_MS,
  HISTORY_REFRESH_MULTI_LIVE_JOB_MS,
  HISTORY_REFRESH_SINGLE_LIVE_JOB_MS,
  HISTORY_REFRESH_WITH_ACTIVE_JOB_MS,
  countLiveAnalyticsJobs,
  getAnalyticsHistoryRefreshMs,
  getAnalyticsStatusPollMs,
  mergeHistoryJobStatus,
  resolveSelectedAnalyticsJobId,
} from "../../lib/analyticsHistoryPolling.ts";

test("countLiveAnalyticsJobs counts pending and running history rows only", () => {
  const liveCount = countLiveAnalyticsJobs([
    { job_id: "job-1", status: "pending" },
    { job_id: "job-2", status: "running" },
    { job_id: "job-3", status: "completed" },
    { job_id: "job-4", status: "failed" },
  ]);

  assert.equal(liveCount, 2);
});

test("getAnalyticsStatusPollMs prefers faster active-job polling while visible and slower while hidden", () => {
  assert.equal(getAnalyticsStatusPollMs({ job_id: "job-1", status: "running" }, false), ACTIVE_RUNNING_STATUS_POLL_MS);
  assert.equal(getAnalyticsStatusPollMs({ job_id: "job-2", status: "pending" }, false), ACTIVE_PENDING_STATUS_POLL_MS);
  assert.equal(getAnalyticsStatusPollMs({ job_id: "job-3", status: "running" }, true), ACTIVE_HIDDEN_STATUS_POLL_MS);
  assert.equal(getAnalyticsStatusPollMs({ job_id: "job-4", status: "completed" }, false), null);
});

test("getAnalyticsHistoryRefreshMs backs off full-history refreshes when active job truth is already available", () => {
  assert.equal(
    getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount: 1,
      activeJobStatus: { job_id: "job-1", status: "running" },
      isDocumentHidden: false,
    }),
    HISTORY_REFRESH_WITH_ACTIVE_JOB_MS,
  );
  assert.equal(
    getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount: 1,
      activeJobStatus: null,
      isDocumentHidden: false,
    }),
    HISTORY_REFRESH_SINGLE_LIVE_JOB_MS,
  );
  assert.equal(
    getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount: 2,
      activeJobStatus: null,
      isDocumentHidden: false,
    }),
    HISTORY_REFRESH_MULTI_LIVE_JOB_MS,
  );
  assert.equal(
    getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount: 2,
      activeJobStatus: { job_id: "job-1", status: "running" },
      isDocumentHidden: true,
    }),
    HISTORY_REFRESH_HIDDEN_MS,
  );
  assert.equal(
    getAnalyticsHistoryRefreshMs({
      liveHistoryJobCount: 0,
      activeJobStatus: { job_id: "job-1", status: "running" },
      isDocumentHidden: false,
    }),
    null,
  );
});

test("mergeHistoryJobStatus updates the matching history row without disturbing selection order", () => {
  const jobs = [
    { job_id: "job-1", status: "pending", progress: 0, message: "Queued" },
    { job_id: "job-2", status: "completed", progress: 100, message: "Done" },
  ];

  const merged = mergeHistoryJobStatus(jobs, {
    job_id: "job-1",
    status: "running",
    progress: 45,
    phase_label: "Preparing data",
  });

  assert.deepEqual(merged, [
    {
      job_id: "job-1",
      status: "running",
      progress: 45,
      message: "Queued",
      phase_label: "Preparing data",
    },
    { job_id: "job-2", status: "completed", progress: 100, message: "Done" },
  ]);
  assert.notEqual(merged, jobs);
});

test("resolveSelectedAnalyticsJobId preserves manual selection while the row remains in history", () => {
  const jobs = [
    { job_id: "job-3", status: "running" },
    { job_id: "job-2", status: "completed" },
    { job_id: "job-1", status: "completed" },
  ];

  assert.equal(resolveSelectedAnalyticsJobId(jobs, "job-1"), "job-1");
});

test("resolveSelectedAnalyticsJobId falls back to the newest visible row when selection disappears", () => {
  const jobs = [
    { job_id: "job-5", status: "running" },
    { job_id: "job-4", status: "completed" },
  ];

  assert.equal(resolveSelectedAnalyticsJobId(jobs, "job-1"), "job-5");
  assert.equal(resolveSelectedAnalyticsJobId(jobs, null), "job-5");
  assert.equal(resolveSelectedAnalyticsJobId([], "job-1"), null);
});

test("resolveSelectedAnalyticsJobId preserves the current page's newly launched active job until history catches up", () => {
  const jobs = [
    { job_id: "job-5", status: "running" },
    { job_id: "job-4", status: "completed" },
  ];

  assert.equal(resolveSelectedAnalyticsJobId(jobs, "job-9", "job-9"), "job-9");
  assert.equal(resolveSelectedAnalyticsJobId([], "job-9", "job-9"), "job-9");
});
