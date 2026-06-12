import { sleep } from 'k6';

import { authHeaders, ensureSession } from './auth.js';
import { baseTags, buildUrl, runtimeConfig } from './config.js';
import { requestJson, requestRaw, parseJson } from './http.js';

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function analyticsWindow() {
  const end = new Date();
  const start = new Date(end.getTime() - runtimeConfig.reports.days * 24 * 60 * 60 * 1000);
  return {
    startIso: start.toISOString(),
    endIso: end.toISOString(),
    startDate: isoDate(start),
    endDate: isoDate(end),
  };
}

function maybeFetchArtifact(url, headers, tags) {
  if (!runtimeConfig.request.fetchArtifacts || !url) {
    return;
  }
  requestRaw('GET', url, null, {
    expectedStatuses: [200],
    headers,
    tags,
  });
}

function pollUntilTerminal({ pollFn, resultFn, scenario, domain, statusEndpoint, resultEndpoint }) {
  let finalPayload = null;
  for (let attempt = 0; attempt < runtimeConfig.scenario.pollAttempts; attempt += 1) {
    const statusResponse = pollFn(
      baseTags(scenario, domain, statusEndpoint, {
        name: `${domain}.${statusEndpoint}`,
        phase: 'poll',
      }),
    );
    finalPayload = parseJson(statusResponse) || {};
    const status = String(finalPayload.status || '').toLowerCase();
    if (status === 'completed' || status === 'failed') {
      if (status === 'completed' && typeof resultFn === 'function') {
        resultFn(
          baseTags(scenario, domain, resultEndpoint, {
            name: `${domain}.${resultEndpoint}`,
            phase: 'result',
          }),
          finalPayload,
        );
      }
      return finalPayload;
    }
    sleep(runtimeConfig.scenario.pollIntervalMs / 1000);
  }
  return finalPayload;
}

export function runAnalyticsFlow(seed, scenario = 'analytics') {
  ensureSession(baseTags(scenario, 'auth', 'login'));
  const headers = authHeaders();
  const window = analyticsWindow();
  const analysisType = runtimeConfig.analytics.type || 'anomaly';
  const modelName =
    runtimeConfig.analytics.model ||
    (analysisType === 'prediction' ? 'failure_ensemble' : 'anomaly_ensemble');

  requestJson('GET', buildUrl('analytics', '/api/v1/analytics/models'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'analytics', 'models', { name: 'analytics.models' }),
  });

  requestJson('POST', buildUrl('analytics', '/api/v1/analytics/preflight'), {
    device_ids: [seed.primaryDeviceId],
    start_time: window.startIso,
    end_time: window.endIso,
  }, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'analytics', 'preflight', { name: 'analytics.preflight' }),
  });

  const runResponse = requestJson('POST', buildUrl('analytics', '/api/v1/analytics/run'), {
    device_id: seed.primaryDeviceId,
    start_time: window.startIso,
    end_time: window.endIso,
    analysis_type: analysisType,
    model_name: modelName,
    parameters: {
      requested_by: 'k6',
      scenario,
      tenant_id: seed.tenantId,
      plant_id: seed.plantId || null,
    },
  }, {
    expectedStatuses: [202],
    headers,
    tags: baseTags(scenario, 'analytics', 'run', { name: 'analytics.run' }),
  });

  const runPayload = parseJson(runResponse) || {};
  const jobId = runPayload.job_id;
  if (!jobId) {
    return runPayload;
  }

  return pollUntilTerminal({
    scenario,
    domain: 'analytics',
    statusEndpoint: 'status',
    resultEndpoint: 'results',
    pollFn: (tags) =>
      requestJson('GET', buildUrl('analytics', `/api/v1/analytics/status/${jobId}`), null, {
        expectedStatuses: [200, 404],
        headers,
        tags,
      }),
    resultFn: (tags) =>
      requestJson('GET', buildUrl('analytics', `/api/v1/analytics/results/${jobId}`), null, {
        expectedStatuses: [200, 409],
        headers,
        tags,
      }),
  });
}

export function runReportsFlow(seed, scenario = 'reports') {
  ensureSession(baseTags(scenario, 'auth', 'login'));
  const headers = authHeaders();
  const window = analyticsWindow();

  requestJson('GET', buildUrl('reporting', '/api/reports/history'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'reports', 'history', { name: 'reports.history' }),
  });

  const createResponse = requestJson('POST', buildUrl('reporting', '/api/reports/energy/consumption'), {
    device_id: seed.primaryDeviceId,
    report_name: `k6-load-${Date.now()}`,
    start_date: window.startDate,
    end_date: window.endDate,
    tenant_id: seed.tenantId,
  }, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'reports', 'create', { name: 'reports.create' }),
  });

  const createPayload = parseJson(createResponse) || {};
  const reportId = createPayload.report_id;
  if (!reportId) {
    return createPayload;
  }

  return pollUntilTerminal({
    scenario,
    domain: 'reports',
    statusEndpoint: 'status',
    resultEndpoint: 'result',
    pollFn: (tags) =>
      requestJson('GET', buildUrl('reporting', `/api/reports/${reportId}/status`), null, {
        expectedStatuses: [200, 404],
        headers,
        tags,
      }),
    resultFn: (tags, finalPayload) => {
      requestJson('GET', buildUrl('reporting', `/api/reports/${reportId}/result`), null, {
        expectedStatuses: [200, 409],
        headers,
        tags,
      });
      if (finalPayload && finalPayload.download_ready) {
        requestJson('GET', buildUrl('reporting', `/api/reports/${reportId}/download`), null, {
          expectedStatuses: [200, 409],
          headers,
          tags: baseTags(scenario, 'reports', 'download', { name: 'reports.download' }),
        });
      }
    },
  });
}

