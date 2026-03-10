(function (global) {
    'use strict';

    function normalizeConnectivity(value) {
        const key = String(value || '').trim().toLowerCase();
        if (key === 'online' || key === 'degraded' || key === 'offline') return key;
        return 'offline';
    }

    function normalizeTelemetry(value) {
        const key = String(value || '').trim().toLowerCase();
        if (key === 'healthy' || key === 'partial' || key === 'stale' || key === 'degraded' || key === 'critical' || key === 'offline') {
            return key;
        }
        return 'stale';
    }

    function normalizeRisk(value) {
        const key = String(value || '').trim().toLowerCase();
        if (key === 'low' || key === 'medium' || key === 'high') return key;
        return 'low';
    }

    function resolveRiskLevel(score) {
        const bounded = Math.max(0, Math.min(100, Number(score) || 0));
        if (bounded >= 70) return 'high';
        if (bounded >= 35) return 'medium';
        return 'low';
    }

    function deriveRiskScore(input) {
        const source = input || {};
        const policyViolations = Math.max(0, Number(source.policyViolations) || 0);
        const suspiciousProcesses = Math.max(0, Number(source.suspiciousProcesses) || 0);
        const alertsCount = Math.max(0, Number(source.alertsCount) || 0);
        const telemetry = normalizeTelemetry(source.telemetry);

        let score = 0;
        score += policyViolations * 12;
        score += suspiciousProcesses * 8;
        score += alertsCount * 6;
        if (telemetry === 'critical' || telemetry === 'offline') score += 28;
        else if (telemetry === 'degraded' || telemetry === 'partial' || telemetry === 'stale') score += 12;

        return Math.max(0, Math.min(100, Math.round(score)));
    }

    function deriveDeviceState(input) {
        const source = input || {};
        const connectivity = normalizeConnectivity(source.connectivity);
        const telemetry = normalizeTelemetry(source.telemetry);
        const policyViolations = Math.max(0, Number(source.policyViolations) || 0);
        const riskScore = Number.isFinite(Number(source.riskScore))
            ? Math.max(0, Math.min(100, Math.round(Number(source.riskScore))))
            : deriveRiskScore(source);

        return {
            connectivity: connectivity,
            telemetry: telemetry,
            policy: policyViolations > 0 ? 'violations' : 'compliant',
            risk: normalizeRisk(source.risk) || resolveRiskLevel(riskScore),
            risk_score: riskScore,
        };
    }

    const api = {
        deriveDeviceState: deriveDeviceState,
        deriveRiskScore: deriveRiskScore,
        resolveRiskLevel: resolveRiskLevel,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleStateMachine = api;
})(typeof window !== 'undefined' ? window : globalThis);
