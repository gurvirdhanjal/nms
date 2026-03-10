(function (global) {
    'use strict';

    function toNumber(value, fallbackValue) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallbackValue;
    }

    function deriveTelemetryState(input) {
        const source = input || {};
        const latencyMs = Math.max(0, toNumber(source.latencyMs, 0));
        const heartbeatAgeSeconds = Math.max(0, toNumber(source.heartbeatAgeSeconds, 0));
        const pollSeconds = Math.max(1, toNumber(source.pollSeconds, 5));
        const hasResponse = source.hasResponse !== false;

        if (!hasResponse) {
            return { state: 'offline', label: 'OFFLINE', color: 'var(--s-critical)' };
        }

        if (heartbeatAgeSeconds > pollSeconds * 3) {
            return { state: 'stale', label: 'TELEMETRY STALE', color: 'var(--s-warning)' };
        }

        if (latencyMs > 150) {
            return { state: 'critical', label: 'TELEMETRY CRITICAL', color: 'var(--s-critical)' };
        }
        if (latencyMs >= 50) {
            return { state: 'degraded', label: 'TELEMETRY DEGRADED', color: 'var(--s-warning)' };
        }

        return { state: 'healthy', label: 'LIVE TELEMETRY', color: 'var(--s-healthy)' };
    }

    const api = {
        deriveTelemetryState: deriveTelemetryState,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleTelemetryState = api;
})(typeof window !== 'undefined' ? window : globalThis);
