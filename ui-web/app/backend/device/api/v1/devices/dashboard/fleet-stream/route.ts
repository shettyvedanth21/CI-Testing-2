import { NextRequest } from "next/server.js";

const isLocalDev = process.env.NODE_ENV !== "production";
const DEVICE_SERVICE_BASE =
  process.env.DEVICE_SERVICE_BASE_URL ??
  (isLocalDev ? "http://localhost:8000" : "http://device-service:8000");

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const revalidate = 0;

function buildUpstreamHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  const forwardHeaderNames = [
    "authorization",
    "accept",
    "cache-control",
    "last-event-id",
    "x-tenant-id",
    "x-target-tenant-id",
    "x-org-id",
  ];

  for (const name of forwardHeaderNames) {
    const value = request.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }

  headers.set("accept", "text/event-stream");
  headers.set("cache-control", "no-store");
  return headers;
}

export async function GET(request: NextRequest): Promise<Response> {
  const upstreamUrl = new URL(
    `/api/v1/devices/dashboard/fleet-stream${request.nextUrl.search}`,
    DEVICE_SERVICE_BASE,
  );

  const upstreamResponse = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildUpstreamHeaders(request),
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  const contentType = upstreamResponse.headers.get("content-type");
  if (contentType) {
    responseHeaders.set("content-type", contentType);
  } else {
    responseHeaders.set("content-type", "text/event-stream");
  }
  responseHeaders.set("cache-control", "no-store");
  responseHeaders.set("connection", "keep-alive");
  responseHeaders.set("x-accel-buffering", "no");

  const backendSession = upstreamResponse.headers.get("x-service-started-at");
  if (backendSession) {
    responseHeaders.set("x-service-started-at", backendSession);
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  });
}
