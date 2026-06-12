/* eslint-disable @typescript-eslint/no-require-imports */

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function isoAt(minutesOffset) {
  return new Date(Date.UTC(2026, 4, 2, 9, 30 + minutesOffset, 0)).toISOString();
}

async function fulfillJson(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

function buildEntitlements() {
  const modules = ["machines", "calendar", "rules", "reports", "settings"];
  return {
    premium_feature_grants: [],
    role_feature_matrix: {
      org_admin: modules,
      plant_manager: [],
      operator: [],
      viewer: [],
      super_admin: modules,
    },
    baseline_features_by_role: {
      org_admin: modules,
      plant_manager: [],
      operator: [],
      viewer: [],
      super_admin: modules,
    },
    effective_features_by_role: {
      org_admin: modules,
      plant_manager: [],
      operator: [],
      viewer: [],
      super_admin: modules,
    },
    available_features: modules,
    entitlements_version: 1,
  };
}

function createJourneyState() {
  const primaryPlant = {
    id: "plant-1",
    tenant_id: "SH00000001",
    name: "Plant North",
    location: "Pune",
    timezone: "Asia/Kolkata",
    is_active: true,
    created_at: isoAt(-60),
  };
  return {
    tenantId: "SH00000001",
    tenant: {
      id: "SH00000001",
      name: "Factory Ops",
      slug: "factory-ops",
      is_active: true,
      created_at: isoAt(-60),
    },
    plant: primaryPlant,
    plants: [primaryPlant],
    me: {
      user: {
        id: "user-journey-1",
        email: "ops@example.com",
        full_name: "Factory Ops Admin",
        role: "org_admin",
        tenant_id: "SH00000001",
        is_active: true,
        created_at: isoAt(-60),
        last_login_at: isoAt(-10),
      },
      tenant: {
        id: "SH00000001",
        name: "Factory Ops",
        slug: "factory-ops",
        is_active: true,
        created_at: isoAt(-60),
      },
      plant_ids: ["plant-1"],
      entitlements: buildEntitlements(),
    },
    deviceCounter: 1,
    ruleCounter: 1,
    eventCounter: 1,
    shiftCounter: 1,
    healthConfigCounter: 1,
    devices: [],
    healthConfigsByDevice: {},
    shiftsByDevice: {},
    rulesByDevice: {},
    activityEventsByDevice: {},
    maintenanceByDevice: {},
    forceRuleMutationDenied: false,
    forceMaintenanceDeleteMissing: false,
    widgetConfigByDevice: {},
    mqttByDevice: {},
    tariff: {
      id: null,
      rate: null,
      currency: "INR",
      updated_at: null,
    },
    tariffHistory: [],
    tariffVersionCounter: 1,
    schedules: [],
    scheduleCounter: 1,
    dashboardCostStateOverride: null,
    dashboardCostReasonsOverride: [],
    fleetVersion: 1,
    loggedIn: false,
  };
}

function buildAccessToken(state) {
  return `header.${base64Json({
    role: state.me.user.role,
    tenant_id: state.tenantId,
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function latestTelemetry(deviceId) {
  return {
    timestamp: isoAt(0),
    device_id: deviceId,
    power: 250,
    current: 12.4,
    voltage: 231,
  };
}

function telemetryRows(deviceId) {
  const row = latestTelemetry(deviceId);
  return [
    row,
    { ...row, timestamp: isoAt(-5), power: 244, current: 12.1 },
    { ...row, timestamp: isoAt(-10), power: 238, current: 11.8 },
  ];
}

function getDevice(state, deviceId) {
  return state.devices.find((device) => device.device_id === deviceId) ?? null;
}

function listHealthConfigs(state, deviceId) {
  return state.healthConfigsByDevice[deviceId] ?? [];
}

function listShifts(state, deviceId) {
  return state.shiftsByDevice[deviceId] ?? [];
}

function listRules(state, deviceId) {
  return state.rulesByDevice[deviceId] ?? [];
}

function listEvents(state, deviceId) {
  return state.activityEventsByDevice[deviceId] ?? [];
}

function unreadCount(state, deviceId) {
  return listEvents(state, deviceId).filter((event) => !event.is_read).length;
}

function buildHealthScore(state, deviceId) {
  const configs = listHealthConfigs(state, deviceId).filter((item) => item.is_active);
  if (configs.length === 0) {
    return null;
  }

  const totalWeight = configs.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  if (Math.abs(totalWeight - 100) > 0.01) {
    return {
      device_id: deviceId,
      health_score: null,
      status: "Awaiting configuration",
      status_color: "⚪",
      message: "Total active weight must equal 100%.",
      machine_state: "RUNNING",
      parameter_scores: configs.map((config) => ({
        parameter_name: config.parameter_name,
        telemetry_key: config.parameter_name,
        value: latestTelemetry(deviceId)[config.parameter_name] ?? null,
        raw_score: null,
        weighted_score: 0,
        weight: config.weight,
        status: "Awaiting configuration",
        status_color: "⚪",
        included_in_score: false,
      })),
      total_weight_configured: totalWeight,
      parameters_included: 0,
      parameters_skipped: configs.length,
    };
  }

  return {
    device_id: deviceId,
    health_score: 92,
    status: "Healthy",
    status_color: "🟢",
    message: "Power is within the configured normal range.",
    machine_state: "RUNNING",
    parameter_scores: configs.map((config) => ({
      parameter_name: config.parameter_name,
      telemetry_key: config.parameter_name,
      value: latestTelemetry(deviceId)[config.parameter_name] ?? null,
      raw_score: config.parameter_name === "power" ? 92 : 90,
      weighted_score: config.parameter_name === "power" ? 92 : 90,
      weight: config.weight,
      status: "Healthy",
      status_color: "🟢",
      included_in_score: true,
    })),
    total_weight_configured: 100,
    parameters_included: configs.length,
    parameters_skipped: 0,
  };
}

function buildUptime(state, deviceId) {
  const shifts = listShifts(state, deviceId).filter((shift) => shift.is_active);
  if (shifts.length === 0) {
    return {
      device_id: deviceId,
      uptime_percentage: null,
      total_planned_minutes: 0,
      total_effective_minutes: 0,
      actual_running_minutes: 0,
      shifts_configured: 0,
      window_start: null,
      window_end: null,
      window_timezone: "Asia/Kolkata",
      data_coverage_pct: 0,
      data_quality: "medium",
      calculation_mode: "no_active_shift",
      message: "No active shift window right now.",
    };
  }

  return {
    device_id: deviceId,
    uptime_percentage: 96.5,
    total_planned_minutes: 480,
    total_effective_minutes: 450,
    actual_running_minutes: 434,
    shifts_configured: shifts.length,
    window_start: isoAt(-480),
    window_end: isoAt(0),
    window_timezone: "Asia/Kolkata",
    data_coverage_pct: 100,
    data_quality: "high",
    calculation_mode: "shift_window",
    message: "Calculated from the active shift window.",
  };
}

function buildLossStats(state, deviceId) {
  const rate = state.tariff.rate;
  const idleKwh = 3.2;
  const offHoursKwh = 1.6;
  const overconsumptionKwh = 0.8;
  const totalLossKwh = idleKwh + offHoursKwh + overconsumptionKwh;
  const todayEnergyKwh = 25.4;
  const cost = (value) => (rate == null ? null : Number((value * rate).toFixed(2)));

  return {
    device_id: deviceId,
    day_bucket: "2026-05-02",
    last_telemetry_ts: isoAt(0),
    updated_at: isoAt(0),
    tariff_configured: rate != null,
    currency: state.tariff.currency,
    full_load_current_a: 18,
    idle_threshold_pct_of_fla: 25,
    derived_idle_threshold_a: 4.5,
    derived_overconsumption_threshold_a: 16,
    today: {
      idle_kwh: idleKwh,
      idle_cost_inr: cost(idleKwh),
      off_hours_kwh: offHoursKwh,
      off_hours_cost_inr: cost(offHoursKwh),
      overconsumption_kwh: overconsumptionKwh,
      overconsumption_cost_inr: cost(overconsumptionKwh),
      total_loss_kwh: totalLossKwh,
      total_loss_cost_inr: cost(totalLossKwh),
      today_energy_kwh: todayEnergyKwh,
      today_energy_cost_inr: cost(todayEnergyKwh),
    },
  };
}

function buildCurrentState(deviceId) {
  return {
    device_id: deviceId,
    state: "running",
    current_band: "in_load",
    current: 12.4,
    voltage: 231,
    threshold: 4.5,
    full_load_current_a: 18,
    idle_threshold_pct_of_fla: 25,
    derived_idle_threshold_a: 4.5,
    derived_overconsumption_threshold_a: 16,
    timestamp: isoAt(0),
    current_field: "current",
    voltage_field: "voltage",
  };
}

function buildWidgetConfig(state, deviceId) {
  const config = state.widgetConfigByDevice[deviceId];
  if (config) {
    return clone(config);
  }
  return {
    device_id: deviceId,
    available_fields: ["power", "current", "voltage"],
    selected_fields: ["power", "current"],
    effective_fields: ["power", "current"],
    default_applied: false,
  };
}

function buildFleetItem(state, device) {
  const healthScore = buildHealthScore(state, device.device_id);
  const uptime = buildUptime(state, device.device_id);
  const operationalStatus = device.operational_status ?? device.runtime_status ?? "running";

  return {
    device_id: device.device_id,
    device_name: device.device_name,
    device_type: device.device_type,
    plant_id: device.plant_id,
    runtime_status: device.runtime_status,
    load_state: device.load_state ?? (operationalStatus === "unknown" ? "unknown" : operationalStatus === "stopped" ? "stopped" : "running"),
    current_band: device.current_band ?? (operationalStatus === "running" ? "in_load" : "unknown"),
    operational_status: operationalStatus,
    location: device.location,
    first_telemetry_timestamp: device.first_telemetry_timestamp,
    last_seen_timestamp: device.last_seen_timestamp,
    health_score: healthScore?.health_score ?? null,
    has_uptime_config: uptime.shifts_configured > 0,
    data_freshness_ts: device.last_seen_timestamp,
    version: state.fleetVersion,
  };
}

function buildDashboardSummary(state, plantId) {
  const devices = state.devices.filter((device) => !plantId || device.plant_id === plantId);
  const fleetItems = devices.map((device) => buildFleetItem(state, device));
  const healthScores = devices
    .map((device) => buildHealthScore(state, device.device_id)?.health_score)
    .filter((value) => typeof value === "number");
  const systemHealth = healthScores.length > 0
    ? healthScores.reduce((sum, value) => sum + value, 0) / healthScores.length
    : null;
  const activeAlerts = devices.reduce((sum, device) => sum + unreadCount(state, device.device_id), 0);
  const uptimeConfigured = devices.filter((device) => listShifts(state, device.device_id).length > 0).length;
  const healthConfigured = devices.filter((device) => listHealthConfigs(state, device.device_id).length > 0).length;
  const statusCounts = {
    unknown: fleetItems.filter((item) => item.operational_status === "unknown").length,
    stopped: fleetItems.filter((item) => item.operational_status === "stopped").length,
    idle: fleetItems.filter((item) => item.operational_status === "idle").length,
    running: fleetItems.filter((item) => item.operational_status === "running").length,
    overconsumption: fleetItems.filter((item) => item.operational_status === "overconsumption").length,
  };
  const costDataState = state.dashboardCostStateOverride ?? (state.tariff.rate == null ? "unavailable" : "fresh");
  const costDataReasons = state.dashboardCostReasonsOverride.length > 0
    ? clone(state.dashboardCostReasonsOverride)
    : (state.tariff.rate == null ? ["tariff_not_configured"] : []);

  return {
    generated_at: isoAt(0),
    stale: false,
    warnings: [],
    summary: {
      total_devices: devices.length,
      running_devices: statusCounts.running,
      stopped_devices: statusCounts.stopped,
      idle_devices: statusCounts.idle,
      in_load_devices: statusCounts.running,
      overconsumption_devices: statusCounts.overconsumption,
      unknown_devices: statusCounts.unknown,
      status_counts: statusCounts,
      devices_with_health_data: healthConfigured,
      devices_with_health_configured: healthConfigured,
      devices_missing_health_config: Math.max(devices.length - healthConfigured, 0),
      devices_with_uptime_configured: uptimeConfigured,
      devices_missing_uptime_config: Math.max(devices.length - uptimeConfigured, 0),
      system_health: systemHealth,
      average_efficiency: 91.2,
    },
    alerts: {
      active_alerts: activeAlerts,
      alerts_triggered: activeAlerts,
      alerts_cleared: 0,
      rules_created: devices.reduce((sum, device) => sum + listRules(state, device.device_id).length, 0),
    },
    devices: devices.map((device) => ({
      device_id: device.device_id,
      device_name: device.device_name,
      device_type: device.device_type,
      plant_id: device.plant_id,
      runtime_status: device.runtime_status,
      operational_status: "running",
      location: device.location,
      first_telemetry_timestamp: device.first_telemetry_timestamp,
      last_seen_timestamp: device.last_seen_timestamp,
      health_score: buildHealthScore(state, device.device_id)?.health_score ?? null,
      uptime_percentage: buildUptime(state, device.device_id).uptime_percentage,
    })),
    cost_data_state: costDataState,
    cost_data_reasons: costDataReasons,
    cost_generated_at: costDataState === "unavailable" ? null : state.tariff.updated_at,
    energy_widgets: {
      month_energy_kwh: 512.4,
      month_energy_cost_inr: state.tariff.rate == null ? 0 : Number((512.4 * state.tariff.rate).toFixed(2)),
      today_energy_kwh: 25.4,
      today_energy_cost_inr: state.tariff.rate == null ? 0 : Number((25.4 * state.tariff.rate).toFixed(2)),
      today_loss_kwh: 5.6,
      today_loss_cost_inr: state.tariff.rate == null ? 0 : Number((5.6 * state.tariff.rate).toFixed(2)),
      generated_at: isoAt(0),
      currency: state.tariff.currency,
      data_quality: "ok",
      invariant_checks: {},
      no_nan_inf: true,
    },
  };
}

function buildDashboardBootstrap(state, deviceId) {
  const device = getDevice(state, deviceId);
  const healthScore = buildHealthScore(state, deviceId);

  return {
    generated_at: isoAt(0),
    version: state.fleetVersion,
    device,
    telemetry: telemetryRows(deviceId),
    uptime: buildUptime(state, deviceId),
    shifts: listShifts(state, deviceId),
    health_configs: listHealthConfigs(state, deviceId),
    health_score: healthScore,
    widget_config: buildWidgetConfig(state, deviceId),
    current_state: buildCurrentState(deviceId),
    idle_stats: null,
    idle_config: {
      device_id: deviceId,
      full_load_current_a: 18,
      idle_threshold_pct_of_fla: 25,
      derived_idle_threshold_a: 4.5,
      derived_overconsumption_threshold_a: 16,
      idle_current_threshold: 4.5,
      configured: true,
    },
    waste_config: {
      device_id: deviceId,
      full_load_current_a: 18,
      idle_threshold_pct_of_fla: 25,
      derived_idle_threshold_a: 4.5,
      derived_overconsumption_threshold_a: 16,
      overconsumption_current_threshold_a: 16,
      unoccupied_weekday_start_time: "20:00",
      unoccupied_weekday_end_time: "06:00",
      unoccupied_weekend_start_time: "20:00",
      unoccupied_weekend_end_time: "06:00",
      has_device_override: false,
    },
    loss_stats: buildLossStats(state, deviceId),
  };
}

function buildMonthlyCalendar(state, year, month) {
  const rate = state.tariff.rate ?? 8.5;
  const days = [1, 2, 3, 4, 5].map((day, index) => {
    const energyKwh = 100 + index * 6;
    return {
      date: `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
      energy_kwh: energyKwh,
      energy_cost_inr: Number((energyKwh * rate).toFixed(2)),
    };
  });

  return {
    year,
    month,
    currency: state.tariff.currency,
    generated_at: isoAt(0),
    stale: false,
    warnings: [],
    cost_data_state: state.tariff.rate == null ? "stale" : "fresh",
    cost_data_reasons: state.tariff.rate == null ? ["tariff_not_configured"] : [],
    cost_generated_at: state.tariff.rate == null ? null : state.tariff.updated_at,
    summary: {
      total_energy_kwh: days.reduce((sum, day) => sum + day.energy_kwh, 0),
      total_energy_cost_inr: days.reduce((sum, day) => sum + day.energy_cost_inr, 0),
    },
    days,
    data_quality: "ok",
  };
}

