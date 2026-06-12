/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installJourneyHappyPathHarness } = require("./support/journeyHappyPathHarness.js");

async function fulfillJson(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

async function fulfillPdf(route, filename = "artifact.pdf") {
  await route.fulfill({
    status: 200,
    contentType: "application/pdf",
    headers: {
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
    body: "%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
  });
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function base64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function withPremiumModules(state) {
  const premiumModules = ["analytics", "reports", "waste_analysis", "copilot"];
  const entitlements = state.me.entitlements;

  for (const role of Object.keys(entitlements.role_feature_matrix)) {
    const current = new Set(entitlements.role_feature_matrix[role] ?? []);
    if (role === "org_admin" || role === "super_admin") {
      premiumModules.forEach((module) => current.add(module));
    }
    entitlements.role_feature_matrix[role] = Array.from(current);
    entitlements.baseline_features_by_role[role] = Array.from(current);
    entitlements.effective_features_by_role[role] = Array.from(current);
  }

  const available = new Set(entitlements.available_features ?? []);
  premiumModules.forEach((module) => available.add(module));
  entitlements.available_features = Array.from(available);
  entitlements.premium_feature_grants = premiumModules;
}

async function seedAuthenticatedSession(page, harness) {
  harness.state.loggedIn = true;
  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", snapshot.tenantId);
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));

    const jsonResponse = (body, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: {
          "Content-Type": "application/json",
        },
      });

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
      const parsedUrl = new URL(url, window.location.origin);
      if (
        parsedUrl.pathname.includes("/api/v1/platform-maintenance/current") ||
        parsedUrl.pathname.includes("/backend/auth/api/v1/platform-maintenance/current")
      ) {
        return jsonResponse({
          tenant_id: snapshot.tenantId,
          announcements: [],
        });
      }
      if (
        parsedUrl.pathname.includes(`/api/v1/tenants/${snapshot.tenantId}/plants`) ||
        parsedUrl.pathname.includes(`/backend/auth/api/v1/tenants/${snapshot.tenantId}/plants`)
      ) {
        return jsonResponse([snapshot.plant]);
      }
      if (parsedUrl.pathname === "/backend/device/api/v1/devices") {
        return jsonResponse({
          success: true,
          data: snapshot.devices,
        });
      }
      return originalFetch(input, init);
    };
  }, {
    accessToken: harness.accessToken,
    tenantId: harness.state.tenantId,
    me: harness.state.me,
    plant: harness.state.plant,
    devices: harness.state.devices,
  });
}

function createAnalyticsResult(jobId) {
  return {
    analysis_type: "anomaly_detection",
    device_id: "AD00000010",
    job_id: jobId,
    health_score: 91,
    confidence_summary: {
      title: "High confidence anomaly summary",
      level: "High",
      evidence_strength: "Strong telemetry coverage",
      summary: "Clear anomaly evidence was found in the selected telemetry range.",
      interpretation: "Power draw spikes were unusual compared with the surrounding baseline.",
      recommended_action: "Inspect the compressor load pattern and verify expected start cycles.",
      factors: ["Power spikes", "Short load bursts"],
    },
    coverage_result: {
      level: "full_coverage",
      usable_for_business_decisions: true,
      summary: "Telemetry coverage is complete for the selected period.",
    },
    confidence: {
      level: "High",
      badge_color: "green",
      banner_text: "Evidence is strong across the selected telemetry range.",
      banner_style: "good",
      days_available: 7,
    },
    summary: {
      total_anomalies: 3,
      anomaly_rate_pct: 4.2,
      anomaly_score: 91,
      health_impact: "Moderate",
      most_affected_parameter: "power",
      data_points_analyzed: 336,
      days_analyzed: 7,
      model_confidence: "High",
      sensitivity: "Balanced",
    },
    anomaly_rate_gauge: {
      value: 4.2,
      max: 10,
      color: "amber",
    },
    parameter_breakdown: [
      {
        parameter: "power",
        anomaly_count: 3,
        anomaly_pct: 4.2,
        severity_distribution: { low: 1, medium: 1, high: 1 },
      },
    ],
    anomalies_over_time: [
      { date: "2026-05-01", count: 1, high_count: 0, medium_count: 1, low_count: 0 },
      { date: "2026-05-02", count: 2, high_count: 1, medium_count: 0, low_count: 1 },
    ],
    anomaly_list: [
      {
        timestamp: "2026-05-02T08:30:00Z",
        severity: "high",
        parameters: ["power"],
        context: "Unexpected power spike during steady operation.",
        reasoning: "The measured power exceeded the learned normal band for this machine.",
        recommended_action: "Review load transitions and verify equipment calibration.",
      },
    ],
    recommendations: [
      {
        rank: 1,
        action: "Inspect load spikes on the compressor",
        urgency: "Medium",
        reasoning: "Spikes are repeatable and exceeded the learned baseline.",
        parameter: "power",
      },
    ],
    metadata: {},
    reasoning: {
      summary: "Repeated spikes suggest a real operational anomaly rather than random noise.",
      affected_parameters: ["power"],
      recommended_action: "Inspect load spikes on the compressor",
      confidence: "High",
    },
    data_quality_flags: [],
  };
}