export function runWasteFlow(seed, scenario = 'waste') {
  ensureSession(baseTags(scenario, 'auth', 'login'));
  const headers = authHeaders();
  const window = analyticsWindow();

  requestJson('GET', buildUrl('waste', '/api/v1/waste/analysis/history?limit=20&offset=0'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'waste', 'history', { name: 'waste.history' }),
  });

  const runResponse = requestJson('POST', buildUrl('waste', '/api/v1/waste/analysis/run'), {
    job_name: `k6-waste-${Date.now()}`,
    scope: 'selected',
    device_ids: seed.selectedDeviceIds,
    start_date: window.startDate,
    end_date: window.endDate,
    granularity: runtimeConfig.waste.granularity,
  }, {
    expectedStatuses: [202],
    headers,
    tags: baseTags(scenario, 'waste', 'run', { name: 'waste.run' }),
  });

  const runPayload = parseJson(runResponse) || {};
  const jobId = runPayload.job_id;
  if (!jobId) {
    return runPayload;
  }

  return pollUntilTerminal({
    scenario,
    domain: 'waste',
    statusEndpoint: 'status',
    resultEndpoint: 'result',
    pollFn: (tags) =>
      requestJson('GET', buildUrl('waste', `/api/v1/waste/analysis/${jobId}/status`), null, {
        expectedStatuses: [200, 404],
        headers,
        tags,
      }),
    resultFn: (tags, finalPayload) => {
      requestJson('GET', buildUrl('waste', `/api/v1/waste/analysis/${jobId}/result`), null, {
        expectedStatuses: [200, 409],
        headers,
        tags,
      });
      if (finalPayload && finalPayload.download_ready) {
        const downloadResponse = requestJson('GET', buildUrl('waste', `/api/v1/waste/analysis/${jobId}/download`), null, {
          expectedStatuses: [200, 409],
          headers,
          tags: baseTags(scenario, 'waste', 'download', { name: 'waste.download' }),
        });
        maybeFetchArtifact(
          buildUrl('waste', `/api/v1/waste/analysis/${jobId}/file`),
          headers,
          baseTags(scenario, 'waste', 'file', { name: 'waste.file' }),
        );
      }
    },
  });
}

function buildRulePayload(seed, ruleName) {
  return {
    rule_type: 'threshold',
    rule_name: ruleName,
    description: 'k6 reusable load-test rule',
    scope: 'selected_devices',
    device_ids: [seed.primaryDeviceId],
    property: 'power',
    condition: '>',
    threshold: 9999999,
    notification_channels: ['email'],
    notification_recipients: [
      {
        channel: 'email',
        value: runtimeConfig.credentials.email,
      },
    ],
  };
}

export function runRulesFlow(seed, scenario = 'rules-alerts') {
  ensureSession(baseTags(scenario, 'auth', 'login'));
  const headers = authHeaders();

  requestJson('GET', buildUrl('rules', '/api/v1/rules?page=1&page_size=20'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'rules', 'list', { name: 'rules.list' }),
  });
  requestJson('GET', buildUrl('rules', '/api/v1/alerts?page=1&page_size=20'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'alerts', 'list', { name: 'alerts.list' }),
  });
  requestJson('GET', buildUrl('rules', '/api/v1/alerts/events/unread-count'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'alerts', 'unread-count', { name: 'alerts.unread_count' }),
  });
  requestJson('GET', buildUrl('rules', '/api/v1/alerts/events/summary'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'alerts', 'summary', { name: 'alerts.summary' }),
  });
  requestJson('GET', buildUrl('rules', '/api/v1/alerts/events?page=1&page_size=20'), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'alerts', 'events', { name: 'alerts.events' }),
  });

  const createResponse = requestJson('POST', buildUrl('rules', '/api/v1/rules'), buildRulePayload(seed, `k6-rule-${Date.now()}`), {
    expectedStatuses: [201, 409],
    headers,
    tags: baseTags(scenario, 'rules', 'create', { name: 'rules.create' }),
  });
  const createPayload = parseJson(createResponse) || {};
  const ruleId = createPayload?.data?.rule_id;
  if (!ruleId) {
    return createPayload;
  }

  requestJson('GET', buildUrl('rules', `/api/v1/rules/${ruleId}`), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'rules', 'get', { name: 'rules.get' }),
  });
  requestJson('PATCH', buildUrl('rules', `/api/v1/rules/${ruleId}/status`), {
    status: 'paused',
  }, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'rules', 'status', { name: 'rules.status' }),
  });
  requestJson('DELETE', buildUrl('rules', `/api/v1/rules/${ruleId}?soft=true`), null, {
    expectedStatuses: [200],
    headers,
    tags: baseTags(scenario, 'rules', 'delete', { name: 'rules.delete' }),
  });
  return { rule_id: ruleId };
}

export function idleThinkTime() {
  sleep(runtimeConfig.scenario.thinkTimeMs / 1000);
}