function createTariffVersion(state, rate, currency, updatedBy = "settings-ui") {
  const version = {
    id: `tariff-version-${state.tariffVersionCounter++}`,
    rate: Number(rate),
    currency,
    updated_at: isoAt(8 + state.tariffVersionCounter),
    effective_from: isoAt(8 + state.tariffVersionCounter),
    updated_by: updatedBy,
    is_active: true,
  };
  state.tariffHistory = state.tariffHistory.map((entry) => ({ ...entry, is_active: false }));
  state.tariffHistory.unshift(version);
  state.tariff = {
    id: version.id,
    rate: version.rate,
    currency: version.currency,
    updated_at: version.updated_at,
    updated_by: updatedBy,
    effective_from: version.effective_from,
    is_active: true,
  };
  return version;
}

function createRuleRecord(state, requestBody) {
  const ruleId = `rule-${state.ruleCounter++}`;
  return {
    rule_id: ruleId,
    rule_name: requestBody.rule_name,
    description: requestBody.description ?? null,
    rule_type: requestBody.rule_type,
    scope: requestBody.scope,
    property: requestBody.property ?? null,
    condition: requestBody.condition ?? null,
    threshold: requestBody.threshold ?? null,
    time_window_start: requestBody.time_window_start ?? null,
    time_window_end: requestBody.time_window_end ?? null,
    timezone: requestBody.timezone ?? "Asia/Kolkata",
    time_condition: requestBody.time_condition ?? null,
    duration_minutes: requestBody.duration_minutes ?? null,
    notification_channels: requestBody.notification_channels ?? [],
    notification_recipients: requestBody.notification_recipients ?? [],
    cooldown_minutes: requestBody.cooldown_minutes ?? 15,
    cooldown_seconds: requestBody.cooldown_seconds ?? 900,
    cooldown_unit: requestBody.cooldown_unit ?? "minutes",
    cooldown_mode: requestBody.cooldown_mode ?? "interval",
    triggered_once: false,
    device_ids: requestBody.device_ids ?? [],
    status: "active",
    created_at: isoAt(5),
    updated_at: isoAt(5),
    last_triggered_at: null,
  };
}

