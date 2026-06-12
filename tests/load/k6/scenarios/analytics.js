import { buildOptions } from '../lib/config.js';
import { discoverRuntimeContext, requireScenarioFeatures } from '../lib/discovery.js';
import { idleThinkTime, runAnalyticsFlow } from '../lib/workloads.js';

export const options = buildOptions('analytics');

export function setup() {
  const seed = discoverRuntimeContext('analytics-bootstrap');
  requireScenarioFeatures(seed, ['analytics'], 'analytics');
  return seed;
}

export default function (seed) {
  runAnalyticsFlow(seed, 'analytics');
  idleThinkTime();
}
