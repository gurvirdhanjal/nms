import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const stateMachine = require('../stateMachine.js');

describe('stateMachine', () => {
  it('derives consistent device state contract', () => {
    const state = stateMachine.deriveDeviceState({
      connectivity: 'online',
      telemetry: 'degraded',
      policyViolations: 2,
      suspiciousProcesses: 1,
      alertsCount: 3,
    });

    expect(state.connectivity).toBe('online');
    expect(state.telemetry).toBe('degraded');
    expect(state.policy).toBe('violations');
    expect(['low', 'medium', 'high']).toContain(state.risk);
    expect(typeof state.risk_score).toBe('number');
  });

  it('falls back to sane defaults', () => {
    const state = stateMachine.deriveDeviceState({});
    expect(state.connectivity).toBe('offline');
    expect(state.telemetry).toBe('stale');
    expect(state.policy).toBe('compliant');
  });

  it('resolves risk thresholds deterministically', () => {
    expect(stateMachine.resolveRiskLevel(10)).toBe('low');
    expect(stateMachine.resolveRiskLevel(40)).toBe('medium');
    expect(stateMachine.resolveRiskLevel(80)).toBe('high');
  });
});
