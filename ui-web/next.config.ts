import type { NextConfig } from "next";

const isLocalDev = process.env.NODE_ENV !== "production";

const DEVICE_SERVICE_BASE = process.env.DEVICE_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8000" : "http://device-service:8000");
const DATA_SERVICE_BASE = process.env.DATA_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8081" : "http://data-service:8081");
const RULE_ENGINE_SERVICE_BASE =
  process.env.RULE_ENGINE_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8002" : "http://rule-engine-service:8002");
const ANALYTICS_SERVICE_BASE =
  process.env.ANALYTICS_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8003" : "http://analytics-service:8003");
const DATA_EXPORT_SERVICE_BASE =
  process.env.DATA_EXPORT_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8080" : "http://data-export-service:8080");
const REPORTING_SERVICE_BASE =
  process.env.REPORTING_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8085" : "http://reporting-service:8085");
const WASTE_SERVICE_BASE =
  process.env.WASTE_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8087" : "http://waste-analysis-service:8087");
const COPILOT_SERVICE_BASE =
  process.env.COPILOT_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8007" : "http://copilot-service:8007");
const AUTH_SERVICE_BASE =
  process.env.AUTH_SERVICE_BASE_URL ?? (isLocalDev ? "http://localhost:8090" : "http://auth-service:8090");

function buildContentSecurityPolicy() {
  const directives: Array<[string, string[]]> = [
    ["default-src", ["'self'"]],
    ["base-uri", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
    ["object-src", ["'none'"]],
    ["form-action", ["'self'"]],
    ["script-src", ["'self'", "'unsafe-inline'", ...(isLocalDev ? ["'unsafe-eval'"] : [])]],
    [
      "style-src",
      ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    ],
    ["img-src", ["'self'", "data:", "blob:", "https:"]],
    ["font-src", ["'self'", "data:", "https://fonts.gstatic.com"]],
    ["connect-src", ["'self'", "ws:", "wss:"]],
    ["frame-src", ["'self'"]],
  ];

  return directives.map(([name, values]) => `${name} ${values.join(" ")}`).join("; ");
}

const contentSecurityPolicy = buildContentSecurityPolicy();

const securityHeaders = [
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  ...(isLocalDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
      {
        source: "/:path*",
        has: [{ type: "header", key: "accept", value: ".*text/html.*" }],
        headers: [
          { key: "Cache-Control", value: "no-store" },
          { key: "Pragma", value: "no-cache" },
          { key: "Expires", value: "0" },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/backend/device/:path*",
        destination: `${DEVICE_SERVICE_BASE}/:path*`,
      },
      {
        source: "/backend/data/:path*",
        destination: `${DATA_SERVICE_BASE}/:path*`,
      },
      {
        source: "/backend/rule-engine/:path*",
        destination: `${RULE_ENGINE_SERVICE_BASE}/:path*`,
      },

      {
        source: "/backend/analytics/:path*",
        destination: `${ANALYTICS_SERVICE_BASE}/:path*`,
      },

      {
        source: "/backend/data-export/:path*",
        destination: `${DATA_EXPORT_SERVICE_BASE}/:path*`,
      },
      {
        source: "/backend/reporting/:path*",
        destination: `${REPORTING_SERVICE_BASE}/:path*`,
      },
      {
        source: "/backend/copilot/:path*",
        destination: `${COPILOT_SERVICE_BASE}/:path*`,
      },
      {
        source: "/backend/auth/:path*",
        destination: `${AUTH_SERVICE_BASE}/:path*`,
      },

      {
        source: "/api/reports/:path(.*)",
        destination: `${REPORTING_SERVICE_BASE}/api/reports/:path(.*)`,
      },
      {
        source: "/api/waste/:path(.*)",
        destination: `${WASTE_SERVICE_BASE}/api/v1/waste/:path(.*)`,
      },
    ];
  },
};

export default nextConfig;
