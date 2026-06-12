import { buildOptions } from '../lib/config.js';
import { discoverRuntimeContext, requireScenarioFeatures } from '../lib/discovery.js';
import { idleThinkTime, runWasteFlow } from '../lib/workloads.js';

export const options = buildOptions('waste');

export function setup() {
  const seed = discoverRuntimeContext('waste-bootstrap');
  requireScenarioFeatures(seed, ['waste_analysis'], 'waste');
  return seed;
}

export default function (seed) {
  runWasteFlow(seed, 'waste');
  idleThinkTime();
}
