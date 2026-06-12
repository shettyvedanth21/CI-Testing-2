import { buildUrl, runtimeConfig, baseTags } from './config.js';
import { authHeaders, ensureSession, loadMe, setResolvedTenantId, getResolvedTenantId } from './auth.js';
import { requestJson, parseJson } from './http.js';

function choosePlantId(mePayload, devices) {
  if (runtimeConfig.scope.plantId) {
    return runtimeConfig.scope.plantId;
  }
  const plantIds = Array.isArray(mePayload?.plant_ids) ? mePayload.plant_ids.filter(Boolean) : [];
  if (plantIds.length > 0) {
    return plantIds[0];
  }
  if (devices.length > 0 && devices[0].plant_id) {
    return String(devices[0].plant_id);
  }
  return '';
}

function pickDevices(devices) {
  if (runtimeConfig.scope.deviceIds.length > 0) {
    const configured = new Set(runtimeConfig.scope.deviceIds);
    const matched = devices.filter((device) => configured.has(String(device.device_id)));
    if (matched.length === runtimeConfig.scope.deviceIds.length) {
      return matched;
    }
    return devices.filter((device) => configured.has(String(device.device_id)));
  }
  return devices.slice(0, Math.max(2, runtimeConfig.scope.selectedDeviceCount));
}

export function discoverRuntimeContext(scenario = 'bootstrap') {
  ensureSession(baseTags(scenario, 'auth', 'login'));
  const mePayload = loadMe(baseTags(scenario, 'auth', 'me'));
  const tenantId = getResolvedTenantId() || mePayload?.tenant?.id || mePayload?.user?.tenant_id || '';
  setResolvedTenantId(tenantId);

  const listingResponse = requestJson(
    'GET',
    buildUrl('device', '/api/v1/devices'),
    null,
    {
      expectedStatuses: [200],
      headers: authHeaders(),
      tags: baseTags(scenario, 'device', 'list', { name: 'device.list' }),
    },
  );
  const listingPayload = parseJson(listingResponse) || {};
  const devices = Array.isArray(listingPayload.data) ? listingPayload.data : [];
  const plantId = choosePlantId(mePayload, devices);
  const selectedDevices = pickDevices(devices);

  if (!tenantId) {
    throw new Error('Unable to resolve tenant scope from K6_TENANT_ID or /api/v1/auth/me');
  }
  if (selectedDevices.length === 0) {
    throw new Error('No devices were discovered for load-test payload construction');
  }

  return {
    tenantId,
    plantId,
    me: mePayload,
    availableFeatures: Array.isArray(mePayload?.entitlements?.available_features)
      ? mePayload.entitlements.available_features.map((feature) => String(feature))
      : [],
    devices,
    selectedDevices,
    primaryDeviceId: String(selectedDevices[0].device_id),
    secondaryDeviceId: String((selectedDevices[1] || selectedDevices[0]).device_id),
    selectedDeviceIds: selectedDevices
      .slice(0, Math.max(1, runtimeConfig.scope.selectedDeviceCount))
      .map((device) => String(device.device_id)),
  };
}

export function requireScenarioFeatures(seed, requiredFeatures, scenario = 'scenario') {
  if (!runtimeConfig.request.strictFeaturePreflight) {
    return;
  }

  const available = new Set((seed?.availableFeatures || []).map((feature) => String(feature)));
  const missing = (requiredFeatures || []).filter((feature) => !available.has(String(feature)));

  if (missing.length > 0) {
    throw new Error(
      [
        `Tenant ${seed?.tenantId || runtimeConfig.scope.tenantId || '<unknown>'} is missing required features for ${scenario}: ${missing.join(', ')}`,
        `Available features: ${Array.from(available).sort().join(', ') || '<none>'}`,
        'Grant the required premium features to the tenant or choose a tenant with the needed entitlements before running this scenario.',
      ].join(' | '),
    );
  }
}