function createAlertEvent(state, deviceId, rule) {
  const eventId = `event-${state.eventCounter++}`;
  return {
    event_id: eventId,
    tenant_id: state.tenantId,
    device_id: deviceId,
    rule_id: rule.rule_id,
    alert_id: `alert-${eventId}`,
    event_type: "alert_triggered",
    title: "Idle duration alert",
    message: "Compressor Line A remained idle for 45 minutes.",
    metadata_json: {
      duration_minutes: rule.duration_minutes,
      rule_name: rule.rule_name,
    },
    is_read: false,
    read_at: null,
    created_at: isoAt(6),
  };
}

async function installJourneyHappyPathHarness(page) {
  const state = createJourneyState();
  const accessToken = buildAccessToken(state);

  async function handleHarnessRoute(route) {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/backend/auth/api/v1/auth/login" && method === "POST") {
      state.loggedIn = true;
      await fulfillJson(route, {
        access_token: accessToken,
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/auth/me" && method === "GET") {
      if (!state.loggedIn) {
        await fulfillJson(route, { detail: "Not authenticated" }, 401);
        return;
      }
      await fulfillJson(route, state.me);
      return;
    }

    if (path === "/backend/auth/api/v1/auth/refresh" && method === "POST") {
      if (!state.loggedIn) {
        await fulfillJson(route, { detail: "No active session" }, 401);
        return;
      }
      await fulfillJson(route, {
        access_token: accessToken,
        token_type: "bearer",
        expires_in: 3600,
      });
      return;
    }

    if (path === "/backend/auth/api/v1/platform-maintenance/current" && method === "GET") {
      await fulfillJson(route, {
        tenant_id: state.tenantId,
        announcements: [],
      });
      return;
    }

    if (path === `/backend/auth/api/v1/tenants/${state.tenantId}/plants` && method === "GET") {
      await fulfillJson(route, clone(state.plants));
      return;
    }

    if (path === "/backend/device/api/v1/devices/onboard" && method === "POST") {
      const body = request.postDataJSON();
      const deviceId = `AD${String(state.deviceCounter).padStart(8, "0")}`;
      state.deviceCounter += 1;

      const device = {
        device_id: deviceId,
        device_name: body.device_name,
        device_type: body.device_type,
        device_id_class: body.device_id_class,
        plant_id: body.plant_id,
        data_source_type: body.data_source_type,
        status: "active",
        runtime_status: "running",
        location: body.location ?? "",
        first_telemetry_timestamp: isoAt(-15),
        last_seen_timestamp: isoAt(0),
      };
      state.devices.push(device);
      state.healthConfigsByDevice[deviceId] = [];
      state.shiftsByDevice[deviceId] = [];
      state.rulesByDevice[deviceId] = [];
      state.activityEventsByDevice[deviceId] = [];
      state.maintenanceByDevice[deviceId] = [];
      state.widgetConfigByDevice[deviceId] = buildWidgetConfig(state, deviceId);
      state.mqttByDevice[deviceId] = {
        device_id: deviceId,
        tenant_id: state.tenantId,
        broker_host: "broker.factory.local",
        broker_port: 1883,
        publish_topic: `${state.tenantId}/devices/${deviceId}/telemetry`,
        status_topic: `${state.tenantId}/devices/${deviceId}/status`,
        subscribe_topic: `${state.tenantId}/devices/${deviceId}/cmd`,
        subscribe_topics: [
          `${state.tenantId}/devices/${deviceId}/cmd`,
          `${state.tenantId}/devices/${deviceId}/config`,
          `${state.tenantId}/devices/${deviceId}/ota`,
        ],
        username: `device:${state.tenantId}:${deviceId}`,
        credential_version: 1,
        status: "active",
        rotated_at: isoAt(0),
        revoked_at: null,
        password_visible_once: false,
      };
      state.fleetVersion += 1;

      await fulfillJson(route, {
        success: true,
        data: {
          device,
          mqtt: {
            broker_host: "broker.factory.local",
            broker_port: 1883,
            tenant_id: state.tenantId,
            device_id: deviceId,
            username: `device:${state.tenantId}:${deviceId}`,
            password: "one-time-mqtt-secret",
            publish_topic: `${state.tenantId}/devices/${deviceId}/telemetry`,
            status_topic: `${state.tenantId}/devices/${deviceId}/status`,
            subscribe_topics: [
              `${state.tenantId}/devices/${deviceId}/cmd`,
              `${state.tenantId}/devices/${deviceId}/config`,
              `${state.tenantId}/devices/${deviceId}/ota`,
            ],
          },
        },
      }, 201);
      return;
    }

    if (path === "/backend/device/api/v1/devices" && method === "GET") {
      await fulfillJson(route, {
        success: true,
        data: state.devices,
        total: state.devices.length,
        page: 1,
        page_size: state.devices.length || 1,
        total_pages: 1,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/dashboard/summary" && method === "GET") {
      await fulfillJson(route, buildDashboardSummary(state, url.searchParams.get("plant_id")));
      return;
    }

    if (path === "/backend/device/api/v1/devices/dashboard/fleet-snapshot" && method === "GET") {
      const plantId = url.searchParams.get("plant_id");
      const devices = state.devices
        .filter((device) => !plantId || device.plant_id === plantId)
        .map((device) => buildFleetItem(state, device));
      await fulfillJson(route, {
        generated_at: isoAt(0),
        total: devices.length,
        page: 1,
        page_size: Number(url.searchParams.get("page_size") ?? 60),
        total_pages: 1,
        devices,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/dashboard/fleet-stream" && method === "GET") {
      const devices = state.devices.map((device) => buildFleetItem(state, device));
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          "id: 1\n" +
          "event: heartbeat\n" +
          `data: ${JSON.stringify({
            id: "1",
            event: "heartbeat",
            generated_at: isoAt(0),
            freshness_ts: isoAt(0),
            stale: false,
            warnings: [],
            devices,
            partial: false,
            version: state.fleetVersion,
          })}\n\n`,
      });
      return;
    }

    if (path === "/backend/device/api/v1/devices/calendar/monthly-energy" && method === "GET") {
      const year = Number(url.searchParams.get("year") ?? "2026");
      const month = Number(url.searchParams.get("month") ?? "5");
      const plantId = url.searchParams.get("plant_id");
      if (plantId) {
        await fulfillJson(route, {
          ...buildMonthlyCalendar(state, year, month),
          days: buildMonthlyCalendar(state, year, month).days.map((day) => ({
            ...day,
            energy_kwh: Number((day.energy_kwh * 0.5).toFixed(2)),
            energy_cost_inr: Number((day.energy_cost_inr * 0.5).toFixed(2)),
          })),
          summary: {
            total_energy_kwh: Number((buildMonthlyCalendar(state, year, month).summary.total_energy_kwh * 0.5).toFixed(2)),
            total_energy_cost_inr: Number((buildMonthlyCalendar(state, year, month).summary.total_energy_cost_inr * 0.5).toFixed(2)),
          },
        });
        return;
      }
      await fulfillJson(route, buildMonthlyCalendar(state, year, month));
      return;
    }

    if (path === "/backend/reporting/api/v1/settings/tariff" && method === "GET") {
      await fulfillJson(route, clone(state.tariff));
      return;
    }

    if (path === "/backend/reporting/api/v1/settings/tariff" && method === "POST") {
      const body = request.postDataJSON();
      createTariffVersion(state, body.rate, body.currency, body.updated_by ?? "settings-ui");
      await fulfillJson(route, clone(state.tariff));
      return;
    }

    if (path === "/backend/reporting/api/v1/settings/tariff/history" && method === "GET") {
      await fulfillJson(route, { versions: clone(state.tariffHistory) });
      return;
    }

    const tariffActivateMatch = path.match(/^\/backend\/reporting\/api\/v1\/settings\/tariff\/history\/([^/]+)\/activate$/);
    if (tariffActivateMatch && method === "PATCH") {
      const version = state.tariffHistory.find((entry) => entry.id === tariffActivateMatch[1]);
      if (!version) {
        await fulfillJson(route, { message: "TARIFF_VERSION_NOT_FOUND" }, 404);
        return;
      }
      state.tariffHistory = state.tariffHistory.map((entry) => ({
        ...entry,
        is_active: entry.id === version.id,
      }));
      state.tariff = {
        id: version.id,
        rate: version.rate,
        currency: version.currency,
        updated_at: version.updated_at,
        updated_by: version.updated_by,
        effective_from: version.effective_from,
        is_active: true,
      };
      await fulfillJson(route, clone(state.tariff));
      return;
    }

    if ((path === "/backend/reporting/api/v1/history" || path === "/api/reports/history") && method === "GET") {
      await fulfillJson(route, { reports: [] });
      return;
    }

    if ((path === "/backend/reporting/api/v1/schedules" || path === "/api/reports/schedules") && method === "GET") {
      await fulfillJson(route, { schedules: clone(state.schedules) });
      return;
    }

    if ((path === "/backend/reporting/api/v1/schedules" || path === "/api/reports/schedules") && method === "POST") {
      const body = request.postDataJSON();
      const schedule = {
        schedule_id: `schedule-${state.scheduleCounter++}`,
        tenant_id: state.tenantId,
        report_type: body.report_type,
        frequency: body.frequency,
        is_active: true,
        next_run_at: isoAt(90),
        last_run_at: null,
        last_status: "pending",
        last_result_url: null,
        created_at: isoAt(0),
        params_template: clone(body.params_template ?? { device_ids: [] }),
      };
      state.schedules.unshift(schedule);
      await fulfillJson(route, schedule, 201);
      return;
    }

    const scheduleMatch = path.match(/^\/(?:backend\/reporting\/api\/v1|api\/reports)\/schedules\/([^/]+)$/);
    if (scheduleMatch && method === "PUT") {
      const body = request.postDataJSON();
      let updated = null;
      state.schedules = state.schedules.map((schedule) => {
        if (schedule.schedule_id !== scheduleMatch[1]) return schedule;
        updated = {
          ...schedule,
          report_type: body.report_type ?? schedule.report_type,
          frequency: body.frequency ?? schedule.frequency,
          params_template: clone(body.params_template ?? schedule.params_template),
        };
        return updated;
      });
      await fulfillJson(route, updated);
      return;
    }

    if (scheduleMatch && method === "DELETE") {
      state.schedules = state.schedules.map((schedule) =>
        schedule.schedule_id === scheduleMatch[1]
          ? { ...schedule, is_active: false, last_status: "cancelled" }
          : schedule,
      );
      await fulfillJson(route, { message: "Schedule deactivated" });
      return;
    }

    if (path.startsWith("/backend/device/api/v1/devices/")) {
      const parts = path.split("/").filter(Boolean);
      const deviceId = parts[5];
      const suffix = parts.slice(6).join("/");
      const device = getDevice(state, deviceId);

      if (!device) {
        await fulfillJson(route, { detail: { message: "Device not found" } }, 404);
        return;
      }

      if (suffix === "" && method === "GET") {
        await fulfillJson(route, { success: true, data: device });
        return;
      }

      if (suffix === "dashboard-bootstrap" && method === "GET") {
        await fulfillJson(route, buildDashboardBootstrap(state, deviceId));
        return;
      }

      if (suffix === "mqtt-credential" && method === "GET") {
        await fulfillJson(route, clone(state.mqttByDevice[deviceId]));
        return;
      }

      if (suffix === "mqtt-credential/rotate" && method === "POST") {
        const nextVersion = (state.mqttByDevice[deviceId]?.credential_version ?? 0) + 1;
        const status = {
          ...state.mqttByDevice[deviceId],
          username: `device:${state.tenantId}:${deviceId}:v${nextVersion}`,
          credential_version: nextVersion,
          status: "active",
          rotated_at: isoAt(14),
          revoked_at: null,
          password_visible_once: false,
        };
        state.mqttByDevice[deviceId] = status;
        await fulfillJson(route, {
          status,
          mqtt: {
            broker_host: status.broker_host,
            broker_port: status.broker_port,
            tenant_id: status.tenant_id,
            device_id: deviceId,
            username: status.username,
            password: `rotated-secret-${nextVersion}`,
            publish_topic: status.publish_topic,
            status_topic: status.status_topic,
            subscribe_topics: status.subscribe_topics,
          },
        });
        return;
      }

      if (suffix === "mqtt-credential/revoke" && method === "POST") {
        state.mqttByDevice[deviceId] = {
          ...state.mqttByDevice[deviceId],
          status: "revoked",
          revoked_at: isoAt(15),
          password_visible_once: false,
        };
        await fulfillJson(route, clone(state.mqttByDevice[deviceId]));
        return;
      }

      if (suffix === "dashboard-widgets" && method === "GET") {
        await fulfillJson(route, buildWidgetConfig(state, deviceId));
        return;
      }

      if (suffix === "dashboard-widgets" && method === "PUT") {
        const body = request.postDataJSON();
        state.widgetConfigByDevice[deviceId] = {
          device_id: deviceId,
          available_fields: ["power", "current", "voltage"],
          selected_fields: body.selected_fields ?? [],
          effective_fields: body.selected_fields ?? [],
          default_applied: false,
        };
        await fulfillJson(route, buildWidgetConfig(state, deviceId));
        return;
      }

      if (suffix === "current-state" && method === "GET") {
        await fulfillJson(route, buildCurrentState(deviceId));
        return;
      }

      if (suffix === "loss-stats" && method === "GET") {
        await fulfillJson(route, buildLossStats(state, deviceId));
        return;
      }

      if (suffix === "maintenance-log/summary" && method === "GET") {
        await fulfillJson(route, {
          success: true,
          data: {
            total_records: 0,
            total_cost: 0,
            latest_maintenance_date: null,
            last_recorded_at: null,
            next_due_date: null,
          },
        });
        return;
      }

      if (suffix === "maintenance-log" && method === "GET") {
        await fulfillJson(route, {
          success: true,
          data: state.maintenanceByDevice[deviceId] ?? [],
        });
        return;
      }

      if (suffix === "maintenance-log" && method === "POST") {
        const body = request.postDataJSON();
        const current = state.maintenanceByDevice[deviceId] ?? [];
        const nextId = Math.max(0, ...current.map((record) => record.id ?? 0)) + 1;
        const record = {
          id: nextId,
          tenant_id: state.tenantId,
          device_id: deviceId,
          maintenance_date: body.maintenance_date,
          title: body.title,
          description: body.description,
          cost: Number(body.cost),
          performed_by: body.performed_by ?? null,
          status: body.status ?? null,
          next_due_date: body.next_due_date ?? null,
          created_by: state.me.user.id,
          created_at: isoAt(10),
          updated_at: isoAt(10),
        };
        state.maintenanceByDevice[deviceId] = [record, ...current];
        await fulfillJson(route, { success: true, data: record }, 201);
        return;
      }

      if (suffix.startsWith("maintenance-log/") && method === "PUT") {
        const maintenanceId = Number(parts[7]);
        const body = request.postDataJSON();
        let updated = null;
        state.maintenanceByDevice[deviceId] = (state.maintenanceByDevice[deviceId] ?? []).map((record) => {
          if (record.id !== maintenanceId) return record;
          updated = {
            ...record,
            maintenance_date: body.maintenance_date,
            title: body.title,
            description: body.description,
            cost: Number(body.cost),
            performed_by: body.performed_by ?? null,
            status: body.status ?? null,
            next_due_date: body.next_due_date ?? null,
            updated_at: isoAt(11),
          };
          return updated;
        });
        await fulfillJson(route, { success: true, data: updated });
        return;
      }

      if (suffix.startsWith("maintenance-log/") && method === "DELETE") {
        if (state.forceMaintenanceDeleteMissing) {
          state.forceMaintenanceDeleteMissing = false;
          await fulfillJson(route, { message: "maintenance_log_not_found" }, 404);
          return;
        }
        const maintenanceId = Number(parts[7]);
        state.maintenanceByDevice[deviceId] = (state.maintenanceByDevice[deviceId] ?? []).filter((record) => record.id !== maintenanceId);
        await fulfillJson(route, { success: true, data: { deleted: true, id: maintenanceId } });
        return;
      }

      if (suffix === "shifts" && method === "GET") {
        await fulfillJson(route, { data: listShifts(state, deviceId) });
        return;
      }

      if (suffix === "shifts" && method === "POST") {
        const body = request.postDataJSON();
        const shift = {
          id: state.shiftCounter++,
          device_id: deviceId,
          shift_name: body.shift_name,
          shift_start: body.shift_start,
          shift_end: body.shift_end,
          maintenance_break_minutes: body.maintenance_break_minutes,
          day_of_week: body.day_of_week ?? null,
          is_active: body.is_active ?? true,
          created_at: isoAt(3),
          updated_at: isoAt(3),
        };
        state.shiftsByDevice[deviceId] = [...listShifts(state, deviceId), shift];
        await fulfillJson(route, { data: shift }, 201);
        return;
      }

      if (suffix.startsWith("shifts/") && method === "DELETE") {
        const shiftId = Number(parts[7]);
        state.shiftsByDevice[deviceId] = listShifts(state, deviceId).filter((shift) => shift.id !== shiftId);
        await route.fulfill({ status: 204, body: "" });
        return;
      }

      if (suffix.startsWith("shifts/") && method === "PUT") {
        const shiftId = Number(parts[7]);
        const body = request.postDataJSON();
        let updated = null;
        state.shiftsByDevice[deviceId] = listShifts(state, deviceId).map((shift) => {
          if (shift.id !== shiftId) return shift;
          updated = {
            ...shift,
            shift_name: body.shift_name ?? shift.shift_name,
            shift_start: body.shift_start ?? shift.shift_start,
            shift_end: body.shift_end ?? shift.shift_end,
            maintenance_break_minutes: body.maintenance_break_minutes ?? shift.maintenance_break_minutes,
            day_of_week: body.day_of_week ?? shift.day_of_week,
            updated_at: isoAt(4),
          };
          return updated;
        });
        await fulfillJson(route, { data: updated });
        return;
      }

      if (suffix === "uptime" && method === "GET") {
        await fulfillJson(route, buildUptime(state, deviceId));
        return;
      }

      if (suffix === "health-config" && method === "GET") {
        await fulfillJson(route, { data: listHealthConfigs(state, deviceId) });
        return;
      }

      if (suffix === "health-config" && method === "POST") {
        const body = request.postDataJSON();
        const record = {
          id: state.healthConfigCounter++,
          device_id: deviceId,
          parameter_name: body.parameter_name,
          normal_min: body.normal_min ?? null,
          normal_max: body.normal_max ?? null,
          weight: body.weight,
          ignore_zero_value: Boolean(body.ignore_zero_value),
          is_active: body.is_active ?? true,
          created_at: isoAt(2),
          updated_at: isoAt(2),
        };
        state.healthConfigsByDevice[deviceId] = [...listHealthConfigs(state, deviceId), record];
        await fulfillJson(route, { data: record }, 201);
        return;
      }

      if (suffix.startsWith("health-config/") && method === "PUT") {
        const configId = Number(parts[7]);
        const body = request.postDataJSON();
        let updated = null;
        state.healthConfigsByDevice[deviceId] = listHealthConfigs(state, deviceId).map((config) => {
          if (config.id !== configId) return config;
          updated = {
            ...config,
            parameter_name: body.parameter_name ?? config.parameter_name,
            normal_min: body.normal_min ?? null,
            normal_max: body.normal_max ?? null,
            weight: body.weight ?? config.weight,
            ignore_zero_value: body.ignore_zero_value ?? config.ignore_zero_value,
            is_active: body.is_active ?? config.is_active,
            updated_at: isoAt(2),
          };
          return updated;
        });
        await fulfillJson(route, { data: updated });
        return;
      }

      if (suffix.startsWith("health-config/") && method === "DELETE") {
        const configId = Number(parts[7]);
        state.healthConfigsByDevice[deviceId] = listHealthConfigs(state, deviceId).filter((config) => config.id !== configId);
        await route.fulfill({ status: 204, body: "" });
        return;
      }

      if (suffix === "health-score" && method === "POST") {
        await fulfillJson(route, buildHealthScore(state, deviceId));
        return;
      }

      if (suffix === "performance-trends" && method === "GET") {
        const metric = url.searchParams.get("metric") ?? "health";
        await fulfillJson(route, {
          device_id: deviceId,
          metric,
          range: url.searchParams.get("range") ?? "24h",
          interval_minutes: 60,
          timezone: "Asia/Kolkata",
          points: [
            {
              timestamp: isoAt(-120),
              health_score: metric === "health" ? 88 : null,
              uptime_percentage: metric === "uptime" ? 95 : null,
              planned_minutes: 480,
              effective_minutes: 450,
              break_minutes: 30,
            },
            {
              timestamp: isoAt(0),
              health_score: metric === "health" ? 92 : null,
              uptime_percentage: metric === "uptime" ? 96.5 : null,
              planned_minutes: 480,
              effective_minutes: 450,
              break_minutes: 30,
            },
          ],
          total_points: 2,
          sampled_points: 2,
          message: "Trend data loaded",
          metric_message: "Trend data loaded",
          range_start: isoAt(-120),
          range_end: isoAt(0),
          is_stale: false,
          last_actual_timestamp: isoAt(0),
          fallback_point: null,
        });
        return;
      }
    }

    if (path.startsWith("/backend/rule-engine/api/v1/rules")) {
      if (path === "/backend/rule-engine/api/v1/rules" && method === "GET") {
        const deviceId = url.searchParams.get("device_id");
        const rules = deviceId ? listRules(state, deviceId) : Object.values(state.rulesByDevice).flat();
        await fulfillJson(route, {
          data: rules,
          total: rules.length,
          page: 1,
          page_size: 20,
          total_pages: 1,
        });
        return;
      }

      if (path === "/backend/rule-engine/api/v1/rules" && method === "POST") {
        const body = request.postDataJSON();
        const rule = createRuleRecord(state, body);
        const deviceId = rule.device_ids[0];
        state.rulesByDevice[deviceId] = [...listRules(state, deviceId), rule];
        state.activityEventsByDevice[deviceId] = [createAlertEvent(state, deviceId, rule), ...listEvents(state, deviceId)];
        state.fleetVersion += 1;
        await fulfillJson(route, { data: rule }, 201);
        return;
      }

      const ruleIdMatch = path.match(/^\/backend\/rule-engine\/api\/v1\/rules\/([^/]+)$/);
      if (ruleIdMatch && method === "PUT") {
        if (state.forceRuleMutationDenied) {
          state.forceRuleMutationDenied = false;
          await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
          return;
        }
        const body = request.postDataJSON();
        let updatedRule = null;
        Object.keys(state.rulesByDevice).forEach((deviceId) => {
          state.rulesByDevice[deviceId] = listRules(state, deviceId).map((rule) => {
            if (rule.rule_id !== ruleIdMatch[1]) return rule;
            updatedRule = {
              ...rule,
              rule_name: body.rule_name ?? rule.rule_name,
              description: body.description ?? rule.description,
              rule_type: body.rule_type ?? rule.rule_type,
              property: body.property ?? rule.property,
              condition: body.condition ?? rule.condition,
              threshold: body.threshold ?? rule.threshold,
              time_window_start: body.time_window_start ?? rule.time_window_start,
              time_window_end: body.time_window_end ?? rule.time_window_end,
              time_condition: body.time_condition ?? rule.time_condition,
              duration_minutes: body.duration_minutes ?? rule.duration_minutes,
              notification_channels: body.notification_channels ?? rule.notification_channels,
              notification_recipients: body.notification_recipients ?? rule.notification_recipients,
              cooldown_minutes: body.cooldown_minutes ?? rule.cooldown_minutes,
              cooldown_seconds: body.cooldown_seconds ?? rule.cooldown_seconds,
              cooldown_unit: body.cooldown_unit ?? rule.cooldown_unit,
              cooldown_mode: body.cooldown_mode ?? rule.cooldown_mode,
              updated_at: isoAt(12),
            };
            return updatedRule;
          });
        });
        await fulfillJson(route, { data: updatedRule });
        return;
      }

      const ruleStatusMatch = path.match(/^\/backend\/rule-engine\/api\/v1\/rules\/([^/]+)\/status$/);
      if (ruleStatusMatch && method === "PATCH") {
        if (state.forceRuleMutationDenied) {
          state.forceRuleMutationDenied = false;
          await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
          return;
        }
        const body = request.postDataJSON();
        let updatedRule = null;
        Object.keys(state.rulesByDevice).forEach((deviceId) => {
          state.rulesByDevice[deviceId] = listRules(state, deviceId).map((rule) => {
            if (rule.rule_id !== ruleStatusMatch[1]) return rule;
            updatedRule = {
              ...rule,
              status: body.status,
              updated_at: isoAt(12),
            };
            return updatedRule;
          });
        });
        await fulfillJson(route, { data: updatedRule });
        return;
      }

      if (ruleIdMatch && method === "DELETE") {
        if (state.forceRuleMutationDenied) {
          state.forceRuleMutationDenied = false;
          await fulfillJson(route, { message: "Forbidden: you cannot modify rules outside your scope." }, 403);
          return;
        }
        Object.keys(state.rulesByDevice).forEach((deviceId) => {
          state.rulesByDevice[deviceId] = listRules(state, deviceId).filter((rule) => rule.rule_id !== ruleIdMatch[1]);
        });
        await fulfillJson(route, { data: { deleted: true } });
        return;
      }
    }

    if (path === "/backend/rule-engine/api/v1/alerts/events/unread-count" && method === "GET") {
      const deviceId = url.searchParams.get("device_id");
      const count = deviceId
        ? unreadCount(state, deviceId)
        : Object.keys(state.activityEventsByDevice).reduce((sum, key) => sum + unreadCount(state, key), 0);
      await fulfillJson(route, { data: { count } });
      return;
    }

    if (path === "/backend/rule-engine/api/v1/alerts/events" && method === "GET") {
      const deviceId = url.searchParams.get("device_id");
      const events = deviceId ? listEvents(state, deviceId) : Object.values(state.activityEventsByDevice).flat();
      await fulfillJson(route, {
        data: events,
        total: events.length,
        page: 1,
        page_size: 20,
        total_pages: Math.max(events.length > 0 ? 1 : 0, 0),
      });
      return;
    }

    if (path === "/backend/rule-engine/api/v1/alerts/events/mark-all-read" && method === "PATCH") {
      const deviceId = url.searchParams.get("device_id");
      const touched = deviceId ? [deviceId] : Object.keys(state.activityEventsByDevice);
      touched.forEach((id) => {
        state.activityEventsByDevice[id] = listEvents(state, id).map((event) => ({
          ...event,
          is_read: true,
          read_at: isoAt(9),
        }));
      });
      await fulfillJson(route, { data: { updated: true } });
      return;
    }

    if (path === "/backend/rule-engine/api/v1/alerts/events" && method === "DELETE") {
      const deviceId = url.searchParams.get("device_id");
      if (deviceId) {
        state.activityEventsByDevice[deviceId] = [];
      }
      await fulfillJson(route, { data: { deleted: true } });
      return;
    }

    const alertActionMatch = path.match(/^\/backend\/rule-engine\/api\/v1\/alerts\/([^/]+)\/(acknowledge|resolve)$/);
    if (alertActionMatch) {
      const [, alertId, action] = alertActionMatch;
      Object.keys(state.activityEventsByDevice).forEach((deviceId) => {
        const existing = listEvents(state, deviceId);
        const matchingEvent = existing.find((event) => event.alert_id === alertId);
        if (!matchingEvent) return;
        state.activityEventsByDevice[deviceId] = [
          {
            event_id: `event-${state.eventCounter++}`,
            tenant_id: state.tenantId,
            device_id: deviceId,
            rule_id: matchingEvent.rule_id,
            alert_id: alertId,
            event_type: action === "acknowledge" ? "alert_acknowledged" : "alert_resolved",
            title: action === "acknowledge" ? "Alert acknowledged" : "Alert resolved",
            message: action === "acknowledge" ? "Operator acknowledged this alert." : "Operator resolved this alert.",
            metadata_json: {},
            is_read: true,
            read_at: isoAt(16),
            created_at: isoAt(16),
          },
          ...existing.map((event) =>
            event.alert_id === alertId ? { ...event, is_read: true, read_at: isoAt(16) } : event,
          ),
        ];
      });
      await fulfillJson(route, { success: true });
      return;
    }

    if (path.startsWith("/backend/data/api/v1/data/telemetry/") && method === "GET") {
      const parts = path.split("/").filter(Boolean);
      const deviceId = parts[6];
      const rows = telemetryRows(deviceId);

      if (parts[7] === "latest") {
        await fulfillJson(route, { data: { item: rows[0] } });
        return;
      }

      if (url.searchParams.get("limit") === "1") {
        await fulfillJson(route, { success: true, data: { items: [rows[0]] } });
        return;
      }

      await fulfillJson(route, { success: true, data: { items: rows } });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "text/plain",
      body: `Unmocked route: ${method} ${path}`,
    });
  }

  await page.route("**/backend/**", handleHarnessRoute);
  await page.route("**/api/reports/**", handleHarnessRoute);

  return {
    state,
    accessToken,
    denyNextRuleMutation() {
      state.forceRuleMutationDenied = true;
    },
    failNextMaintenanceDeleteAsMissing() {
      state.forceMaintenanceDeleteMissing = true;
    },
    setDashboardCostState(stateName, reasons = []) {
      state.dashboardCostStateOverride = stateName;
      state.dashboardCostReasonsOverride = reasons;
    },
  };
}

module.exports = {
  installJourneyHappyPathHarness,
};
