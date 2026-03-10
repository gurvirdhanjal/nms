import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const risk = require('../risk.js');

describe('risk module', () => {
  it('calculates bounded score and level', () => {
    const score = risk.calculateRiskScore({
      alerts: [{ severity: 'high' }, { severity: 'medium' }],
      policyViolations: 2,
      suspiciousProcesses: 3,
      telemetry: 'degraded',
    });
    expect(score).toBeGreaterThan(0);
    expect(score).toBeLessThanOrEqual(100);
    expect(['low', 'medium', 'high']).toContain(risk.riskLevelFromScore(score));
  });

  it('returns segment counts for score bars', () => {
    expect(risk.riskBarSegments(0, 10)).toBe(0);
    expect(risk.riskBarSegments(100, 10)).toBe(10);
    expect(risk.riskBarSegments(50, 2)).toBe(2);
  });

  it('covers low-severity and risk-level boundaries', () => {
    const lowScore = risk.calculateRiskScore({
      alerts: [{ severity: 'low' }],
      policyViolations: 0,
      suspiciousProcesses: 0,
      telemetry: 'healthy',
    });
    expect(lowScore).toBe(5);
    expect(risk.riskLevelFromScore(10)).toBe('low');
    expect(risk.riskLevelFromScore(40)).toBe('medium');
    expect(risk.riskLevelFromScore(90)).toBe('high');
  });
});
