import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const apiNormalizer = require('../apiNormalizer.js');

describe('apiNormalizer', () => {
  it('normalizes website policy payload', () => {
    const normalized = apiNormalizer.normalizeWebsitePolicyResponse({
      mode: 'active',
      restricted_site_meta: [{ domain: 'youtube.com', category: 'Productivity', reason: '' }],
      global_restricted_sites: ['global.example'],
      effective_restricted_sites: ['global.example', 'youtube.com'],
      effective_policy_version: 'v1_2',
      agent_policy_version: 'agent-v1',
      agent_policy_last_seen_at: '2026-03-06T10:00:00Z',
      policy_cache_state: 'fresh',
      policy_cache_age_seconds: 12,
      policy_stale: true,
      rebuild_enqueued: true,
      identity_link_status: 'linked',
      linked_inventory_device_id: 44,
      violations_today: 1,
      recent_violations: [{ domain: 'youtube.com', time: '2026-03-05T10:32:00' }],
    });

    expect(normalized.mode).toBe('active');
    expect(normalized.restrictedDomains).toHaveLength(1);
    expect(normalized.restrictedDomains[0].domain).toBe('youtube.com');
    expect(normalized.globalRestrictedSites).toEqual(['global.example']);
    expect(normalized.effectiveRestrictedSites).toEqual(['global.example', 'youtube.com']);
    expect(normalized.effectivePolicyVersion).toBe('v1_2');
    expect(normalized.agentPolicyVersion).toBe('agent-v1');
    expect(normalized.agentPolicyLastSeenAt).toBe('2026-03-06T10:00:00Z');
    expect(normalized.policyCacheState).toBe('fresh');
    expect(normalized.policyCacheAgeSeconds).toBe(12);
    expect(normalized.policyStale).toBe(true);
    expect(normalized.rebuildEnqueued).toBe(true);
    expect(normalized.identityLinkStatus).toBe('linked');
    expect(normalized.linkedInventoryDeviceId).toBe(44);
    expect(normalized.violationsToday).toBe(1);
  });

  it('normalizes website policy fallback branches', () => {
    const normalized = apiNormalizer.normalizeWebsitePolicyResponse({
      restricted_sites: ['one.example', { domain: 'two.example', category: 'Security', reason: 'risk' }, null],
      recent_violations: ['bad'],
      policy_cache_age_seconds: 'not-a-number',
      identity_link_status: '',
      linked_inventory_device_id: 0,
    });

    expect(normalized.mode).toBe('active');
    expect(normalized.restrictedDomains).toEqual([
      { domain: 'one.example', category: 'Custom', reason: '' },
      { domain: 'two.example', category: 'Security', reason: 'risk' },
    ]);
    expect(normalized.recentViolations[0].domain).toBe('unknown');
    expect(normalized.policyCacheAgeSeconds).toBe(0);
    expect(normalized.identityLinkStatus).toBe('unlinked');
    expect(normalized.linkedInventoryDeviceId).toBe(null);
  });

  it('normalizes alerts response and active count', () => {
    const normalized = apiNormalizer.normalizeAlertsResponse({
      alerts: [
        { event_id: '1', domain: 'youtube.com', status: 'active', severity: 'Medium' },
        { event_id: '2', domain: 'example.com', status: 'resolved', severity: 'Low' },
      ],
      risk_score: 58,
      risk_level: 'medium',
    });

    expect(normalized.alerts).toHaveLength(2);
    expect(normalized.activeAlertCount).toBe(1);
    expect(normalized.riskScore).toBe(58);
    expect(normalized.riskLevel).toBe('medium');
  });

  it('normalizes alert fallbacks and device state', () => {
    const normalized = apiNormalizer.normalizeAlertsResponse({
      alerts: ['bad', { id: '2', site_visited: 'fallback.example', timestamp: '2026-03-06T10:00:00Z' }],
      device_state: { risk: 'high' },
    });

    expect(normalized.alerts[0].domain).toBe('unknown');
    expect(normalized.alerts[1].domain).toBe('fallback.example');
    expect(normalized.deviceState).toEqual({ risk: 'high' });
    expect(normalized.activeAlertCount).toBe(2);
  });
});
