import { buildOptions, runtimeConfig } from '../lib/config.js';
import { discoverRuntimeContext, requireScenarioFeatures } from '../lib/discovery.js';
import { idleThinkTime, runAnalyticsFlow, runReportsFlow, runRulesFlow, runWasteFlow } from '../lib/workloads.js';

export const options = buildOptions('mixed');

function weightedOperations() {
  const operations = [];
  const pushMany = (count, name) => {
    for (let index = 0; index < Math.max(0, count); index += 1) {
      operations.push(name);
    }
  };
  pushMany(runtimeConfig.mixedWeights.analytics, 'analytics');
  pushMany(runtimeConfig.mixedWeights.reports, 'reports');
  pushMany(runtimeConfig.mixedWeights.waste, 'waste');
  pushMany(runtimeConfig.mixedWeights.rules, 'rules');
  return operations.length > 0 ? operations : ['analytics', 'reports', 'waste', 'rules'];
}

const operations = weightedOperations();

function chooseOperation() {
  return operations[Math.floor(Math.random() * operations.length)];
}

export function setup() {
  const seed = discoverRuntimeContext('mixed-bootstrap');
  requireScenarioFeatures(seed, ['analytics', 'reports', 'waste_analysis', 'rules'], 'mixed');
  return seed;
}

export default function (seed) {
  const operation = chooseOperation();
  if (operation === 'analytics') {
    runAnalyticsFlow(seed, 'mixed');
  } else if (operation === 'reports') {
    runReportsFlow(seed, 'mixed');
  } else if (operation === 'waste') {
    runWasteFlow(seed, 'mixed');
  } else {
    runRulesFlow(seed, 'mixed');
  }
  idleThinkTime();
}