function createHiddenInsight() {
  return {
    summary: {
      total_hidden_overconsumption_kwh: 12.4,
      total_hidden_overconsumption_cost: 105.4,
      total_baseline_energy_kwh: 48.2,
      aggregate_p75_baseline_reference: 780,
      selected_days: 2,
    },
    daily_breakdown: [
      {
        date: "2026-05-01",
        actual_energy_kwh: 32.1,
        p75_power_baseline_w: 780,
        baseline_energy_kwh: 24.8,
        hidden_overconsumption_kwh: 7.3,
        hidden_overconsumption_cost: 62.05,
        sample_count: 1440,
        covered_duration_hours: 24,
      },
      {
        date: "2026-05-02",
        actual_energy_kwh: 28.5,
        p75_power_baseline_w: 780,
        baseline_energy_kwh: 23.4,
        hidden_overconsumption_kwh: 5.1,
        hidden_overconsumption_cost: 43.35,
        sample_count: 1440,
        covered_duration_hours: 24,
      },
    ],
  };
}

async function installPremiumJourney(page) {
  const harness = await installJourneyHappyPathHarness(page);
  if (harness.state.devices.length === 0) {
    const device = {
      device_id: "AD00000010",
      device_name: "Packaging Line A",
      device_type: "compressor",
      device_id_class: "active",
      plant_id: harness.state.plant.id,
      data_source_type: "metered",
      status: "active",
      runtime_status: "running",
      location: "Pune Bay 1",
      first_telemetry_timestamp: "2026-05-02T08:00:00Z",
      last_seen_timestamp: "2026-05-02T09:00:00Z",
    };
    harness.state.devices.push(device);
    harness.state.healthConfigsByDevice[device.device_id] = [];
    harness.state.shiftsByDevice[device.device_id] = [];
    harness.state.rulesByDevice[device.device_id] = [];
    harness.state.activityEventsByDevice[device.device_id] = [];
    harness.state.maintenanceByDevice[device.device_id] = [];
  }
  withPremiumModules(harness.state);
  await seedAuthenticatedSession(page, harness);
  return harness;
}

async function installReportRoutes(page, harness, options = {}) {
  const reportState = {
    createdReportId: options.createdReportId ?? "report-energy-success",
    history: clone(
      options.history ?? [
        {
          report_id: "report-finalizing",
          status: "completed",
          backend_status: "completed",
          report_type: "consumption",
          progress: 100,
          result_ready: false,
          artifact_ready: false,
          download_ready: false,
          error_code: null,
          error_message: null,
          created_at: "2026-05-01T08:00:00Z",
          completed_at: "2026-05-01T08:10:00Z",
        },
        {
          report_id: "report-no-data",
          status: "failed",
          backend_status: "failed",
          report_type: "consumption",
          progress: 100,
          result_ready: false,
          artifact_ready: false,
          download_ready: false,
          error_code: "NO_TELEMETRY_IN_RANGE",
          error_message: "No telemetry found in selected time range",
          created_at: "2026-05-01T07:00:00Z",
          completed_at: "2026-05-01T07:05:00Z",
        },
      ],
    ),
    schedules: [],
    createdStatusCalls: 0,
    downloadRequests: 0,
    hiddenInsight: createHiddenInsight(),
  };

  await page.route("**/api/reports/energy/consumption", async (route) => {
    reportState.createdStatusCalls = 0;
    reportState.history = [
      {
        report_id: reportState.createdReportId,
        status: "running",
        backend_status: "processing",
        report_type: "consumption",
        progress: 55,
        phase_label: "Calculating cost breakdown",
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        error_code: null,
        error_message: null,
        created_at: "2026-05-02T10:00:00Z",
        completed_at: null,
      },
      ...reportState.history.filter((item) => item.report_id !== reportState.createdReportId),
    ];

    await fulfillJson(route, {
      report_id: reportState.createdReportId,
      status: "running",
      progress: 30,
      phase_label: "Queued for processing",
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
    });
  });

  await page.route(`**/api/reports/${reportState.createdReportId}/status**`, async (route) => {
    reportState.createdStatusCalls += 1;
    reportState.history[0] = {
      ...reportState.history[0],
      status: "completed",
      backend_status: "completed",
      progress: 100,
      phase_label: "Completed",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
      completed_at: "2026-05-02T10:04:00Z",
    };
    await fulfillJson(route, {
      report_id: reportState.createdReportId,
      status: "completed",
      progress: 100,
      phase_label: "Completed",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    });
  });

  await page.route("**/api/reports/report-finalizing/status**", async (route) => {
    await fulfillJson(route, {
      report_id: "report-finalizing",
      status: "completed",
      progress: 100,
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
    });
  });

  await page.route("**/api/reports/report-no-data/status**", async (route) => {
    await fulfillJson(route, {
      report_id: "report-no-data",
      status: "failed",
      progress: 100,
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
      error_code: "NO_TELEMETRY_IN_RANGE",
      error_message: "No telemetry found in selected time range",
    });
  });

  await page.route(`**/api/reports/${reportState.createdReportId}/result**`, async (route) => {
    await fulfillJson(route, {
      summary: {
        total_kwh: 128.4,
        peak_demand_kw: 18.6,
        load_factor_pct: 77.4,
        total_cost: 1091.4,
        currency: harness.state.tariff.currency,
      },
      insights: [
        "Power stayed within expected thresholds for most of the selected period.",
      ],
      hidden_overconsumption_insight: reportState.hiddenInsight,
    });
  });

  await page.route(`**/api/reports/${reportState.createdReportId}/download**`, async (route) => {
    reportState.downloadRequests += 1;
    await fulfillPdf(route, "energy_report_report-energy-success.pdf");
  });

  await page.route("**/api/reports/history**", async (route) => {
    await fulfillJson(route, { reports: clone(reportState.history) });
  });

  await page.route("**/api/reports/schedules**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await fulfillJson(route, { schedules: clone(reportState.schedules) });
      return;
    }

    const body = request.postDataJSON();
    const schedule = {
      schedule_id: "schedule-1",
      tenant_id: harness.state.tenantId,
      report_type: body.report_type,
      frequency: body.frequency,
      is_active: true,
      last_run_at: null,
      next_run_at: "2026-05-03T01:00:00Z",
      last_status: "pending",
      last_result_url: null,
      params_template: body.params_template,
    };
    reportState.schedules = [schedule];
    await fulfillJson(route, {
      schedule_id: schedule.schedule_id,
      tenant_id: schedule.tenant_id,
      report_type: schedule.report_type,
      frequency: schedule.frequency,
      is_active: true,
      next_run_at: schedule.next_run_at,
      created_at: "2026-05-02T10:20:00Z",
    }, 201);
  });

  await page.route("**/api/reports/schedules/*", async (route) => {
    reportState.schedules = reportState.schedules.map((schedule) => ({
      ...schedule,
      is_active: false,
    }));
    await fulfillJson(route, { message: "deactivated" });
  });

  return reportState;
}

