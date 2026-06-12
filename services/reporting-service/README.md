# Reporting Service

FactoryOPS reporting backend for energy reports, PDF generation, scheduling, report history, and platform settings (tariff + notification channels).

## Base URL
- `http://<host>:8085`

## Health Endpoints
- `GET /health`
- `GET /ready`

## API Surface

### Energy Reports
- `POST /api/reports/energy/consumption`
- `POST /api/reports/energy/comparison`
- `POST /api/reports/energy/comparison/` (compat alias)

### Report Lifecycle
- `GET /api/reports/history`
- `POST /api/reports/schedules`
- `GET /api/reports/schedules`
- `DELETE /api/reports/schedules/{schedule_id}`
- `GET /api/reports/{report_id}/status`
- `GET /api/reports/{report_id}/result`
- `GET /api/reports/{report_id}/download`

### Tariff (legacy report tariff API)
- `POST /api/reports/tariffs/`
- `GET /api/reports/tariffs/{tenant_id}`

### Settings (current source of truth)
- `GET /api/v1/settings/tariff`
- `POST /api/v1/settings/tariff`
- `GET /api/v1/settings/notifications`
- `POST /api/v1/settings/notifications/email`
- `DELETE /api/v1/settings/notifications/email/{channel_id}`

## Runtime Components
- App/router wiring: `src/main.py`
- Report handlers: `src/handlers/*.py`
- Report task worker: `src/tasks/report_task.py`
- Calculation engine: `src/services/report_engine.py`
- Insights: `src/services/insights_engine.py`
- PDF build: `src/pdf/builder.py`

## Energy Calculation Priority (Implemented)
Code: `src/services/report_engine.py`

For each device/window, first valid method wins:
1. `energy_kwh` delta:
   - `kwh = last(energy_kwh) - first(energy_kwh)`
   - quality: `high`
2. `power` integration:
   - `kwh = ∫ power_kw dt` (timestamp-based integration)
   - quality: `medium`
3. derived power from `V * I * PF / 1000`, then integrate:
   - quality: `medium`
4. PF missing -> assume PF=1.0, derive + integrate:
   - quality: `low`, warning emitted
5. insufficient fields:
   - no guessed energy/cost
   - quality: `insufficient`

## Additional Metrics
- Peak demand:
  - max of direct/derived `power_kw`
- Average load:
  - `average_load_kw = total_kwh / total_hours`
- Load factor:
  - `load_factor_pct = clamp((average_load_kw / peak_demand_kw) * 100, 0, 100)`
  - if peak demand is 0/missing -> `null`

## Cost Model
- Tariff is fetched live from Settings (`/api/v1/settings/tariff`) at report execution time.
- `total_cost = total_kwh * tariff_rate`
- If tariff is missing: costs return `null` with warnings.
- Report output stores tariff snapshot fields for auditability.

## Notification Channels Storage
Settings endpoints store channels in reporting DB:
- Email is functional (active/inactive)
- WhatsApp/SMS are placeholder channels in UI for future use

## Timezone
- User-facing report timestamps are rendered in IST (`Asia/Kolkata`) in current templates/formatters.

## Single-DB Migration Note
- For unified DB deployments, Alembic version table is namespaced for this service (`alembic_version_reporting`).
