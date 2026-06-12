# CO2 Emissions Widget Implementation Spec

## 1. Purpose

The CO2 Emissions widget converts machine electricity consumption into Scope 2 electricity emissions so users can understand energy usage in sustainability terms, not only kWh and INR.

The widget is not a new telemetry pipeline. It is a reporting layer over energy already calculated by the platform.

Primary outcomes:

- Show per-device CO2e for today, this week, and this month.
- Show avoidable CO2e from total waste/loss energy as one clear value.
- Keep every displayed value auditable by exposing the emission factor, source, year, and method used.
- Avoid adding pressure to InfluxDB or live telemetry endpoints.

Implementation boundary for the first release:

- Phase 1 widget is machine-wise, meaning each device gets its own CO2 widget on the machine detail page.
- Plant-wise and fleet-wise aggregation are follow-on phases, not the first release surface.

## 2. Accounting Scope

This feature should be implemented as a Scope 2 purchased electricity widget.

Scope 2 emissions cover purchased or acquired electricity, steam, heat, and cooling. For this platform, the immediate scope is electricity consumed by factory machines.

Reference:

- GHG Protocol Scope 2 Guidance: https://ghgprotocol.org/scope_2_guidance
- GHG Protocol Scope 2 FAQ: https://ghgprotocol.org/scope-2-frequently-asked-questions
- CEA CO2 Baseline Database for the Indian Power Sector: https://cea.nic.in/cdm-co2-baseline-database/?lang=en

## 3. Recommended India Emission Factor

For an India-wide location-based default, use the CEA all-India grid electricity factor that includes renewable generation.

Recommended default:

```text
0.716 kg CO2/kWh
```

Reason:

- CEA baseline database Version 19.0 lists FY 2022-23 average CO2 emission factor of grid electricity including renewable generation as approximately `0.716 tCO2/MWh`.
- `1 tCO2/MWh` equals `1 kg CO2/kWh`, so `0.716 tCO2/MWh` equals `0.716 kg CO2/kWh`.

Important distinction:

- `0.82 kg CO2/kWh` appears as a conventional-generation weighted average in CEA tables and is also a common legacy estimate.
- It should not be silently used as the default for a location-based all-grid Scope 2 widget unless the business explicitly wants a conservative/conventional-only factor.

Enterprise rule:

- The factor must be configurable and auditable.
- The UI/API must return the factor source and year with every CO2 result.
- Future updates to the factor must not require code deployment.

## 4. Core Formula

```text
co2_kg = energy_kwh * emission_factor_kg_per_kwh
```

Examples:

```text
1000 kWh * 0.716 kg CO2/kWh = 716.0 kg CO2
24.5 kWh * 0.716 kg CO2/kWh = 17.542 kg CO2
```

Display rounding:

- Under 100 kg: one decimal place, for example `17.5 kg CO2e`.
- 100 kg to 999 kg: one decimal place, for example `716.0 kg CO2e`.
- 1000 kg and above: show tonnes with two decimals, for example `1.24 t CO2e`.

Stored calculations should keep at least 4 decimal places.

## 5. Data Inputs

The feature should reuse existing platform energy outputs.

Preferred inputs:

- `today_energy_kwh`
- `week_energy_kwh`
- `month_energy_kwh`
- `total_loss_kwh`

Data sources by use case:

- Live machine-detail widget: use the `device-service` bootstrap/read model and existing MySQL projection data already needed by the machine dashboard.
- Calendar or monthly history: use existing daily/monthly energy projection tables.
- Reports: use reporting-service aggregate outputs.
- Backfills or historical recalculation: InfluxDB is allowed only for explicit background jobs, never for hot dashboard reads.

Do not fetch raw telemetry from InfluxDB for the normal widget render.

## 6. Ownership and Latency Contract

The machine-detail CO2 widget must behave like a KPI widget, not a report widget.

Service ownership:

- The first production read path belongs in `device-service`, because the machine detail page already hydrates through the device dashboard/bootstrap read model.
- `energy-service` remains the source for energy aggregate concepts and may own later plant/fleet/report CO2 APIs.
- The first release should not add a new real-time stream processor or raw telemetry fanout just for CO2.

Fast-path source of truth:

- `today_energy_kwh` and `today_loss_kwh` should come from existing MySQL projection/aggregate state.
- Weekly and monthly values should come from existing aggregate tables or materialized summary rows.
- CO2 math should be a cheap read-time multiplication against a configured factor.
- No hot machine-detail render should call InfluxDB.
- No hot machine-detail render should perform historical recomputation.

## 7. Widget Metrics

Per-device widget:

```text
CO2 today = today_energy_kwh * factor
CO2 this week = week_energy_kwh * factor
CO2 this month = month_energy_kwh * factor
Wasted CO2 today = today_loss_kwh * factor
Avoidable CO2 this month = month_loss_kwh * factor
```

