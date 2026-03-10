import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const mutationLocks = require('../mutationLocks.js');
const apiNormalizer = require('../apiNormalizer.js');
const ackFallbackStore = require('../ackFallbackStore.js');

describe('device live DOM integration', () => {
  it('supports retry button behavior for policy and alerts', () => {
    document.body.innerHTML = `
      <div id="root">
        <button id="retryPolicy" data-action-retry-policy="1">Retry Policy</button>
        <button id="retryAlerts" data-action-retry-alerts="1">Retry Alerts</button>
      </div>
    `;

    const seen = [];
    document.getElementById('root').addEventListener('click', (event) => {
      if (event.target.closest('[data-action-retry-policy]')) seen.push('policy');
      if (event.target.closest('[data-action-retry-alerts]')) seen.push('alerts');
    });

    document.getElementById('retryPolicy').click();
    document.getElementById('retryAlerts').click();

    expect(seen).toEqual(['policy', 'alerts']);
  });

  it('enforces mutation lock disable-enable cycle', async () => {
    document.body.innerHTML = `<button id="addDomain">Add Domain</button>`;
    const button = document.getElementById('addDomain');
    const locks = mutationLocks.createMutationLocks();

    const task = locks.withLock('policy:add', async () => {
      button.disabled = true;
      await new Promise((resolve) => setTimeout(resolve, 10));
      button.disabled = false;
    });

    await expect(locks.withLock('policy:add', async () => null)).rejects.toMatchObject({ code: 'LOCKED' });
    await task;
    expect(button.disabled).toBe(false);
  });

  it('updates alert badge count from normalized payload', () => {
    document.body.innerHTML = `<span id="tabCountAlerts" class="d-none">0</span>`;
    const badge = document.getElementById('tabCountAlerts');

    const normalized = apiNormalizer.normalizeAlertsResponse({
      alerts: [
        { event_id: 'a1', status: 'active', domain: 'youtube.com' },
        { event_id: 'a2', status: 'active', domain: 'example.com' },
        { event_id: 'a3', status: 'resolved', domain: 'chatgpt.com' },
      ],
    });

    badge.textContent = String(normalized.activeAlertCount);
    badge.classList.toggle('d-none', normalized.activeAlertCount === 0);

    expect(badge.textContent).toBe('2');
    expect(badge.classList.contains('d-none')).toBe(false);
  });

  it('persists local acknowledge marker across reads', () => {
    const store = ackFallbackStore.createAckFallbackStore({ key: 'ack-test', storage: window.localStorage });
    store.clear();

    expect(store.isAcked('event-1')).toBe(false);
    store.markAcked('event-1');
    expect(store.isAcked('event-1')).toBe(true);
  });

  it('maps effective policy metadata into the website policy panel contract', () => {
    document.body.innerHTML = `
      <strong id="policyEffectiveVersion"></strong>
      <strong id="policyAgentVersion"></strong>
      <strong id="policyCacheState"></strong>
      <div id="policyCacheWarning" class="d-none"></div>
      <div id="policyRestrictedCount"></div>
      <div id="policyGlobalCount"></div>
      <div id="policyEffectiveCount"></div>
    `;

    const normalized = apiNormalizer.normalizeWebsitePolicyResponse({
      restricted_site_meta: [{ domain: 'device.example', category: 'Custom' }],
      global_restricted_sites: ['global.example'],
      effective_restricted_sites: ['device.example', 'global.example'],
      effective_policy_version: 'v2_2',
      agent_policy_version: 'agent-v1',
      policy_cache_state: 'stale_fallback',
      policy_stale: true,
    });

    document.getElementById('policyEffectiveVersion').textContent = normalized.effectivePolicyVersion;
    document.getElementById('policyAgentVersion').textContent = normalized.agentPolicyVersion;
    document.getElementById('policyCacheState').textContent = normalized.policyCacheState.toUpperCase();
    document.getElementById('policyCacheWarning').classList.toggle('d-none', !normalized.policyStale);
    document.getElementById('policyRestrictedCount').textContent = String(normalized.restrictedDomains.length);
    document.getElementById('policyGlobalCount').textContent = String(normalized.globalRestrictedSites.length);
    document.getElementById('policyEffectiveCount').textContent = String(normalized.effectiveRestrictedSites.length);

    expect(document.getElementById('policyEffectiveVersion').textContent).toBe('v2_2');
    expect(document.getElementById('policyAgentVersion').textContent).toBe('agent-v1');
    expect(document.getElementById('policyCacheState').textContent).toBe('STALE_FALLBACK');
    expect(document.getElementById('policyCacheWarning').classList.contains('d-none')).toBe(false);
    expect(document.getElementById('policyRestrictedCount').textContent).toBe('1');
    expect(document.getElementById('policyGlobalCount').textContent).toBe('1');
    expect(document.getElementById('policyEffectiveCount').textContent).toBe('2');
  });
});
