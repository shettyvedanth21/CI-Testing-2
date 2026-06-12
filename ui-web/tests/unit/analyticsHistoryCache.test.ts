import test from "node:test";
import assert from "node:assert/strict";

import {
  buildAnalyticsHistoryStorageKey,
  readAnalyticsHistorySnapshot,
  writeAnalyticsHistorySnapshot,
} from "../../lib/analyticsHistoryCache.ts";

function installSessionStorage(seed?: Record<string, string>) {
  const storage = new Map(Object.entries(seed ?? {}));
  const previousWindow = globalThis.window;

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      sessionStorage: {
        getItem(key: string) {
          return storage.has(key) ? storage.get(key)! : null;
        },
        setItem(key: string, value: string) {
          storage.set(key, value);
        },
      },
    },
  });

  return {
    storage,
    restore() {
      if (previousWindow === undefined) {
        Reflect.deleteProperty(globalThis, "window");
        return;
      }
      Object.defineProperty(globalThis, "window", {
        configurable: true,
        value: previousWindow,
      });
    },
  };
}

test("writeAnalyticsHistorySnapshot persists the current history page shape", () => {
  const { storage, restore } = installSessionStorage();

  try {
    writeAnalyticsHistorySnapshot("tenant-1", {
      jobs: [{ job_id: "job-1", status: "completed", progress: 100, result_ready: true }],
      page: 2,
      hasMore: true,
      selectedJobId: "job-1",
    });

    const raw = storage.get(buildAnalyticsHistoryStorageKey("tenant-1"));
    assert.ok(raw);
    assert.deepEqual(JSON.parse(raw!), {
      jobs: [{ job_id: "job-1", status: "completed", progress: 100, result_ready: true }],
      page: 2,
      hasMore: true,
      selectedJobId: "job-1",
    });
  } finally {
    restore();
  }
});

test("readAnalyticsHistorySnapshot restores only valid cached analytics history", () => {
  const key = buildAnalyticsHistoryStorageKey("tenant-1");
  const { restore } = installSessionStorage({
    [key]: JSON.stringify({
      jobs: [
        { job_id: "job-1", status: "running", progress: 42 },
        { job_id: "job-2", status: "completed", result_ready: true },
      ],
      page: 1,
      hasMore: true,
      selectedJobId: "job-2",
    }),
  });

  try {
    assert.deepEqual(readAnalyticsHistorySnapshot("tenant-1"), {
      jobs: [
        { job_id: "job-1", status: "running", progress: 42 },
        { job_id: "job-2", status: "completed", result_ready: true },
      ],
      page: 1,
      hasMore: true,
      selectedJobId: "job-2",
    });
  } finally {
    restore();
  }
});

test("readAnalyticsHistorySnapshot ignores malformed or incompatible cache entries", () => {
  const key = buildAnalyticsHistoryStorageKey("tenant-1");
  const { restore } = installSessionStorage({
    [key]: JSON.stringify({
      jobs: [{ status: "completed" }],
      page: -3,
      hasMore: "yes",
      selectedJobId: 10,
    }),
  });

  try {
    assert.equal(readAnalyticsHistorySnapshot("tenant-1"), null);
  } finally {
    restore();
  }
});