async function installWasteRoutes(page) {
  const wasteState = {
    jobId: "waste-job-1",
    history: [],
    downloadRequests: 0,
  };

  await page.route("**/api/waste/analysis/history**", async (route) => {
    await fulfillJson(route, { items: clone(wasteState.history) });
  });

  await page.route("**/api/waste/analysis/run", async (route) => {
    wasteState.history = [
      {
        job_id: wasteState.jobId,
        job_name: "Weekly Waste Review",
        status: "completed",
        progress_pct: 100,
        phase_label: "Completed",
        result_ready: true,
        artifact_ready: true,
        download_ready: true,
        created_at: "2026-05-02T11:00:00Z",
        started_at: "2026-05-02T11:01:00Z",
        completed_at: "2026-05-02T11:04:00Z",
        scope: "all",
        requested_device_count: 1,
        start_date: "2026-04-25",
        end_date: "2026-05-02",
      },
    ];
    await fulfillJson(route, clone(wasteState.history[0]));
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/status`, async (route) => {
    await fulfillJson(route, clone(wasteState.history[0]));
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/result`, async (route) => {
    await fulfillJson(route, {
      total_waste_cost: 420.5,
      total_energy_cost: 2400.75,
      total_energy_kwh: 282.4,
      total_idle_kwh: 31.2,
      insights: ["Overconsumption was concentrated during off-hours."],
      device_summaries: [
        {
          device_id: "AD00000010",
          device_name: "Packaging Line A",
          off_hours: {
            duration_sec: 7200,
            energy_kwh: 12.4,
            cost: 105.4,
          },
          overconsumption: {
            duration_sec: 3600,
            energy_kwh: 8.2,
            cost: 69.7,
          },
        },
      ],
    });
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/download`, async (route) => {
    await fulfillJson(route, {
      job_id: wasteState.jobId,
      status: "completed",
      download_url: "/api/waste/downloads/waste-job-1.pdf",
      expires_in_seconds: 300,
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    });
  });

  await page.route("**/api/waste/downloads/waste-job-1.pdf", async (route) => {
    wasteState.downloadRequests += 1;
    await fulfillPdf(route, "waste_report_waste-job-1.pdf");
  });

  return wasteState;
}

test("analytics page truthfully covers empty history, successful jobs, and failed history states", async ({ page }) => {
  const state = {
    tenantId: "SH00000001",
    plant: {
      id: "plant-1",
      tenant_id: "SH00000001",
      name: "Plant North",
      location: "Pune",
      timezone: "Asia/Kolkata",
      is_active: true,
      created_at: "2026-05-02T08:00:00Z",
    },
    devices: [
      {
        device_id: "AD00000010",
        device_name: "Packaging Line A",
        device_type: "compressor",
        device_id_class: "active",
        plant_id: "plant-1",
        data_source_type: "metered",
        status: "active",
        runtime_status: "running",
        location: "Pune Bay 1",
        first_telemetry_timestamp: "2026-05-02T08:00:00Z",
        last_seen_timestamp: "2026-05-02T09:00:00Z",
      },
    ],
    me: {
      user: {
        id: "user-analytics-1",
        email: "ops@example.com",
        full_name: "Factory Ops Admin",
        role: "org_admin",
        tenant_id: "SH00000001",
        is_active: true,
        created_at: "2026-05-02T08:00:00Z",
        last_login_at: "2026-05-02T09:00:00Z",
      },
      tenant: {
        id: "SH00000001",
        name: "Factory Ops",
        slug: "factory-ops",
        is_active: true,
        created_at: "2026-05-02T08:00:00Z",
      },
      plant_ids: ["plant-1"],
      entitlements: {
        premium_feature_grants: [],
        role_feature_matrix: {
          super_admin: ["machines", "calendar", "rules", "reports", "settings"],
          org_admin: ["machines", "calendar", "rules", "reports", "settings"],
          plant_manager: ["machines", "calendar", "rules", "reports"],
          operator: ["machines", "calendar", "rules"],
          viewer: ["machines", "calendar"],
        },
        baseline_features_by_role: {
          super_admin: ["machines", "calendar", "rules", "reports", "settings"],
          org_admin: ["machines", "calendar", "rules", "reports", "settings"],
          plant_manager: ["machines", "calendar", "rules", "reports"],
          operator: ["machines", "calendar", "rules"],
          viewer: ["machines", "calendar"],
        },
        effective_features_by_role: {
          super_admin: ["machines", "calendar", "rules", "reports", "settings"],
          org_admin: ["machines", "calendar", "rules", "reports", "settings"],
          plant_manager: ["machines", "calendar", "rules", "reports"],
          operator: ["machines", "calendar", "rules"],
          viewer: ["machines", "calendar"],
        },
        available_features: ["machines", "calendar", "rules", "reports", "settings"],
        entitlements_version: 1,
      },
    },
  };
  withPremiumModules(state);
  const accessToken = `header.${base64Json({
    sub: state.me.user.id,
    role: state.me.user.role,
    tenant_id: state.tenantId,
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;

  await page.addInitScript((snapshot) => {
    window.sessionStorage.setItem("factoryops_access_token", snapshot.accessToken);
    window.sessionStorage.setItem("factoryops_refresh_token", "refresh-token");
    window.sessionStorage.setItem("factoryops_selected_tenant", snapshot.tenantId);
    window.sessionStorage.setItem("factoryops_me", JSON.stringify(snapshot.me));

    const jsonResponse = (body, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: {
          "Content-Type": "application/json",
        },
      });

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
      if (
        url.includes("/api/v1/platform-maintenance/current") ||
        url.includes("/backend/auth/api/v1/platform-maintenance/current")
      ) {
        return jsonResponse({
          tenant_id: snapshot.tenantId,
          announcements: [],
        });
      }
      if (
        url.includes(`/api/v1/tenants/${snapshot.tenantId}/plants`) ||
        url.includes(`/backend/auth/api/v1/tenants/${snapshot.tenantId}/plants`)
      ) {
        return jsonResponse([snapshot.plant]);
      }
      if (url.includes("/backend/device/api/v1/devices")) {
        return jsonResponse({
          success: true,
          data: snapshot.devices,
        });
      }
      return originalFetch(input, init);
    };
  }, {
    accessToken,
    tenantId: state.tenantId,
    me: state.me,
    plant: state.plant,
    devices: state.devices,
  });

  await page.route("**/backend/auth/api/v1/auth/me", async (route) => {
    await fulfillJson(route, state.me);
  });

  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    await fulfillJson(route, {
      access_token: accessToken,
      token_type: "bearer",
      expires_in: 3600,
    });
  });

  await page.route("**/api/v1/platform-maintenance/current**", async (route) => {
    await fulfillJson(route, {
      tenant_id: state.tenantId,
      announcements: [],
    });
  });

  await page.route("**/backend/auth/api/v1/platform-maintenance/current**", async (route) => {
    await fulfillJson(route, {
      tenant_id: state.tenantId,
      announcements: [],
    });
  });

  await page.route(`**/api/v1/tenants/${state.tenantId}/plants**`, async (route) => {
    await fulfillJson(route, [clone(state.plant)]);
  });

  await page.route(`**/backend/auth/api/v1/tenants/${state.tenantId}/plants**`, async (route) => {
    await fulfillJson(route, [clone(state.plant)]);
  });

  await page.route("**/backend/device/api/v1/devices**", async (route) => {
    await fulfillJson(route, {
      success: true,
      data: clone(state.devices),
    });
  });

  const analyticsState = {
    jobs: [],
    selectedDeviceCount: 1,
    successJobId: "analytics-job-success",
    blockedJobId: "analytics-job-blocked",
    failedJobId: "analytics-job-failed",
    successStatusCalls: 0,
  };

  await page.route("**/backend/analytics/api/v1/analytics/models", async (route) => {
    await fulfillJson(route, {
      anomaly_detection: ["deterministic-anomaly-v1"],
      failure_prediction: ["deterministic-failure-v1"],
      forecasting: [],
    });
  });

  await page.route("**/backend/analytics/api/v1/analytics/preflight", async (route) => {
    await fulfillJson(route, {
      devices: [
        {
          device_id: "AD00000010",
          has_telemetry_in_range: true,
          reason: "telemetry_available",
          message: "Telemetry is available for this machine.",
        },
      ],
      checked_device_count: 1,
      devices_with_telemetry: 1,
      devices_without_telemetry: 0,
      devices_unverified: 0,
      guaranteed_no_data: false,
      message: "Telemetry is available for all selected devices.",
      coverage_result: {
        level: "full_coverage",
        usable_for_business_decisions: true,
        summary: "Telemetry coverage is complete for the selected period.",
      },
    });
  });

  await page.route("**/backend/analytics/api/v1/analytics/jobs**", async (route) => {
    await fulfillJson(route, clone(analyticsState.jobs));
  });

  await page.route("**/backend/analytics/api/v1/analytics/run", async (route) => {
    analyticsState.successStatusCalls = 0;
    analyticsState.jobs = [
      {
        job_id: analyticsState.successJobId,
        status: "running",
        progress: 45,
        phase_label: "Preparing anomaly model output",
        created_at: "2026-05-02T09:30:00Z",
        started_at: "2026-05-02T09:31:00Z",
        completed_at: null,
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        workflow_kind: "single",
      },
      {
        job_id: analyticsState.blockedJobId,
        status: "completed",
        progress: 100,
        phase_label: "No Data",
        created_at: "2026-05-02T09:20:00Z",
        started_at: "2026-05-02T09:21:00Z",
        completed_at: "2026-05-02T09:22:00Z",
        error_code: "NO_TELEMETRY_IN_RANGE",
        error_message: "No telemetry was available for the selected window.",
        result_ready: true,
        artifact_ready: false,
        download_ready: false,
        workflow_kind: "single",
      },
      {
        job_id: analyticsState.failedJobId,
        status: "failed",
        progress: 100,
        phase_label: "Execution stopped",
        created_at: "2026-05-01T09:30:00Z",
        started_at: "2026-05-01T09:31:00Z",
        completed_at: "2026-05-01T09:36:00Z",
        error_code: "WORKER_UNAVAILABLE",
        error_message: "analytics worker unavailable",
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        workflow_kind: "single",
      },
    ];

    await fulfillJson(route, {
      job_id: analyticsState.successJobId,
      status: "running",
      message: "Analysis accepted for background processing.",
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
    });
  });

  await page.route(`**/backend/analytics/api/v1/analytics/status/${analyticsState.successJobId}`, async (route) => {
    analyticsState.successStatusCalls += 1;
    analyticsState.jobs[0] = {
      ...analyticsState.jobs[0],
      status: "completed",
      progress: 100,
      phase_label: "Completed",
      completed_at: "2026-05-02T09:34:00Z",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    };
    await fulfillJson(route, {
      job_id: analyticsState.successJobId,
      status: "completed",
      progress: 100,
      message: "Completed",
      phase_label: "Completed",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    });
  });

  await page.route(`**/backend/analytics/api/v1/analytics/formatted-results/${analyticsState.successJobId}`, async (route) => {
    await fulfillJson(route, createAnalyticsResult(analyticsState.successJobId));
  });

  await page.route(`**/backend/analytics/api/v1/analytics/formatted-results/${analyticsState.blockedJobId}`, async (route) => {
    await fulfillJson(route, {
      analysis_type: "anomaly",
      job_id: analyticsState.blockedJobId,
      device_id: "AD00000010",
      status: "no_data",
      summary: "No telemetry was available for the selected window.",
      coverage_result: {
        level: "no_coverage",
        usable_for_business_decisions: false,
        artifact_generation_allowed: false,
        terminal_status: "business_blocked",
        message: "No telemetry is available in the selected time range.",
      },
    });
  });

  await page.goto("/analytics");

  await expect(page.getByText("No analytics jobs found yet.")).toBeVisible();
  const continueFromScope = page.getByRole("button", { name: "Continue" });
  await expect(page.getByTestId("device-scope-mode-all")).toContainText("1 accessible");
  await expect(continueFromScope).toBeEnabled();
  await continueFromScope.click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Anomaly Detection" }).click();
  await expect(page.getByText("Telemetry is available for all selected devices.")).toBeVisible();
  await page.getByRole("button", { name: "Run Analysis" }).click();

  await expect(page.getByText("Analysis Complete")).toBeVisible();
  await page.getByRole("button", { name: "View Dashboard" }).click();
  await expect(page.getByText("Confidence Summary")).toBeVisible();
  await expect(page.getByText("Inspect load spikes on the compressor").first()).toBeVisible();

  await page.getByRole("button", { name: "New Analysis" }).click();
  await expect(page.getByText("Analysis History")).toBeVisible();
  await expect(page.getByRole("cell", { name: analyticsState.successJobId })).toBeVisible();
  await expect(page.getByRole("cell", { name: analyticsState.failedJobId })).toBeVisible();
  await page.getByRole("cell", { name: analyticsState.failedJobId }).click();
  await expect(page.getByText("Processing is temporarily unavailable right now. Please try again in a moment.")).toBeVisible();
});

test("tariff settings follow the real browser validation and save contract", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);
  withPremiumModules(harness.state);

  await page.route("**/api/v1/platform-maintenance/current**", async (route) => {
    await fulfillJson(route, {
      tenant_id: harness.state.tenantId,
      announcements: [],
    });
  });

  await page.route("**/backend/auth/api/v1/platform-maintenance/current**", async (route) => {
    await fulfillJson(route, {
      tenant_id: harness.state.tenantId,
      announcements: [],
    });
  });

  await page.route(`**/api/v1/tenants/${harness.state.tenantId}/plants**`, async (route) => {
    await fulfillJson(route, [clone(harness.state.plant)]);
  });

  await page.route(`**/backend/auth/api/v1/tenants/${harness.state.tenantId}/plants**`, async (route) => {
    await fulfillJson(route, [clone(harness.state.plant)]);
  });

  await page.route("**/backend/device/api/v1/devices", async (route) => {
    await fulfillJson(route, {
      success: true,
      data: clone(harness.state.devices),
    });
  });

  await page.route("**/backend/device/api/v1/devices/dashboard/summary**", async (route) => {
    const rate = harness.state.tariff.rate;
    const currency = harness.state.tariff.currency;
    await fulfillJson(route, {
      generated_at: "2026-05-02T10:05:00Z",
      stale: false,
      warnings: [],
      summary: {
        total_devices: 1,
        running_devices: 1,
        stopped_devices: 0,
        idle_devices: 0,
        in_load_devices: 1,
        overconsumption_devices: 0,
        unknown_devices: 0,
        status_counts: {
          unknown: 0,
          stopped: 0,
          idle: 0,
          running: 1,
          overconsumption: 0,
        },
        devices_with_health_data: 1,
        devices_with_health_configured: 1,
        devices_missing_health_config: 0,
        devices_with_uptime_configured: 1,
        devices_missing_uptime_config: 0,
        system_health: 92,
        average_efficiency: 91.2,
      },
      alerts: {
        active_alerts: 0,
        alerts_triggered: 0,
        alerts_cleared: 0,
        rules_created: 0,
      },
      devices: [
        {
          device_id: "AD00000010",
          device_name: "Packaging Line A",
          device_type: "compressor",
          plant_id: "plant-1",
          runtime_status: "running",
          operational_status: "running",
          location: "Pune Bay 1",
          first_telemetry_timestamp: "2026-05-02T08:00:00Z",
          last_seen_timestamp: "2026-05-02T09:00:00Z",
          health_score: 92,
          uptime_percentage: 96.5,
        },
      ],
      cost_data_state: rate == null ? "unavailable" : "fresh",
      cost_data_reasons: rate == null ? ["tariff_not_configured"] : [],
      cost_generated_at: rate == null ? null : harness.state.tariff.updated_at,
      energy_widgets: {
        month_energy_kwh: 512.4,
        month_energy_cost_inr: rate == null ? 0 : Number((512.4 * rate).toFixed(2)),
        today_energy_kwh: 25.4,
        today_energy_cost_inr: rate == null ? 0 : Number((25.4 * rate).toFixed(2)),
        today_loss_kwh: 5.6,
        today_loss_cost_inr: rate == null ? 0 : Number((5.6 * rate).toFixed(2)),
        generated_at: "2026-05-02T10:05:00Z",
        currency,
        data_quality: "ok",
        invariant_checks: {},
        no_nan_inf: true,
      },
    });
  });

  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-snapshot**", async (route) => {
    await fulfillJson(route, {
      generated_at: "2026-05-02T10:05:00Z",
      total: 1,
      page: 1,
      page_size: 60,
      total_pages: 1,
      devices: [
        {
          device_id: "AD00000010",
          device_name: "Packaging Line A",
          device_type: "compressor",
          plant_id: "plant-1",
          runtime_status: "running",
          load_state: "running",
          current_band: "in_load",
          operational_status: "running",
          location: "Pune Bay 1",
          first_telemetry_timestamp: "2026-05-02T08:00:00Z",
          last_seen_timestamp: "2026-05-02T09:00:00Z",
          health_score: 92,
          has_uptime_config: true,
          data_freshness_ts: "2026-05-02T09:00:00Z",
          version: 1,
        },
      ],
    });
  });

  await page.route("**/backend/device/api/v1/devices/dashboard/fleet-stream**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: 1\n" +
        "event: heartbeat\n" +
        `data: ${JSON.stringify({
          id: "1",
          event: "heartbeat",
          generated_at: "2026-05-02T10:05:00Z",
          freshness_ts: "2026-05-02T10:05:00Z",
          stale: false,
          warnings: [],
          devices: [],
          partial: false,
          version: 1,
        })}\n\n`,
    });
  });

  await page.route("**/backend/rule-engine/api/v1/alerts/events/unread-count**", async (route) => {
    await fulfillJson(route, { data: { count: 0 } });
  });

  await page.route("**/backend/rule-engine/api/v1/alerts/events**", async (route) => {
    await fulfillJson(route, {
      data: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 0,
    });
  });

  const reportsState = {
    createdReportId: "report-energy-success",
    createdStatusCalls: 0,
    schedules: [],
    history: [
      {
        report_id: "report-finalizing",
        status: "completed",
        backend_status: "completed",
        report_type: "consumption",
        progress: 100,
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        error_code: null,
        error_message: null,
        created_at: "2026-05-01T08:00:00Z",
        completed_at: "2026-05-01T08:10:00Z",
      },
      {
        report_id: "report-no-data",
        status: "failed",
        backend_status: "failed",
        report_type: "consumption",
        progress: 100,
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        error_code: "NO_TELEMETRY_IN_RANGE",
        error_message: "No telemetry found in selected time range",
        created_at: "2026-05-01T07:00:00Z",
        completed_at: "2026-05-01T07:05:00Z",
      },
    ],
    hiddenInsight: createHiddenInsight(),
  };

  const wasteState = {
    jobId: "waste-job-1",
    history: [],
  };

  await page.route("**/api/reports/energy/consumption", async (route) => {
    reportsState.createdStatusCalls = 0;
    reportsState.history = [
      {
        report_id: reportsState.createdReportId,
        status: "running",
        backend_status: "processing",
        report_type: "consumption",
        progress: 55,
        phase_label: "Calculating cost breakdown",
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
        error_code: null,
        error_message: null,
        created_at: "2026-05-02T10:00:00Z",
        completed_at: null,
      },
      ...reportsState.history.filter((item) => item.report_id !== reportsState.createdReportId),
    ];

    await fulfillJson(route, {
      report_id: reportsState.createdReportId,
      status: "running",
      progress: 30,
      phase_label: "Queued for processing",
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
    });
  });

  await page.route(`**/api/reports/${reportsState.createdReportId}/status**`, async (route) => {
    reportsState.createdStatusCalls += 1;
    if (reportsState.createdStatusCalls < 2) {
      await fulfillJson(route, {
        report_id: reportsState.createdReportId,
        status: "running",
        progress: 78,
        phase_label: "Finalizing insights",
        result_ready: false,
        artifact_ready: false,
        download_ready: false,
      });
      return;
    }

    reportsState.history[0] = {
      ...reportsState.history[0],
      status: "completed",
      backend_status: "completed",
      progress: 100,
      phase_label: "Completed",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
      completed_at: "2026-05-02T10:04:00Z",
    };
    await fulfillJson(route, {
      report_id: reportsState.createdReportId,
      status: "completed",
      progress: 100,
      phase_label: "Completed",
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    });
  });

  await page.route("**/api/reports/report-finalizing/status**", async (route) => {
    await fulfillJson(route, {
      report_id: "report-finalizing",
      status: "completed",
      progress: 100,
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
    });
  });

  await page.route("**/api/reports/report-no-data/status**", async (route) => {
    await fulfillJson(route, {
      report_id: "report-no-data",
      status: "failed",
      progress: 100,
      result_ready: false,
      artifact_ready: false,
      download_ready: false,
      error_code: "NO_TELEMETRY_IN_RANGE",
      error_message: "No telemetry found in selected time range",
    });
  });

  await page.route(`**/api/reports/${reportsState.createdReportId}/result**`, async (route) => {
    await fulfillJson(route, {
      summary: {
        total_kwh: 128.4,
        peak_demand_kw: 18.6,
        load_factor_pct: 77.4,
        total_cost: 1091.4,
        currency: "INR",
      },
      insights: [
        "Power stayed within expected thresholds for most of the selected period.",
      ],
      hidden_overconsumption_insight: reportsState.hiddenInsight,
    });
  });

  await page.route(`**/api/reports/${reportsState.createdReportId}/download**`, async (route) => {
    await fulfillPdf(route, "energy_report_report-energy-success.pdf");
  });

  await page.route("**/api/reports/history**", async (route) => {
    await fulfillJson(route, { reports: clone(reportsState.history) });
  });

  await page.route("**/api/reports/schedules**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await fulfillJson(route, { schedules: clone(reportsState.schedules) });
      return;
    }
    const body = request.postDataJSON();
    const schedule = {
      schedule_id: "schedule-1",
      tenant_id: harness.state.tenantId,
      report_type: body.report_type,
      frequency: body.frequency,
      is_active: true,
      last_run_at: null,
      next_run_at: "2026-05-03T01:00:00Z",
      last_status: "pending",
      last_result_url: null,
      params_template: body.params_template,
    };
    reportsState.schedules = [schedule];
    await fulfillJson(route, {
      schedule_id: schedule.schedule_id,
      tenant_id: schedule.tenant_id,
      report_type: schedule.report_type,
      frequency: schedule.frequency,
      is_active: true,
      next_run_at: schedule.next_run_at,
      created_at: "2026-05-02T10:20:00Z",
    }, 201);
  });

  await page.route("**/api/reports/schedules/*", async (route) => {
    reportsState.schedules = reportsState.schedules.map((schedule) => ({
      ...schedule,
      is_active: false,
    }));
    await fulfillJson(route, { message: "deactivated" });
  });

  await page.route("**/api/waste/analysis/history**", async (route) => {
    await fulfillJson(route, { items: clone(wasteState.history) });
  });

  await page.route("**/api/waste/analysis/run", async (route) => {
    wasteState.history = [
      {
        job_id: wasteState.jobId,
        job_name: "Weekly Waste Review",
        status: "completed",
        progress_pct: 100,
        phase_label: "Completed",
        result_ready: true,
        artifact_ready: true,
        download_ready: true,
        created_at: "2026-05-02T11:00:00Z",
        started_at: "2026-05-02T11:01:00Z",
        completed_at: "2026-05-02T11:04:00Z",
        scope: "all",
        requested_device_count: 1,
        start_date: "2026-04-25",
        end_date: "2026-05-02",
      },
    ];
    await fulfillJson(route, clone(wasteState.history[0]));
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/status`, async (route) => {
    await fulfillJson(route, clone(wasteState.history[0]));
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/result`, async (route) => {
    await fulfillJson(route, {
      total_waste_cost: 420.5,
      total_energy_cost: 2400.75,
      total_energy_kwh: 282.4,
      total_idle_kwh: 31.2,
      insights: ["Overconsumption was concentrated during off-hours."],
      device_summaries: [
        {
          device_id: "AD00000010",
          device_name: "Packaging Line A",
          off_hours: {
            duration_sec: 7200,
            energy_kwh: 12.4,
            cost: 105.4,
          },
          overconsumption: {
            duration_sec: 3600,
            energy_kwh: 8.2,
            cost: 69.7,
          },
        },
      ],
    });
  });

  await page.route(`**/api/waste/analysis/${wasteState.jobId}/download`, async (route) => {
    await fulfillJson(route, {
      job_id: wasteState.jobId,
      status: "completed",
      download_url: "/api/waste/downloads/waste-job-1.pdf",
      expires_in_seconds: 300,
      result_ready: true,
      artifact_ready: true,
      download_ready: true,
    });
  });

  await page.route("**/api/waste/downloads/waste-job-1.pdf", async (route) => {
    await fulfillPdf(route, "waste_report_waste-job-1.pdf");
  });

  await seedAuthenticatedSession(page, harness);

  await page.goto("/settings");
  const tariffRateInput = page.getByLabel("Energy Rate (per kWh)");
  await tariffRateInput.fill("-3");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Rate must be a valid positive number")).toBeVisible();
  await expect(page.getByText("Current tariff: Not configured")).toBeVisible();

  await tariffRateInput.fill("8.5");
  await page.getByLabel("Currency").selectOption("INR");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Tariff updated")).toBeVisible();
  await expect(page.getByText("Current tariff: ₹8.50 / kWh")).toBeVisible();

  await page.goto("/machines");
  await expect(page.getByText("₹4,355.40")).toBeVisible();
  await expect(page.getByText("₹215.90")).toBeVisible();
  await expect(page.getByText("₹47.60")).toBeVisible();
});

