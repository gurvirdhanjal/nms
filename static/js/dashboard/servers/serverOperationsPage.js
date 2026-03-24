import { initServerModal } from '../modals/serverDetailModal.js';
import {
    renderEnhancedServerTable,
    renderFleetOverview,
    renderServerHealthSummary,
    setServerHealthFilter,
} from './serverHealth.js';

const EMPTY_SNAPSHOT = {
    counts: { total: 0, healthy: 0, warning: 0, critical: 0, offline: 0 },
    filters: { all: 0, problem: 0, healthy: 0 },
    servers: [],
    health: { healthy: 0, warning: 0, critical: 0, offline: 0 },
    impact_summary: {
        affected_servers: 0,
        healthy_servers: 0,
        total_servers: 0,
        fleet_pct: 0,
        primary_issue_label: 'No active server issues',
        primary_issue_severity: 'Healthy',
        unaffected_domains: ['CPU', 'Memory', 'Disk'],
    },
    metric_cards: {},
    active_issues: [],
    trends: { labels: [], cpu: {}, memory: {}, disk: {} },
    p95: { cpu: 0, memory: 0, disk: 0 },
    uptime: { current_24h_pct: 0, delta_pct: 0 },
};

let currentSnapshot = { ...EMPTY_SNAPSHOT };
let isLoading = false;
let pollHandle = null;

function setRefreshBusy(busy) {
    const button = document.getElementById('btnRefreshDashboard');
    if (!button) return;
    button.disabled = busy;
    button.innerHTML = busy
        ? '<i class="fas fa-sync-alt fa-spin me-1"></i>Refreshing...'
        : '<i class="fas fa-sync-alt me-1"></i>Refresh';
}

function updateLastCheck(timestamp, fallback = '-') {
    const element = document.getElementById('server-last-check');
    if (!element) return;
    if (!timestamp) {
        element.textContent = fallback;
        return;
    }
    const date = new Date(timestamp);
    element.textContent = Number.isNaN(date.getTime())
        ? fallback
        : date.toLocaleString(undefined, {
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
}

function renderSnapshot(snapshot) {
    try { renderServerHealthSummary(snapshot); } catch (e) { console.error('[ServerOps] renderServerHealthSummary failed:', e); }
    try { renderFleetOverview(snapshot); } catch (e) { console.error('[ServerOps] renderFleetOverview failed:', e); }
    try { renderEnhancedServerTable(snapshot); } catch (e) { console.error('[ServerOps] renderEnhancedServerTable failed:', e); }
    updateLastCheck(snapshot?.timestamp, 'Unavailable');
}

async function fetchFleetSnapshot({ showBusy = false } = {}) {
    if (isLoading) return;
    isLoading = true;
    if (showBusy) setRefreshBusy(true);

    try {
        const response = await fetch('/api/server/fleet-metrics', {
            headers: { Accept: 'application/json' },
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        currentSnapshot = payload || { ...EMPTY_SNAPSHOT };
        renderSnapshot(currentSnapshot);
    } catch (error) {
        console.error('[ServerOperationsPage] Failed to fetch fleet metrics:', error);
        updateLastCheck(null, 'Refresh failed');
        if (!Array.isArray(currentSnapshot?.servers) || !currentSnapshot.servers.length) {
            currentSnapshot = { ...EMPTY_SNAPSHOT };
            renderServerHealthSummary(currentSnapshot);
            renderEnhancedServerTable(currentSnapshot);
        }
    } finally {
        if (showBusy) setRefreshBusy(false);
        isLoading = false;
    }
}

function bindFilterButtons() {
    document.querySelectorAll('[data-server-filter]').forEach((button) => {
        button.addEventListener('click', () => {
            setServerHealthFilter(button.getAttribute('data-server-filter') || 'all');
            renderEnhancedServerTable(currentSnapshot);
        });
    });
}

function bindRefreshButton() {
    const button = document.getElementById('btnRefreshDashboard');
    if (!button) return;
    button.addEventListener('click', () => {
        fetchFleetSnapshot({ showBusy: true });
    });
}

export function initServerOperationsPage() {
    initServerModal();
    setServerHealthFilter('all');
    bindFilterButtons();
    bindRefreshButton();
    renderSnapshot(currentSnapshot);
    fetchFleetSnapshot();

    if (!pollHandle) {
        pollHandle = window.setInterval(() => {
            fetchFleetSnapshot();
        }, 30000);
    }
}
