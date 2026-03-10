(function (global) {
    'use strict';

    function toNumber(value, fallbackValue) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallbackValue;
    }

    function normalizeDomainEntry(value) {
        if (value && typeof value === 'object') {
            return {
                domain: String(value.domain || '').trim(),
                category: String(value.category || 'Custom').trim() || 'Custom',
                reason: String(value.reason || '').trim(),
            };
        }
        return {
            domain: String(value || '').trim(),
            category: 'Custom',
            reason: '',
        };
    }

    function normalizeWebsitePolicyResponse(payload) {
        const src = payload && typeof payload === 'object' ? payload : {};
        const domainSource = Array.isArray(src.restricted_site_meta)
            ? src.restricted_site_meta
            : (Array.isArray(src.restricted_sites) ? src.restricted_sites : []);
        const restrictedDomains = domainSource
            .map(normalizeDomainEntry)
            .filter(function (row) { return row.domain; });

        const recentSource = Array.isArray(src.recent_violations) ? src.recent_violations : [];
        const recentViolations = recentSource.map(function (entry) {
            const row = entry && typeof entry === 'object' ? entry : {};
            return {
                time: row.time || row.timestamp || null,
                domain: row.domain || row.site || 'unknown',
                severity: String(row.severity || 'Medium'),
                user: row.user || 'unknown',
                action: row.action || 'Blocked',
            };
        });

        return {
            mode: String(src.mode || 'active').toLowerCase(),
            restrictedDomains: restrictedDomains,
            globalRestrictedSites: Array.isArray(src.global_restricted_sites) ? src.global_restricted_sites : [],
            effectiveRestrictedSites: Array.isArray(src.effective_restricted_sites) ? src.effective_restricted_sites : [],
            effectivePolicyVersion: String(src.effective_policy_version || '').trim(),
            agentPolicyVersion: String(src.agent_policy_version || '').trim(),
            agentPolicyLastSeenAt: src.agent_policy_last_seen_at || null,
            policyCacheState: String(src.policy_cache_state || 'fresh').trim().toLowerCase(),
            policyCacheAgeSeconds: Math.max(0, toNumber(src.policy_cache_age_seconds, 0)),
            policyStale: Boolean(src.policy_stale),
            rebuildEnqueued: Boolean(src.rebuild_enqueued),
            identityLinkStatus: String(src.identity_link_status || 'unlinked').trim().toLowerCase(),
            linkedInventoryDeviceId: src.linked_inventory_device_id || null,
            violationsToday: Math.max(0, toNumber(src.violations_today, 0)),
            recentViolations: recentViolations,
        };
    }

    function normalizeAlertCard(value) {
        const row = value && typeof value === 'object' ? value : {};
        return {
            eventId: row.event_id || row.id || '',
            title: row.title || 'Policy Violation',
            domain: row.domain || row.site || row.site_visited || 'unknown',
            user: row.user || 'unknown',
            severity: String(row.severity || 'Medium'),
            time: row.time || row.timestamp || row.observed_at_utc || null,
            status: String(row.status || 'active').toLowerCase(),
            action: row.action || 'Blocked',
        };
    }

    function normalizeAlertsResponse(payload) {
        const src = payload && typeof payload === 'object' ? payload : {};
        const alerts = (Array.isArray(src.alerts) ? src.alerts : []).map(normalizeAlertCard);
        const activeCount = Math.max(
            0,
            toNumber(
                src.active_alert_count,
                alerts.filter(function (item) { return item.status !== 'resolved'; }).length
            )
        );
        return {
            alerts: alerts,
            activeAlertCount: activeCount,
            riskScore: Math.max(0, toNumber(src.risk_score, 0)),
            riskLevel: String(src.risk_level || 'low').toLowerCase(),
            deviceState: src.device_state && typeof src.device_state === 'object' ? src.device_state : null,
        };
    }

    const api = {
        normalizeWebsitePolicyResponse: normalizeWebsitePolicyResponse,
        normalizeAlertsResponse: normalizeAlertsResponse,
        normalizeAlertCard: normalizeAlertCard,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleApiNormalizer = api;
})(typeof window !== 'undefined' ? window : globalThis);