test("reports truthfully cover generation, history states, schedules, and hidden insight presentation", async ({ page }) => {
  const harness = await installPremiumJourney(page);
  harness.state.tariff = {
    rate: 8.5,
    currency: "INR",
    updated_at: "2026-05-02T09:40:00Z",
  };
  const reportState = await installReportRoutes(page, harness);

  page.on("dialog", async (dialog) => {
    await dialog.accept();
  });

  await page.goto("/reports/energy");
  await page.getByRole("button", { name: "Custom" }).click();
  const reportDateInputs = page.locator("input[type='date']");
  await reportDateInputs.nth(0).fill("2026-05-01");
  await reportDateInputs.nth(1).fill("2026-05-02");
  await page.getByRole("button", { name: "Generate Report" }).click();

  await expect(page.getByRole("heading", { name: "Report Result" })).toBeVisible();
  await expect(page.getByText("INR 1091.40")).toBeVisible();
  await expect(page.getByText("12.40").first()).toBeVisible();
  await expect(page.getByText("INR 105.40")).toBeVisible();
  await expect(page.getByText("Aggregate P75 Baseline")).toBeVisible();
  await expect(page.getByText("Above Baseline").first()).toBeVisible();
  await page.getByRole("button", { name: "Download PDF" }).click();
  await expect.poll(() => reportState.downloadRequests).toBe(1);

  await page.goto("/reports");
  await expect(page.getByText(reportState.createdReportId)).toBeVisible();
  await expect(page.getByRole("button", { name: "Download artifact" })).toBeVisible();
  await page.getByRole("button", { name: "Download artifact" }).click();
  await expect(page.getByText("Download started")).toBeVisible();
  await expect.poll(() => reportState.downloadRequests).toBe(2);

  const historyRows = page.locator("tbody tr");
  await historyRows.nth(1).click();
  await expect(page.getByText("Results are still being finalized for this history view.")).toBeVisible();

  await historyRows.nth(2).click();
  await expect(
    page
      .getByText("No telemetry was available for the selected period.")
      .filter({ visible: true })
      .last(),
  ).toBeVisible();

  await page.getByRole("button", { name: "Schedules" }).click();
  await expect(page.getByText("No schedules configured yet")).toBeVisible();
  await page.getByRole("button", { name: "New Schedule" }).click();
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Schedule created successfully")).toBeVisible();
  await expect(page.getByRole("cell", { name: "daily" })).toBeVisible();
  await page.getByRole("button", { name: "Deactivate" }).click();
  await expect(page.getByText("Schedule deactivated")).toBeVisible();
});

