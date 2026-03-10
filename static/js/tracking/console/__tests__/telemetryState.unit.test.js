import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const telemetryState = require('../telemetryState.js');

describe('telemetryState', () => {
  it('returns healthy below 50ms latency', () => {
    const result = telemetryState.deriveTelemetryState({
      latencyMs: 20,
      heartbeatAgeSeconds: 2,
      pollSeconds: 5,
      hasResponse: true,
    });
    expect(result.state).toBe('healthy');
  });

  it('returns degraded between 50 and 150ms latency', () => {
    const result = telemetryState.deriveTelemetryState({
      latencyMs: 80,
      heartbeatAgeSeconds: 2,
      pollSeconds: 5,
      hasResponse: true,
    });
    expect(result.state).toBe('degraded');
  });

  it('returns critical over 150ms latency', () => {
    const result = telemetryState.deriveTelemetryState({
      latencyMs: 170,
      heartbeatAgeSeconds: 2,
      pollSeconds: 5,
      hasResponse: true,
    });
    expect(result.state).toBe('critical');
  });

  it('returns offline when no response', () => {
    const result = telemetryState.deriveTelemetryState({ hasResponse: false });
    expect(result.state).toBe('offline');
  });
});
