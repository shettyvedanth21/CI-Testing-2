import { chromium } from '@playwright/test';

function base64Json(value) { return Buffer.from(JSON.stringify(value), 'utf8').toString('base64'); }
function buildMe(role='org_admin'){ return { user:{ id:'user-1', email:`${role}@example.com`, full_name:'User', role, tenant_id:'SH00000001', is_active:true, created_at:new Date().toISOString(), last_login_at:null}, org:{id:'SH00000001',name:'Shivex Demo Tenant',slug:'shivex-demo-tenant',is_active:true,created_at:new Date().toISOString()}, plant_ids:['plant-1'], entitlements:{ premium_feature_grants:['machines','rules','reports','waste_analysis','copilot'], role_feature_matrix:{ super_admin:['machines','rules','reports','waste_analysis','copilot'], org_admin:['machines','rules','reports','waste_analysis','copilot'], plant_manager:['machines','rules','reports','waste_analysis'], operator:['machines','rules'], viewer:['machines']}, baseline_features_by_role:{ super_admin:['machines','rules','reports','waste_analysis','copilot'], org_admin:['machines','rules','reports','waste_analysis','copilot'], plant_manager:['machines','rules','reports','waste_analysis'], operator:['machines','rules'], viewer:['machines']}, effective_features_by_role:{ super_admin:['machines','rules','reports','waste_analysis','copilot'], org_admin:['machines','rules','reports','waste_analysis','copilot'], plant_manager:['machines','rules','reports','waste_analysis'], operator:['machines','rules'], viewer:['machines']}, available_features:['machines','rules','reports','waste_analysis','copilot'], entitlements_version:1 } }; }
let records=[{id:1,tenant_id:'SH00000001',device_id:'AD00000010',maintenance_date:'2026-04-20',title:'Filter replacement',description:'Changed intake filter and checked belt alignment.',cost:1250,performed_by:'Ramesh',status:'Completed',next_due_date:'2026-05-20',created_by:'user-1',created_at:'2026-04-20T10:00:00Z',updated_at:'2026-04-20T10:00:00Z'}];
function buildSummary(){ const sorted=[...records].sort((a,b)=>String(b.maintenance_date).localeCompare(String(a.maintenance_date))); return { total_records:records.length, total_cost:records.reduce((s,r)=>s+Number(r.cost||0),0), latest_maintenance_date:sorted[0]?.maintenance_date ?? null, last_recorded_at:sorted[0]?.updated_at ?? null, next_due_date: records.map(r=>r.next_due_date).filter(Boolean).sort()[0] ?? null }; }
async function fulfillJson(route, data, status=200){ await route.fulfill({status, contentType:'application/json', body: JSON.stringify(data)}); }

const browser = await chromium.launch({headless:true});
const context = await browser.newContext({ baseURL: 'http://localhost:3000' });
const page = await context.newPage();
page.setDefaultTimeout(10000);
page.on('console', msg => console.log('console:', msg.type(), msg.text()));
page.on('pageerror', err => console.log('pageerror:', err.message));
page.on('requestfailed', req => console.log('requestfailed:', req.url(), req.failure()?.errorText));
await page.addInitScript((snapshot) => {
  window.sessionStorage.setItem('factoryops_access_token', snapshot.accessToken);
  window.sessionStorage.setItem('factoryops_refresh_token', 'refresh-token');
  window.sessionStorage.setItem('factoryops_selected_tenant', 'SH00000001');
  window.sessionStorage.setItem('factoryops_me', JSON.stringify(snapshot.me));
}, { accessToken: `header.${base64Json({ sub:'user-1', role:'org_admin', tenant_id:'SH00000001', plant_ids:['plant-1'], permissions_version:1, tenant_entitlements_version:1, exp: Math.floor(Date.now()/1000)+3600 })}.signature`, me: buildMe('org_admin') });

await page.route('**/backend/**', async (route, req) => { console.log('unhandled backend route', req.method(), req.url()); await route.fallback(); });
await page.route('**/backend/auth/api/v1/auth/me', route => fulfillJson(route, buildMe('org_admin')));
await page.route('**/backend/device/api/v1/devices/AD00000010/dashboard-bootstrap', route => fulfillJson(route, { generated_at:new Date().toISOString(), version:1, device:{ device_id:'AD00000010', tenant_id:'SH00000001', device_name:'AB-TESTING', device_type:'Chiller', status:'active', runtime_status:'stopped', last_seen_timestamp:'2026-04-26T06:30:00Z', location:'Floor-01', fla_current_amps:15 }, telemetry:[{ timestamp:'2026-04-26T06:30:00Z', current:1.24, current_l1:1.24, power:120 }], uptime:{ shifts_configured:0, uptime_percentage:null, total_planned_minutes:0, total_effective_minutes:0, actual_running_minutes:0, message:'No shifts configured.'}, shifts:[], health_configs:[], health_score:{ health_score:100, status:'Healthy', status_color:'🟢', machine_state:'STOPPED', parameters_included:0, parameters_skipped:0, total_weight_configured:0, parameter_scores:[] }, widget_config:{ selected_fields:['current','current_l1'], effective_fields:['current','current_l1'] }, current_state:{ machine_state:'STOPPED', load_state:'unloaded', current_amps:0 }, idle_stats:null, idle_config:null, waste_config:null, loss_stats:{ device_id:'AD00000010', day_bucket:'2026-04-26', last_telemetry_ts:'2026-04-26T06:30:00Z', updated_at:'2026-04-26T06:30:00Z', tariff_configured:true, currency:'INR', full_load_current_a:15, idle_threshold_pct_of_fla:20, derived_idle_threshold_a:3, derived_overconsumption_threshold_a:15, today:{ idle_kwh:0, idle_cost_inr:0, off_hours_kwh:0, off_hours_cost_inr:0, overconsumption_kwh:0, overconsumption_cost_inr:0, total_loss_kwh:0, total_loss_cost_inr:0, today_energy_kwh:0, today_energy_cost_inr:0 } } }));
await page.route('**/backend/device/api/v1/devices/AD00000010/performance-trends**', route => fulfillJson(route, { metric:'health', range:'1h', points:[], summary:null }));
await page.route('**/backend/rule-engine/api/v1/alerts/events?**', route => fulfillJson(route, { data:[], total:0, page:1, page_size:25, total_pages:0 }));
await page.route('**/backend/rule-engine/api/v1/alerts/events/unread-count?**', route => fulfillJson(route, { data:{ count:0 } }));
await page.route('**/backend/device/api/v1/devices/AD00000010/mqtt-credential', route => route.fulfill({status:404, contentType:'application/json', body: JSON.stringify({error:'DEVICE_MQTT_CREDENTIAL_NOT_FOUND'})}));
await page.route('**/backend/device/api/v1/devices/AD00000010/maintenance-log/summary', route => fulfillJson(route, { success:true, data:buildSummary() }));
await page.route('**/backend/device/api/v1/devices/AD00000010/maintenance-log', async (route, request) => {
  if (request.method()==='GET') return fulfillJson(route, { success:true, data:records });
  return fulfillJson(route, { success:true, data:{} });
});

await page.goto('/machines/AD00000010', { waitUntil: 'domcontentloaded' });
console.log('title', await page.title());
await page.waitForTimeout(3000);
console.log('url', page.url());
console.log('bodytext', (await page.locator('body').innerText()).slice(0,2000));
await page.screenshot({ path: '/tmp/maintenance-debug.png', fullPage: true });
await browser.close();
