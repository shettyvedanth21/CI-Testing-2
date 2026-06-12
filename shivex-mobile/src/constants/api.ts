/**
 * Central API configuration.
 * Change BASE_HOST for your deployment.
 * In production, replace with your actual server IP or domain.
 */
const BASE_HOST = "http://192.168.1.3"; // LAN IP - change per deployment

export const API_CONFIG = {
  AUTH_SERVICE: `${BASE_HOST}:8090`,
  DEVICE_SERVICE: `${BASE_HOST}:8000`,
  DATA_SERVICE: `${BASE_HOST}:8081`,
  RULE_ENGINE_SERVICE: `${BASE_HOST}:8002`,
  ENERGY_SERVICE: `${BASE_HOST}:8010`,
  REPORTING_SERVICE: `${BASE_HOST}:8085`,
  WASTE_ANALYSIS: `${BASE_HOST}:8087`,
  ANALYTICS_SERVICE: `${BASE_HOST}:8003`,
  COPILOT_SERVICE: `${BASE_HOST}:8007`,
} as const;
