import test, { mock } from "node:test";
import assert from "node:assert/strict";

import { NextRequest } from "next/server.js";

test("fleet stream route proxies SSE without buffering and forwards tenant/auth headers", async () => {
  process.env.DEVICE_SERVICE_BASE_URL = "http://device-service:8000";
  const { GET } = await import("../../app/backend/device/api/v1/devices/dashboard/fleet-stream/route.ts");

  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          'id: 1\nevent: heartbeat\ndata: {"id":"1","event":"heartbeat","generated_at":"2026-04-04T00:00:00.000Z","freshness_ts":"2026-04-04T00:00:00.000Z","stale":false,"warnings":[],"devices":[],"partial":false,"version":0}\n\n',
        ),
      );
      controller.close();
    },
  });

  const fetchMock = mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(String(input), "http://device-service:8000/api/v1/devices/dashboard/fleet-stream?page_size=5");
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("authorization"), "Bearer token-123");
    assert.equal(headers.get("x-target-tenant-id"), "SH00000001");
    assert.equal(headers.get("accept"), "text/event-stream");
    return new Response(upstreamBody, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "x-service-started-at": "2026-04-04T00:00:00.000Z",
      },
    });
  });

  try {
    const request = new NextRequest(
      "http://localhost:3000/backend/device/api/v1/devices/dashboard/fleet-stream?page_size=5",
      {
        headers: {
          authorization: "Bearer token-123",
          "x-target-tenant-id": "SH00000001",
          accept: "text/event-stream",
        },
      },
    );

    const response = await GET(request);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "text/event-stream");
    assert.equal(response.headers.get("x-service-started-at"), "2026-04-04T00:00:00.000Z");
    const body = await response.text();
    assert.match(body, /event: heartbeat/);
  } finally {
    fetchMock.mock.restore();
    delete process.env.DEVICE_SERVICE_BASE_URL;
  }
});