Waste-derived CO2 depends on loss data availability:

- If total loss/waste data is available, show avoidable CO2 as one combined value.
- If waste/loss analysis has not yet produced usable values for the device or period, show total CO2e only and mark avoidable CO2 as unavailable.
- Do not show `0.0 kg CO2e avoided` unless the underlying waste/loss calculation is genuinely zero.
- Do not split avoidable CO2 into idle, off-hours, and overconsumption in the first release widget.

Optional equivalent:

```text
driving_km_equivalent = co2_kg / car_kg_co2_per_km
```

Recommended default for equivalence:

```text
car_kg_co2_per_km = 0.173
```

This equivalence must be labelled as an illustrative estimate, not a compliance number.

## 8. API Design

Recommended first-release endpoint, owned by `device-service`:

```http
GET /api/v1/devices/{device_id}/co2-summary?tenant_id=...&period=today,week,month
```

This endpoint should be implemented on the same fast read-model boundary used by the machine detail page. Later plant, fleet, or report APIs may live in `energy-service` or `reporting-service`, but the machine-detail widget should not require an extra hot-path service hop to render.

Recommended response:

```json
{
  "success": true,
  "device_id": "AD00000001",
  "tenant_id": "SH00000002",
  "factor": {
    "value": 0.716,
    "unit": "kg_co2_per_kwh",
    "method": "location_based",
    "country": "IN",
    "region": "all_india_grid",
    "source": "Central Electricity Authority CO2 Baseline Database",
    "source_version": "Version 19.0",
    "factor_year": "FY2022-23"
  },
  "periods": {
    "today": {
      "energy_kwh": 24.5,
      "co2_kg": 17.542,
      "loss_kwh": 3.2,
      "avoidable_co2_kg": 2.2912
    },
    "week": {
      "energy_kwh": 144.8,
      "co2_kg": 103.6768,
      "loss_kwh": 18.4,
      "avoidable_co2_kg": 13.1744
    },
    "month": {
      "energy_kwh": 612.3,
      "co2_kg": 438.1068,
      "loss_kwh": 51.1,
      "avoidable_co2_kg": 36.5876
    }
  },
  "quality": {
    "energy_source": "projected_aggregate",
    "factor_source": "tenant_default",
    "calculation_version": "co2_scope2_v1",
    "auditable": true
  }
}
```

Config endpoint:

```http
GET /api/v1/energy/emission-factor
PUT /api/v1/energy/emission-factor
```

Only super admin or authorized org admin roles should be able to change the tenant factor.

## 9. Database Design

Minimum enterprise-safe table:

```sql
CREATE TABLE tenant_emission_factors (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id VARCHAR(10) NOT NULL,
  country VARCHAR(8) NOT NULL DEFAULT 'IN',
  region VARCHAR(64) NOT NULL DEFAULT 'all_india_grid',
  method VARCHAR(32) NOT NULL DEFAULT 'location_based',
  factor_value DECIMAL(12,6) NOT NULL,
  factor_unit VARCHAR(32) NOT NULL DEFAULT 'kg_co2_per_kwh',
  source_name VARCHAR(255) NOT NULL,
  source_version VARCHAR(64) NULL,
  factor_year VARCHAR(32) NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_tenant_emission_factor_active (tenant_id, country, region, method, is_active)
);
```

Recommended first seed:

```text
tenant_id: platform_default or tenant-specific
country: IN
region: all_india_grid
method: location_based
factor_value: 0.716
factor_unit: kg_co2_per_kwh
source_name: Central Electricity Authority CO2 Baseline Database
source_version: Version 19.0
factor_year: FY2022-23
```

Materialized CO2 snapshots are optional in Phase 1. If performance requires it later, add daily/monthly CO2 materialization tables using existing energy aggregate tables as the source.

## 10. Frontend Widget Design

Recommended placement:

- Device detail page: with the energy/loss area, preferably directly above or below `Waste & Loss Today`.
- Fleet dashboard: later-phase aggregate card for total fleet emissions.
- Plant dashboard: later-phase aggregate card for plant-level emissions.
- Reports page: include CO2e in generated energy reports after the first device widget is stable.

Placement rule:

- Do not place this widget inside `Machine Health`.
- Machine health remains about risk and anomaly intelligence.
- CO2 belongs to the energy, waste, and sustainability area of the page.

Relationship with existing cost/tariff widgets:

- The CO2 widget is an emissions surface, not a cost surface.
- Existing waste/loss widgets continue to show INR values using tariff configuration.
- The CO2 widget may sit beside waste/cost data, but it must not duplicate rupee numbers or mix rupee totals into the CO2 display.
- Clear separation in UI: CO2 widget shows `kg CO2e` / `t CO2e`, waste widget shows `INR`, and both may reference the same underlying energy/loss basis.

Widget layout:

```text
CO2 Emissions

Today          This Week       This Month
17.5 kg CO2e   103.7 kg CO2e   438.1 kg CO2e

Avoidable today: 2.3 kg CO2e
Savings opportunity: 24.8 kg CO2e/month

Factor: 0.716 kg CO2/kWh, CEA India grid, FY2022-23
```

UI rules:

- Always show the factor source in the expanded/detail state.
- Use `CO2e` in labels for business reporting language.
- If factor is missing, show `Not configured` instead of guessing.
- If energy quality is degraded, show the CO2 value with a quality note.
- If waste/loss data is unavailable for the period, show an explicit empty state such as `Avoidable CO2 not available yet`.
- Do not render tariff rates, rupee symbols, or monetary totals inside the CO2 widget.
- When CO2 and cost are shown on the same page, label them distinctly so the user can tell `emissions` from `cost` at a glance.
- Do not show separate idle/off-hours/overconsumption CO2 rows in the first release. Keep waste CO2 as one combined avoidable emissions value.

## 11. Validation Rules

Calculation validation:

- `co2_kg` must equal `energy_kwh * factor_value`.
- Negative energy must be clamped or rejected according to the existing energy accounting rule.
- If `energy_kwh` is unavailable, CO2 should be unavailable.
- If factor is unavailable, CO2 should be unavailable.
- Do not use tariff rate in CO2 calculations.
- If `loss_kwh` is unavailable, avoidable CO2 should be unavailable, not zero.

Widget truthfulness validation:

- `Total CO2e` may render even when waste-derived CO2 is unavailable, because total energy and loss availability are separate contracts.
- `Avoidable CO2` must only render when the corresponding waste/loss basis is available for that period.
- The CO2 widget must not display INR values, tariff rates, or cost-derived labels.
- The CO2 widget must not split avoidable CO2 into waste categories in the first release.
- Pages that show both widgets must preserve one-to-one consistency:
  - same underlying `energy_kwh` and `loss_kwh` basis
  - different units and labels
  - no conflicting totals between rupee and CO2 surfaces

Tenant isolation:

- A tenant must only read its own factor and device energy.
- Super admin can manage global defaults.
- Org admin can manage tenant override only if allowed by product policy.

Performance validation:

- Widget read path must use MySQL aggregate/projection data.
- No dashboard render should call InfluxDB directly.
- No per-device fanout to data-service for fleet view.
- The machine-detail CO2 response should be available from the same fast read model as the machine dashboard/bootstrap payload, or from a similarly cheap device-service endpoint.
- CO2 calculation should be pure multiplication over existing aggregate values and should not introduce new telemetry scans.

Audit validation:

- Every API response must include factor value, unit, method, source, version, and calculation version.
- Historical reports should store or render the factor used at report generation time.
- If a factor changes, old reports should remain explainable.

## 12. Rollout Plan

Phase 1: Documentation and factor governance

- Approve default factor and source.
- Add tenant emission factor table.
- Seed India default factor.
- Add tests for factor resolution and audit metadata.

Phase 2: Device CO2 summary API

- Add machine-detail CO2 summary read path in `device-service`.
- Use existing energy/loss projections.
- Add tenant isolation tests.
- Add no-Influx-on-widget-read tests.
- Add latency/contract tests proving no raw telemetry scan is required for the widget.

Phase 3: Device UI widget

- Add widget to machine detail page.
- Add loading, unavailable, and factor-not-configured states.
- Add mobile layout tests.

Phase 4: Plant, fleet, and reports

- Add plant-level CO2 summary.
- Add fleet-level CO2 summary.
- Add CO2e section to energy reports.
- Store factor metadata in generated report payloads.

Phase 5: Admin configuration

- Add super admin factor management.
- Add tenant override policy.
- Add audit log for factor changes.

## 13. Edge Cases

- Missing factor: return `co2_available=false`.
- Missing energy: return `energy_available=false`.
- Zero energy: show `0.0 kg CO2e`.
- Negative energy delta: follow existing energy accounting quality rules.
- Factor update mid-month: default behavior should calculate current widgets using active factor, while generated reports should persist the factor used at generation time.
- Renewable contract or market-based claim: support later as `method=market_based`; do not blend it into location-based calculations silently.

## 14. Recommended First Implementation Boundary

The first production version should be intentionally narrow:

- Per-device widget only.
- Machine-wise only in the UI.
- One combined avoidable CO2 value for waste/loss.
- Location-based India default only.
- Configurable factor in DB.
- `device-service` machine-detail read model for the first widget.
- MySQL aggregate/projection source only.
- No direct InfluxDB dashboard reads.
- No category-level waste CO2 breakdown.
- No market-based certificate logic until the business has documentation for renewable contracts or EACs.

This gives the platform a clean, stakeholder-friendly sustainability feature without weakening the existing live telemetry performance boundary.
