import test from "node:test";
import assert from "node:assert/strict";

import { createFleetStreamConnector } from "../../lib/fleetStreamReconnect.ts";

function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );
}

test("fleet stream reconnect refreshes auth before retrying", async () => {
  const callOrder: string[] = [];
  const eventIds: string[] = [];
  let currentToken = "expired-token";
  let reconnectCallback: (() => void) | null = null;

  const connectFleetStream = createFleetStreamConnector({
    streamFetch: async () => {
      callOrder.push(`fetch:${currentToken}`);
      if (currentToken === "expired-token") {
        return sseResponse([
          'id: 1\nevent: fleet_update\ndata: {"id":"1","event":"fleet_update","generated_at":"2026-04-03T00:00:00.000Z","freshness_ts":"2026-04-03T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":1}\n\n',
        ]);
      }
      return sseResponse([
        'id: 2\nevent: fleet_update\ndata: {"id":"2","event":"fleet_update","generated_at":"2026-04-03T00:00:01.000Z","freshness_ts":"2026-04-03T00:00:01.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":2}\n\n',
      ]);
    },
    refreshAccessToken: async () => {
      callOrder.push("refresh");
      currentToken = "fresh-token";
      return currentToken;
    },
    clearSession: () => {
      callOrder.push("clear");
    },
    scheduleReconnect: (callback) => {
      reconnectCallback = callback;
      return 1 as unknown as ReturnType<typeof window.setTimeout>;
    },
    clearScheduledReconnect: () => {},
    createAbortController: () => new AbortController(),
    createTextDecoder: () => new TextDecoder(),
    parseEventChunk: (chunk) => {
      const payloadLine = chunk.split("\n").find((line) => line.startsWith("data:"));
      return payloadLine ? JSON.parse(payloadLine.slice(5).trim()) : null;
    },
  });

  const stop = connectFleetStream({
    streamUrl: "/api/v1/devices/dashboard/fleet-stream",
    onEvent: (payload) => {
      eventIds.push(payload.id);
    },
  });

  await flushMicrotasks();
  assert.deepEqual(callOrder, ["fetch:expired-token"]);
  assert.equal(typeof reconnectCallback, "function");

  const reconnect = reconnectCallback!;
  assert.equal(typeof reconnect, "function");
  reconnect();
  await flushMicrotasks();
  await flushMicrotasks();

  assert.deepEqual(callOrder, ["fetch:expired-token", "refresh", "fetch:fresh-token"]);
  assert.deepEqual(eventIds, ["1", "2"]);
  stop();
});

test("fleet stream reconnect stops and clears auth when refresh fails", async () => {
  const callOrder: string[] = [];
  let reconnectCallback: (() => void) | null = null;

  const connectFleetStream = createFleetStreamConnector({
    streamFetch: async () => {
      callOrder.push("fetch");
      return sseResponse([
        'id: 1\nevent: heartbeat\ndata: {"id":"1","event":"heartbeat","generated_at":"2026-04-03T00:00:00.000Z","freshness_ts":"2026-04-03T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":0}\n\n',
      ]);
    },
    refreshAccessToken: async () => {
      callOrder.push("refresh");
      return null;
    },
    clearSession: () => {
      callOrder.push("clear");
    },
    scheduleReconnect: (callback) => {
      reconnectCallback = callback;
      return 1 as unknown as ReturnType<typeof window.setTimeout>;
    },
    clearScheduledReconnect: () => {},
    createAbortController: () => new AbortController(),
    createTextDecoder: () => new TextDecoder(),
    parseEventChunk: (chunk) => {
      const payloadLine = chunk.split("\n").find((line) => line.startsWith("data:"));
      return payloadLine ? JSON.parse(payloadLine.slice(5).trim()) : null;
    },
  });

  const stop = connectFleetStream({
    streamUrl: "/api/v1/devices/dashboard/fleet-stream",
    onEvent: () => {},
  });

  await flushMicrotasks();
  assert.deepEqual(callOrder, ["fetch"]);
  assert.equal(typeof reconnectCallback, "function");

  const reconnect = reconnectCallback!;
  assert.equal(typeof reconnect, "function");
  reconnect();
  await flushMicrotasks();

  assert.deepEqual(callOrder, ["fetch", "refresh", "clear"]);
  stop();
});

test("fleet stream reconnect notifies on clean stream closure", async () => {
  const callOrder: string[] = [];
  const reconnectEvents: string[] = [];
  let reconnectCallback: (() => void) | null = null;

  const connectFleetStream = createFleetStreamConnector({
    streamFetch: async () => {
      callOrder.push("fetch");
      return sseResponse([
        'id: 1\nevent: fleet_update\ndata: {"id":"1","event":"fleet_update","generated_at":"2026-04-03T00:00:00.000Z","freshness_ts":"2026-04-03T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":1}\n\n',
      ]);
    },
    refreshAccessToken: async () => {
      callOrder.push("refresh");
      return "fresh-token";
    },
    clearSession: () => {
      callOrder.push("clear");
    },
    scheduleReconnect: (callback) => {
      reconnectCallback = callback;
      return 1 as unknown as ReturnType<typeof window.setTimeout>;
    },
    clearScheduledReconnect: () => {},
    createAbortController: () => new AbortController(),
    createTextDecoder: () => new TextDecoder(),
    parseEventChunk: (chunk) => {
      const payloadLine = chunk.split("\n").find((line) => line.startsWith("data:"));
      return payloadLine ? JSON.parse(payloadLine.slice(5).trim()) : null;
    },
  });

  const stop = connectFleetStream({
    streamUrl: "/api/v1/devices/dashboard/fleet-stream",
    onEvent: () => {},
    onReconnectStart: (reason, retryCount) => {
      reconnectEvents.push(`${reason}:${retryCount}`);
    },
  });

  await flushMicrotasks();
  assert.deepEqual(reconnectEvents, ["stream_closed:1"]);

  const reconnect = reconnectCallback!;
  reconnect();
  await flushMicrotasks();

  assert.deepEqual(callOrder, ["fetch", "refresh", "fetch"]);
  stop();
});