test("waste analysis truthfully covers run, result presentation, and download readiness", async ({ page }) => {
  const harness = await installPremiumJourney(page);
  harness.state.tariff = {
    rate: 8.5,
    currency: "INR",
    updated_at: "2026-05-02T09:40:00Z",
  };
  const wasteState = await installWasteRoutes(page);

  await page.goto("/waste-analysis");
  await expect(page.getByText("No waste analysis jobs yet.")).toBeVisible();
  await page.getByRole("button", { name: "Run Waste Analysis" }).click();

  await expect(page.getByRole("heading", { name: "Selected Waste Analysis" })).toBeVisible();
  await expect(page.getByText("Result and PDF ready")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Waste Analysis Result" })).toBeVisible();
  await expect(page.getByText("₹420.50")).toBeVisible();
  await expect(page.getByText("282.40 kWh")).toBeVisible();
  await expect(page.getByText("Packaging Line A")).toBeVisible();
  await page.getByRole("button", { name: "Download PDF" }).last().click();
  await expect.poll(() => wasteState.downloadRequests).toBe(1);
});

test("copilot fallback states stay truthful across unavailable and internal-error service responses", async ({ page }) => {
  await installPremiumJourney(page);
  let chatCalls = 0;

  await page.route("**/backend/copilot/api/v1/copilot/curated-questions", async (route) => {
    await fulfillJson(route, {
      starter_questions: [
        { id: "q-1", text: "Why is Packaging Line A wasting energy?" },
      ],
    });
  });

  await page.route("**/backend/copilot/api/v1/copilot/chat", async (route) => {
    chatCalls += 1;
    if (chatCalls === 1) {
      await fulfillJson(route, { error_code: "AI_UNAVAILABLE", message: "Copilot is temporarily unavailable." }, 503);
      return;
    }
    if (chatCalls === 2) {
      await fulfillJson(route, { error_code: "INTERNAL_ERROR", message: "Unexpected failure." }, 500);
      return;
    }
    await fulfillJson(route, {
      answer: "Packaging Line A showed off-hours draw above the expected baseline.",
      reasoning: "Cost and telemetry patterns point to off-hours draw.",
      reasoning_sections: {
        what_happened: "Off-hours power stayed elevated.",
        why_it_matters: "That behavior increases waste cost.",
        how_calculated: "The assistant compared actual power against the learned baseline.",
      },
      data_table: null,
      chart: null,
      page_links: [{ label: "Open Waste Analysis", route: "/waste-analysis" }],
      follow_up_suggestions: [],
      curated_context: null,
      error_code: null,
    });
  });

  await page.goto("/copilot");
  const starterQuestion = page.getByRole("button", { name: "Why is Packaging Line A wasting energy?" });
  await expect(starterQuestion).toBeVisible();

  await starterQuestion.click();
  await expect(page.getByText("Could not get answer. Please try again.")).toBeVisible();

  await starterQuestion.click();
  await expect(page.getByText("Could not get answer. Please try again.")).toBeVisible();

  await starterQuestion.click();
  await expect(page.getByText("Packaging Line A showed off-hours draw above the expected baseline.")).toBeVisible();
  await expect(page.getByText("Open Waste Analysis")).toBeVisible();
});
