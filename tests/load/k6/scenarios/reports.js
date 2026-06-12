import { buildOptions } from '../lib/config.js';
import { discoverRuntimeContext, requireScenarioFeatures } from '../lib/discovery.js';
import { idleThinkTime, runReportsFlow } from '../lib/workloads.js';

export const options = buildOptions('reports');

export function setup() {
  const seed = discoverRuntimeContext('reports-bootstrap');
  requireScenarioFeatures(seed, ['reports'], 'reports');
  return seed;
}

export default function (seed) {
  runReportsFlow(seed, 'reports');
  idleThinkTime();
}
