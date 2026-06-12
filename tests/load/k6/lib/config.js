function requiredEnv(name) {
  const value = __ENV[name];
  if (!value || !String(value).trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return String(value).trim();
}

function optionalEnv(name, fallback = '') {
  const value = __ENV[name];
  return value === undefined ? fallback : String(value).trim();
}

function intEnv(name, fallback) {
  const raw = optionalEnv(name, '');
  if (!raw) {
    return fallback;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Environment variable ${name} must be an integer`);
  }
  return parsed;
}

function boolEnv(name, fallback) {
  const raw = optionalEnv(name, '');
  if (!raw) {
    return fallback;
  }
  return ['1', 'true', 'yes', 'on'].includes(raw.toLowerCase());
}

function splitCsv(raw) {
  return String(raw || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

function trimSlash(value) {
  return String(value || '').replace(/\/+$/, '');
}

function joinUrl(baseUrl, path) {
  const trimmedBase = trimSlash(baseUrl);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${trimmedBase}${normalizedPath}`;
}

function buildServiceBases() {
  const routeMode = optionalEnv('K6_ROUTE_MODE', 'proxy').toLowerCase();
  if (routeMode === 'proxy') {
    const baseUrl = trimSlash(requiredEnv('K6_BASE_URL'));
    return {
      routeMode,
      auth: trimSlash(optionalEnv('K6_AUTH_BASE_URL', `${baseUrl}/backend/auth`)),
      device: trimSlash(optionalEnv('K6_DEVICE_BASE_URL', `${baseUrl}/backend/device`)),
      analytics: trimSlash(optionalEnv('K6_ANALYTICS_BASE_URL', `${baseUrl}/backend/analytics`)),
      reporting: trimSlash(optionalEnv('K6_REPORTING_BASE_URL', baseUrl)),
      waste: trimSlash(optionalEnv('K6_WASTE_BASE_URL', baseUrl)),
      rules: trimSlash(optionalEnv('K6_RULES_BASE_URL', `${baseUrl}/backend/rule-engine`)),
    };
  }

  if (routeMode !== 'direct') {
    throw new Error(`Unsupported K6_ROUTE_MODE: ${routeMode}`);
  }

  return {
    routeMode,
    auth: trimSlash(requiredEnv('K6_AUTH_BASE_URL')),
    device: trimSlash(requiredEnv('K6_DEVICE_BASE_URL')),
    analytics: trimSlash(requiredEnv('K6_ANALYTICS_BASE_URL')),
    reporting: trimSlash(requiredEnv('K6_REPORTING_BASE_URL')),
    waste: trimSlash(requiredEnv('K6_WASTE_BASE_URL')),
    rules: trimSlash(requiredEnv('K6_RULES_BASE_URL')),
  };
}

export const runtimeConfig = {
  services: buildServiceBases(),
  credentials: {
    email: requiredEnv('K6_LOGIN_EMAIL'),
    password: requiredEnv('K6_LOGIN_PASSWORD'),
  },
  scope: {
    tenantId: optionalEnv('K6_TENANT_ID', ''),
    plantId: optionalEnv('K6_PLANT_ID', ''),
    deviceIds: splitCsv(optionalEnv('K6_DEVICE_IDS', '')),
    selectedDeviceCount: intEnv('K6_SELECTED_DEVICE_COUNT', 3),
  },
  scenario: {
    name: optionalEnv('K6_SCENARIO', 'mixed'),
    duration: optionalEnv('K6_DURATION', '15m'),
    vus: intEnv('K6_VUS', 8),
    rate: optionalEnv('K6_RATE', ''),
    timeUnit: optionalEnv('K6_TIME_UNIT', '1s'),
    preAllocatedVUs: intEnv('K6_PREALLOCATED_VUS', 8),
    maxVUs: intEnv('K6_MAX_VUS', 32),
    executor: optionalEnv('K6_EXECUTOR', 'constant-vus'),
    thinkTimeMs: intEnv('K6_THINK_TIME_MS', 1000),
    pollAttempts: intEnv('K6_POLL_ATTEMPTS', 10),
    pollIntervalMs: intEnv('K6_POLL_INTERVAL_MS', 3000),
  },
  request: {
    timeout: optionalEnv('K6_HTTP_TIMEOUT', '60s'),
    fetchArtifacts: boolEnv('K6_FETCH_ARTIFACTS', false),
    strictFeaturePreflight: boolEnv('K6_STRICT_FEATURE_PREFLIGHT', true),
  },
  thresholds: {
    failed: optionalEnv('K6_HTTP_REQ_FAILED_THRESHOLD', 'rate<0.05'),
    p95: optionalEnv('K6_HTTP_REQ_DURATION_P95_THRESHOLD', 'p(95)<2500'),
    p99: optionalEnv('K6_HTTP_REQ_DURATION_P99_THRESHOLD', 'p(99)<5000'),
  },
  analytics: {
    type: optionalEnv('K6_ANALYTICS_TYPE', 'anomaly'),
    model: optionalEnv('K6_ANALYTICS_MODEL', ''),
  },
  reports: {
    days: intEnv('K6_REPORT_DAYS', 7),
  },
  waste: {
    granularity: optionalEnv('K6_WASTE_GRANULARITY', 'daily'),
  },
  mixedWeights: {
    analytics: intEnv('K6_MIX_ANALYTICS_WEIGHT', 3),
    reports: intEnv('K6_MIX_REPORTS_WEIGHT', 3),
    waste: intEnv('K6_MIX_WASTE_WEIGHT', 2),
    rules: intEnv('K6_MIX_RULES_WEIGHT', 2),
  },
};

export function buildUrl(service, path) {
  const baseUrl = runtimeConfig.services[service];
  if (!baseUrl) {
    throw new Error(`Unknown service key: ${service}`);
  }
  return joinUrl(baseUrl, path);
}

export function baseTags(scenario, domain, endpoint, extra = {}) {
  return {
    suite: 'shivex-k6',
    scenario,
    domain,
    endpoint,
    route_mode: runtimeConfig.services.routeMode,
    ...extra,
  };
}

export function buildOptions(scenarioName) {
  const scenarioConfig =
    runtimeConfig.scenario.rate || runtimeConfig.scenario.executor === 'constant-arrival-rate'
      ? {
          executor: 'constant-arrival-rate',
          rate: Number.parseInt(runtimeConfig.scenario.rate || `${runtimeConfig.scenario.vus}`, 10),
          timeUnit: runtimeConfig.scenario.timeUnit,
          duration: runtimeConfig.scenario.duration,
          preAllocatedVUs: runtimeConfig.scenario.preAllocatedVUs,
          maxVUs: runtimeConfig.scenario.maxVUs,
          exec: 'default',
          tags: {
            suite: 'shivex-k6',
            scenario: scenarioName,
          },
        }
      : {
          executor: 'constant-vus',
          vus: runtimeConfig.scenario.vus,
          duration: runtimeConfig.scenario.duration,
          exec: 'default',
          tags: {
            suite: 'shivex-k6',
            scenario: scenarioName,
          },
        };

  return {
    scenarios: {
      [scenarioName]: scenarioConfig,
    },
    thresholds: {
      http_req_failed: [runtimeConfig.thresholds.failed],
      http_req_duration: [runtimeConfig.thresholds.p95, runtimeConfig.thresholds.p99],
    },
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    userAgent: 'shivex-k6-load-toolkit/1.0',
  };
}
