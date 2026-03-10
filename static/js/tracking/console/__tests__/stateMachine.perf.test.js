import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const stateMachine = require('../stateMachine.js');
const cacheStore = require('../cacheStore.js');

describe('state machine perf', () => {
  it('derives 5k state updates under threshold', () => {
    const start = performance.now();
    for (let i = 0; i < 5000; i += 1) {
      stateMachine.deriveDeviceState({
        connectivity: i % 2 === 0 ? 'online' : 'degraded',
        telemetry: i % 3 === 0 ? 'healthy' : 'degraded',
        policyViolations: i % 4,
        suspiciousProcesses: i % 5,
        alertsCount: i % 6,
      });
    }
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(120);
  });

  it('handles cache get/set operations under threshold', () => {
    const cache = cacheStore.createCacheStore(10000);
    const start = performance.now();
    for (let i = 0; i < 10000; i += 1) {
      const key = `k:${i}`;
      cache.set(key, i);
      cache.get(key);
    }
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(180);
  });
});
