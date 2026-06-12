import { buildOptions } from '../lib/config.js';
import { discoverRuntimeContext, requireScenarioFeatures } from '../lib/discovery.js';
import { idleThinkTime, runRulesFlow } from '../lib/workloads.js';

export const options = buildOptions('rules-alerts');

export function setup() {
  const seed = discoverRuntimeContext('rules-alerts-bootstrap');
  requireScenarioFeatures(seed, ['rules'], 'rules-alerts');
  return seed;
}

export default function (seed) {
  runRulesFlow(seed, 'rules-alerts');
  idleThinkTime();
}
