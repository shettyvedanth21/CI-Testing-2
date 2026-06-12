import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

import { runtimeConfig } from './config.js';

export const endpointFailures = new Rate('endpoint_failures');

function mergeHeaders(baseHeaders = {}, extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...baseHeaders,
    ...extraHeaders,
  };
}

function normalizeExpected(expected) {
  return Array.isArray(expected) ? expected : [expected];
}

export function requestJson(method, url, body, { expectedStatuses = [200], headers = {}, tags = {} } = {}) {
  const payload = body === null || body === undefined ? null : JSON.stringify(body);
  const response = http.request(method, url, payload, {
    headers: mergeHeaders({}, headers),
    tags,
    timeout: runtimeConfig.request.timeout,
  });

  const expected = normalizeExpected(expectedStatuses);
  const ok = check(response, {
    [`${method.toUpperCase()} ${tags.endpoint || url} status is expected`]: (res) =>
      expected.includes(res.status),
  });
  endpointFailures.add(!ok, tags);
  return response;
}

export function requestRaw(method, url, body, { expectedStatuses = [200], headers = {}, tags = {} } = {}) {
  const response = http.request(method, url, body, {
    headers,
    tags,
    timeout: runtimeConfig.request.timeout,
  });
  const expected = normalizeExpected(expectedStatuses);
  const ok = check(response, {
    [`${method.toUpperCase()} ${tags.endpoint || url} status is expected`]: (res) =>
      expected.includes(res.status),
  });
  endpointFailures.add(!ok, tags);
  return response;
}

export function parseJson(response) {
  if (!response || !response.body) {
    return null;
  }
  try {
    return response.json();
  } catch (_error) {
    return null;
  }
}
