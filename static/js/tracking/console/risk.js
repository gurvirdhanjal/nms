(function (global) {
    'use strict';

    function calculateRiskScore(input) {
        const source = input || {};
        const alerts = Array.isArray(source.alerts) ? source.alerts : [];
        const policyViolations = Math.max(0, Number(source.policyViolations) || 0);
        const suspiciousProcesses = Math.max(0, Number(source.suspiciousProcesses) || 0);
        const telemetry = String(source.telemetry || '').trim().toLowerCase();

        let score = 0;
        alerts.forEach(function (alert) {
            const severity = String(alert && alert.severity || '').trim().toLowerCase();
            if (severity === 'high' || severity === 'critical') score += 18;
            else if (severity === 'medium' || severity === 'warning') score += 10;
            else score += 5;
        });

        score += policyViolations * 8;
        score += suspiciousProcesses * 6;

        if (telemetry === 'offline' || telemetry === 'critical') score += 25;
        else if (telemetry === 'degraded' || telemetry === 'partial' || telemetry === 'stale') score += 12;

        return Math.max(0, Math.min(100, Math.round(score)));
    }

    function riskLevelFromScore(score) {
        const bounded = Math.max(0, Math.min(100, Number(score) || 0));
        if (bounded >= 70) return 'high';
        if (bounded >= 35) return 'medium';
        return 'low';
    }

    function riskBarSegments(score, totalSegments) {
        const segments = Math.max(4, Number(totalSegments) || 10);
        const bounded = Math.max(0, Math.min(100, Number(score) || 0));
        return Math.round((bounded / 100) * segments);
    }

    const api = {
        calculateRiskScore: calculateRiskScore,
        riskLevelFromScore: riskLevelFromScore,
        riskBarSegments: riskBarSegments,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleRisk = api;
})(typeof window !== 'undefined' ? window : globalThis);
