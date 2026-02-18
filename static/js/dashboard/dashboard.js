/**
 * Dashboard Orchestrator
 */
import { fetchFullSnapshot, fetchAvailabilityDetails, fetchSubnetDetails } from './api.js';
import { updateState, updateStateBatch, getState, subscribe, loadFromCache, mergeRealtimeUpdate } from './state.js';
import { renderDevicesOnline } from './cards/devicesOnline.js';
import { renderDeviceStatusCards } from './cards/deviceStatus.js';
import { renderNetworkAvailability } from './cards/networkAvailability.js';
import { renderTopLatencyTable, renderTopPacketLossTable, renderTopAffectedDevices } from './tables/topProblems.js';
import { renderInventoryTable, initInventoryInteractions } from './tables/inventoryTable.js';
import { renderInventoryChart } from './charts/inventoryChart.js';
import { initDiscovery } from './discovery.js';
import { initServerModal } from './modals/serverDetailModal.js';
import { renderServerHealthSummary, renderServerHealthTable, initServerHealthTable, setServerHealthFilter, renderFleetOverview, renderEnhancedServerTable } from './servers/serverHealth.js';
import { initAlertCenter, renderAlertCenter } from './alerts/alertCenter.js';
import { renderConnectionIndicator, initConnectionIndicator } from './connectionIndicator.js';
import { timeAgo, setupTacticalDropdown, formatPercent, formatNumber } from './utils.js';
import { initSSE } from './sseClient.js';
import { patchKeyedTableRows, setTableMessageRow } from './domPatch.js';

console.log("[Dashboard] Module loading...");

// Prevent double-init (e.g., back/forward cache)
const dashboardBootKey = '__dashboardBooted';

// Polling state
let pollingInterval = null;
const POLLING_INTERVAL_MS = 30000;
const SSE_REFRESH_DEBOUNCE_MS = 1200;
let isSSEConnected = false;
let sseInitialized = false;
let sseRefreshTimer = null;
let sseRefreshForceFreshTopProblems = false;

// Batch DOM updates to the next animation frame
// Batch DOM updates to the next animation frame
let renderScheduled = false;
let latestState = null;
let booting = true;
let maintenanceModalInstance = null;
let availabilityModalInstance = null;
let subnetModalInstance = null;
let latestSubnetHealthRows = [];
let subnetDetailsRequestSeq = 0;
const SUBNET_DETAILS_CACHE_TTL_MS = 15000;
const subnetDetailsCache = new Map();

// Init
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDashboard);
} else {
    // Already loaded (or interactive)
    initDashboard();
}

function initDashboard() {
    if (window[dashboardBootKey]) {
        // Page might be restored from cache; just refresh data
        refreshAll().catch(() => { });
        if (!sseInitialized) initRealtimeTransport();
        if (!isSSEConnected) startPolling('restore');
        return;
    }
    window[dashboardBootKey] = true;

    console.log('[Dashboard] Initializing...');
    const errorEl = document.getElementById('global-error');

    try {
        // 1. Initialize connection indicator (polling mode)
        console.log('[Dashboard] Setting up connection indicator (polling)...');
        initConnectionIndicator();
        initRealtimeTransport();

        // 2. Initialize Discovery UI
        initDiscovery();

        // 3. RENDER STRUCTURE FIRST (Skeleton / Cache)
        // 3. RENDER STRUCTURE FIRST (Skeleton / Cache)
        if (loadFromCache()) {
            console.log('[Dashboard] Loaded cache, immediate critical render...');
            // Render visible state immediately (CRITICAL ONLY)
            requestAnimationFrame(() => renderCritical(getState()));
        } else {
            // No cache? Render empty skeleton state if needed
            // (The HTML already provides a good skeleton structure)
            console.log('[Dashboard] No cache, waiting for fetch...');
        }

        // 4. Initial Fetch (Stale-while-revalidate pattern)
        // We fetch fresh data in the background, updating the UI when ready
        console.log('[Dashboard] Starting background fetch...');

        // Defer the network request slightly to let the UI paint first
        requestAnimationFrame(() => {
            refreshAll({ source: 'bootstrap-initial', ignoreModalGate: true }).catch(err => {
                console.error('[Dashboard] Async fetch error:', err);
                showGlobalError(`Fetch Error: ${err.message}`);
            });
        });

        // 5. Start polling fallback loop while SSE connects.
        // Polling auto-stops when SSE is connected.
        startPolling('bootstrap');

        // 5. Setup Manual Refresh Button
        const refreshBtn = document.getElementById('btn-refresh');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                refreshBtn.classList.add('rotating');
                refreshAll().finally(() => {
                    setTimeout(() => refreshBtn.classList.remove('rotating'), 500);
                });
            });
        }

        // 6. Setup Time Range Dropdown
        const savedRange = localStorage.getItem('tactical_dashboard_range') || '24h';
        let timeRangeDropdown = null;

        if (document.getElementById('time-range-container')) {
            timeRangeDropdown = setupTacticalDropdown(
                'time-range-container',
                (newValue) => {
                    localStorage.setItem('tactical_dashboard_range', newValue);
                    refreshAll();
                },
                [
                    { value: '24h', label: 'Last 24 Hours' },
                    { value: '7d', label: 'Last 7 Days' },
                    { value: '30d', label: 'Last 30 Days' }
                ]
            );
            // Set initial
            if (timeRangeDropdown) timeRangeDropdown.setValue(savedRange);
        }

        // 7. Subscribe to State Changes to Render UI (batched to reduce DOM thrash)
        subscribe((state) => {
            scheduleRender(state);
        });

        // 8. Setup Tabs
        setupTabs();

        // 9. Init Inventory Interactions
        initInventoryInteractions();
        initSubnetInteractions();

        // 10. Init Server Modal
        initServerModal();

        // 11. Init Server Health Table
        initServerHealthTable();

        // 12. Init Alert Center
        initAlertCenter({
            onDeviceBreakdown: () => openDeviceBreakdown()
        });

        // 13. Init KPI interactions
        initDeviceBreakdown();
        initServerKpiInteractions();

        console.log('[Dashboard] Initialization sequence complete.');

    } catch (err) {
        console.error('[Dashboard] CRITICAL INIT ERROR:', err);
        showGlobalError(`Initialization Failed: ${err.message}`, true);
    }
}

/**
 * Start polling loop (always on).
 */
function startPolling(reason = 'fallback') {
    if (pollingInterval) return;
    console.log(`[Dashboard] Starting polling fallback (${POLLING_INTERVAL_MS / 1000}s interval)`, reason);
    renderConnectionIndicator('polling_fallback');
    pollingInterval = setInterval(() => {
        refreshAll({ source: 'polling' }).catch((err) => {
            console.error('[Dashboard] Polling refresh failed:', err);
        });
    }, POLLING_INTERVAL_MS);
}

function stopPolling() {
    if (!pollingInterval) return;
    clearInterval(pollingInterval);
    pollingInterval = null;
}

function initRealtimeTransport() {
    if (sseInitialized) return;
    sseInitialized = true;

    initSSE({
        onConnectionChange: handleSSEConnectionChange,
        onDeviceStatus: (payload) => handleSSEEvent('device_status', payload),
        onDeviceUpdate: (payload) => handleSSEEvent('device_update', payload),
        onAlertCreated: (payload) => handleSSEEvent('alert_created', payload),
        onLatencySpike: (payload) => handleSSEEvent('latency_spike', payload),
        onInterfaceThreshold: (payload) => handleSSEEvent('interface_threshold', payload),
        onClassificationUpdate: (payload) => handleSSEEvent('classification_update', payload)
    });
}

function handleSSEConnectionChange(status) {
    if (status === 'connected') {
        isSSEConnected = true;
        stopPolling();
        renderConnectionIndicator('sse_connected');
        const state = getState();
        const lastUpdateTs = state.lastUpdated ? new Date(state.lastUpdated).getTime() : 0;
        const isStale = !lastUpdateTs || (Date.now() - lastUpdateTs > POLLING_INTERVAL_MS);
        if (!state.summary || isStale) {
            refreshAll({ source: 'sse-connect-sync', ignoreModalGate: true }).catch((err) => {
                console.error('[Dashboard] SSE connect sync refresh failed:', err);
            });
        }
        return;
    }

    if (status === 'connecting') {
        renderConnectionIndicator('sse_connecting');
        return;
    }

    // disconnected
    isSSEConnected = false;
    renderConnectionIndicator('polling_fallback');

    if (document.visibilityState === 'visible') {
        startPolling('sse-disconnected');
        refreshAll({ source: 'sse-fallback' }).catch((err) => {
            console.error('[Dashboard] SSE fallback refresh failed:', err);
        });
    }
}

function handleSSEEvent(eventType, payload) {
    try {
        mergeRealtimeUpdate(eventType, payload || {});
    } catch (err) {
        console.error('[Dashboard] Failed to merge SSE event:', eventType, err);
    }

    // Keep full refresh coalesced and limited. Classification updates can be noisy.
    const shouldRefresh =
        eventType === 'device_status' ||
        eventType === 'device_update' ||
        eventType === 'alert_created' ||
        eventType === 'latency_spike' ||
        eventType === 'interface_threshold';

    if (!shouldRefresh) return;

    scheduleSSERefresh({
        forceFreshTopProblems: eventType === 'alert_created' || eventType === 'latency_spike'
    });
}

function scheduleSSERefresh({ forceFreshTopProblems = false } = {}) {
    if (document.visibilityState === 'hidden') return;
    if (forceFreshTopProblems) {
        sseRefreshForceFreshTopProblems = true;
    }

    if (sseRefreshTimer) return;

    sseRefreshTimer = setTimeout(() => {
        sseRefreshTimer = null;
        const refreshOptions = {
            source: 'sse',
            forceFreshTopProblems: sseRefreshForceFreshTopProblems
        };
        sseRefreshForceFreshTopProblems = false;
        refreshAll(refreshOptions).catch((err) => {
            console.error('[Dashboard] SSE refresh failed:', err);
        });
    }, SSE_REFRESH_DEBOUNCE_MS);
}

async function refreshAll(options = {}) {
    const { forceFreshTopProblems = false, source = 'manual', ignoreModalGate = false } = options;

    // SSE primary: skip periodic polling work while SSE is healthy.
    if (source === 'polling' && isSSEConnected) {
        return;
    }

    const hasOpenModal = !!document.querySelector('.modal.show');
    if (window.cleanupBootstrapModal && !hasOpenModal) {
        window.cleanupBootstrapModal();
    }
    if (hasOpenModal && !ignoreModalGate) {
        console.log('[Dashboard] Modal open; skipping refresh');
        return;
    }
    console.log('[Dashboard] Refreshing data...');
    updateState('isLoading', true);

    const timeRange = document.querySelector('#time-range-container .dropdown-toggle')?.dataset.value || '24h';

    // Helper for timeout
    const fetchWithTimeout = (p, ms = 15000) => Promise.race([
        p,
        new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), ms))
    ]);

    try {
        const snapshot = await fetchWithTimeout(fetchFullSnapshot({
            range: timeRange,
            forceFreshTopProblems,
            alertsStatus: 'active',
            alertsLimit: 200
        }));

        const sectionErrors = snapshot?.errors || {};
        const nextState = { isLoading: false };
        if (!('summary' in sectionErrors) && snapshot.summary !== undefined) nextState.summary = snapshot.summary;
        if (!('fleetMetrics' in sectionErrors) && snapshot.fleetMetrics !== undefined) nextState.fleetMetrics = snapshot.fleetMetrics;
        if (!('topProblems' in sectionErrors) && snapshot.topProblems !== undefined) nextState.topProblems = snapshot.topProblems;
        if (!('trends' in sectionErrors) && snapshot.trends !== undefined) nextState.trends = snapshot.trends;
        if (!('inventory' in sectionErrors) && snapshot.inventory !== undefined) nextState.inventory = snapshot.inventory;
        if (!('serverHealth' in sectionErrors) && snapshot.serverHealth !== undefined) nextState.serverHealth = snapshot.serverHealth;
        if (!('alerts' in sectionErrors) && snapshot.alerts !== undefined) nextState.alerts = snapshot.alerts;
        updateStateBatch(nextState);

        if (Object.keys(sectionErrors).length > 0) {
            console.warn('[Dashboard] Snapshot returned partial errors:', sectionErrors);
        }
    } catch (err) {
        updateStateBatch({ isLoading: false });
        throw err;
    }

    // If this was the boot sequence, we can now allow full rendering
    if (booting) {
        setTimeout(() => {
            booting = false;
            // Force a full re-render state check
            scheduleRender(getState());
        }, 100);
    }
}

// === RENDER ORCHESTRATION ===

function renderCritical(state) {
    if (state.error) return;
    try {
        const ts = state.lastUpdated ? state.lastUpdated.toISOString() : null;

        // 1. Summary KPIs (Network)
        if (state.summary) {
            safeRender('Devices Online', () => renderDevicesOnline(state.summary, ts));
            safeRender('Device Status Cards', () => renderDeviceStatusCards(state.summary, ts));
            safeRender('Overall Health', () => renderOverallHealth(state));
            safeRender('Subnet Health', () => renderSubnetHealth(state.summary.subnet_health));
        }

        // 2. Fleet Overview (KPIs)
        if (state.fleetMetrics) {
            safeRender('Fleet Overview', () => renderFleetOverview(state.fleetMetrics));
        }
    } catch (e) {
        console.error("Critical Render Error", e);
    }
}

function renderSecondary(state) {
    if (state.error) return;
    try {
        const ts = state.lastUpdated ? state.lastUpdated.toISOString() : null;

        // 3. Network Availability (Sparkline Chart)
        if (state.summary) {
            safeRender('Network Availability', () => renderNetworkAvailability(state.summary, state.trends));
            safeRender('Device Status Trend Meta', () => renderDeviceStatusCards(state.summary, ts, state.trends));
        }

        // 4. Top Problems Tables
        if (state.topProblems) {
            safeRender('Top Latency Table', () => renderTopLatencyTable(state.topProblems.high_latency));
            safeRender('Top Packet Loss Table', () => renderTopPacketLossTable(state.topProblems.high_packet_loss));
            if (state.topProblems.recently_down) {
                safeRender('Top Affected Devices', () => renderTopAffectedDevices(state.topProblems.recently_down));
            }
        }

        // 5. Server Health (Table + Meta)
        if (state.serverHealth) {
            safeRender('Server Health Table', () => renderEnhancedServerTable(state.serverHealth));
            safeRender('Server Health Meta', () => renderServerLastCheck(state.serverHealth));
            safeRender('Server Health Summary', () => renderServerHealthSummary(state.serverHealth));
        } else {
            safeRender('Server Health Meta', () => renderServerLastCheck(null));
        }

        // 6. Alert Center (Table)
        if (state.alerts) {
            safeRender('Alert Center', () => renderAlertCenter(state.alerts));
        }

        // 7. Inventory (Table + Chart)
        if (state.inventory) {
            if (isTabVisible('tab-inventory-list')) {
                safeRender('Inventory List', () => renderInventoryTable(state.inventory.devices));
            }
            if (typeof Chart !== 'undefined') {
                safeRender('Inventory Chart', () => renderInventoryChart(state.inventory));
            }
        }

        // 8. Timestamps
        const timeEl = document.getElementById('last-updated-text');
        if (timeEl && state.lastUpdated) timeEl.textContent = state.lastUpdated.toLocaleTimeString();

        const breakdownUpdated = document.getElementById('device-breakdown-updated');
        if (breakdownUpdated && state.lastUpdated) breakdownUpdated.textContent = state.lastUpdated.toLocaleString();

        const alertsUpdated = document.getElementById('alerts-last-updated');
        if (alertsUpdated && state.lastUpdated) alertsUpdated.textContent = state.lastUpdated.toLocaleString();

        // 9. Global Error Clearing
        if (!state.error) {
            const errorEl = document.getElementById('global-error');
            if (errorEl && !errorEl.dataset.hasErrors) {
                errorEl.style.display = 'none';
            }
        }

    } catch (e) {
        console.error("Secondary Render Error", e);
    }
}

function renderAll(state) {
    // This function is kept for backward compatibility if called elsewhere,
    // but internally we now prioritize critical vs secondary.
    renderCritical(state);
    if ('requestIdleCallback' in window) {
        requestIdleCallback(() => renderSecondary(state));
    } else {
        setTimeout(() => renderSecondary(state), 0);
    }
}

function safeRender(name, renderFn) {
    try {
        renderFn();
    } catch (e) {
        console.error(`[Dashboard] Component ${name} failed to render:`, e);
    }
}

function showGlobalError(msg, isSticky = false) {
    const errorEl = document.getElementById('global-error');
    if (errorEl) {
        const textEl = document.getElementById('global-error-text');
        if (textEl) {
            textEl.textContent = msg;
        } else {
            errorEl.textContent = msg;
        }
        errorEl.style.display = 'block';
        if (isSticky) errorEl.dataset.hasErrors = 'true';
    }
}

function setupTabs() {
    const tabs = document.querySelectorAll('.tabs button');
    const contents = document.querySelectorAll('.tab-content');

    // Initialize active tab state
    const activeBtn = document.querySelector('.tabs button.active') || tabs[0];
    contents.forEach(c => {
        c.classList.remove('is-active', 'is-leaving');
        c.style.display = 'none';
    });
    if (activeBtn) {
        const initialId = activeBtn.dataset.target;
        const initialEl = document.getElementById(initialId);
        if (initialEl) {
            initialEl.style.display = 'block';
            requestAnimationFrame(() => initialEl.classList.add('is-active'));
        }
    }

    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            const targetId = e.target.dataset.target;
            const targetEl = document.getElementById(targetId);
            if (!targetEl) return;

            // If already active, ignore
            if (targetEl.classList.contains('is-active')) return;

            const currentEl = document.querySelector('.tab-content.is-active');

            tabs.forEach(t => t.classList.remove('active'));
            e.target.classList.add('active');

            if (currentEl) {
                currentEl.classList.remove('is-active');
                currentEl.classList.add('is-leaving');
                setTimeout(() => {
                    currentEl.style.display = 'none';
                    currentEl.classList.remove('is-leaving');
                }, 220);
            }

            targetEl.style.display = 'block';
            targetEl.classList.remove('is-leaving');
            requestAnimationFrame(() => targetEl.classList.add('is-active'));

            renderAll(getState());
        });
    });
}



let breakdownLiveInFlight = false;
let activeBreakdownCardId = null;
async function triggerLiveBreakdownRefresh() {
    if (breakdownLiveInFlight) return;
    const state = getState();
    const lastUpdate = state.lastUpdated ? new Date(state.lastUpdated).getTime() : 0;
    if (Date.now() - lastUpdate < 15000) {
        refreshAll({ forceFreshTopProblems: true }).catch(() => { });
        return;
    }
    breakdownLiveInFlight = true;
    try {
        await fetch('/api/monitoring/status', { credentials: 'same-origin' });
    } catch (e) {
        console.error('[Dashboard] Live breakdown refresh failed:', e);
    } finally {
        breakdownLiveInFlight = false;
    }
    refreshAll({ forceFreshTopProblems: true }).catch(() => { });
}

function setBreakdownActiveCard(card = null) {
    const cards = document.querySelectorAll('.device-kpi-card');
    cards.forEach(c => c.classList.remove('breakdown-active'));
    activeBreakdownCardId = null;

    if (card && card.id) {
        card.classList.add('breakdown-active');
        activeBreakdownCardId = card.id;
    }
}

function focusBreakdownPanel() {
    const el = document.getElementById('device-breakdown');
    if (!el) return;
    const panel = el.querySelector('.breakdown-panel');

    if (panel) {
        panel.classList.remove('scroll-focus');
        // Force reflow so animation retriggers on repeated opens
        void panel.offsetWidth;
        panel.classList.add('scroll-focus');
        setTimeout(() => panel.classList.remove('scroll-focus'), 800);
    }

    const headerOffset = 88;
    const top = el.getBoundingClientRect().top + window.scrollY - headerOffset;
    window.scrollTo({
        top: Math.max(top, 0),
        behavior: 'smooth'
    });
}

function toggleDeviceBreakdown(sourceCard = null) {
    const el = document.getElementById('device-breakdown');
    if (!el) return;

    const isActive = el.classList.contains('is-active');
    if (!isActive) {
        openDeviceBreakdown(sourceCard);
        return;
    }

    if (sourceCard && sourceCard.id && sourceCard.id !== activeBreakdownCardId) {
        openDeviceBreakdown(sourceCard);
        return;
    }

    closeDeviceBreakdown();
}

function openDeviceBreakdown(sourceCard = null) {
    const el = document.getElementById('device-breakdown');
    if (!el) return;

    const wasActive = el.classList.contains('is-active');
    el.classList.add('is-active');

    if (sourceCard) {
        setBreakdownActiveCard(sourceCard);
    } else if (!activeBreakdownCardId) {
        const defaultCard = document.getElementById('card-devices-online');
        if (defaultCard) setBreakdownActiveCard(defaultCard);
    }

    triggerLiveBreakdownRefresh();
    requestAnimationFrame(() => focusBreakdownPanel());

    // Trigger resize/repaint for charts and table layout
    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
        renderAll(getState());
    }, wasActive ? 120 : 180);
}

function closeDeviceBreakdown() {
    const el = document.getElementById('device-breakdown');
    if (!el) return;
    el.classList.remove('is-active');
    setBreakdownActiveCard(null);
}

function initDeviceBreakdown() {
    // Event Delegation for Device KPI Cards
    document.body.addEventListener('click', (e) => {
        const card = e.target.closest('.device-kpi-card');
        if (card) {
            if (card.id === 'card-devices-maintenance') {
                openMaintenanceModal();
            } else if (card.id === 'card-network-avail') {
                openAvailabilityModal();
            } else {
                toggleDeviceBreakdown(card);
            }
        }

        // Close button delegation
        const closeBtn = e.target.closest('#device-breakdown-close');
        if (closeBtn) {
            closeDeviceBreakdown();
        }

        // Maintenance toggle action in modal
        const maintenanceBtn = e.target.closest('.maintenance-toggle-btn');
        if (maintenanceBtn) {
            e.preventDefault();
            toggleMaintenanceDevice(maintenanceBtn.dataset.deviceId, maintenanceBtn);
        }
    });
}

function getMaintenanceModal() {
    const el = document.getElementById('maintenance-modal');
    if (!el || !window.bootstrap) return null;
    if (!maintenanceModalInstance) {
        maintenanceModalInstance = new window.bootstrap.Modal(el);
    }
    return maintenanceModalInstance;
}

async function openMaintenanceModal() {
    const modal = getMaintenanceModal();
    if (!modal) return;
    await renderMaintenanceList();
    modal.show();
}

async function renderMaintenanceList() {
    const tbody = document.getElementById('maintenance-list-body');
    if (!tbody) return;

    setTableMessageRow(
        tbody,
        5,
        '<i class="fas fa-spinner fa-spin me-2"></i>Loading...',
        'text-center text-secondary py-3'
    );
    try {
        const res = await fetch('/api/maintenance/devices');
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to load maintenance devices');
        }

        const maintenanceDevices = (data.devices || []).filter(d => d.maintenance_mode);
        patchKeyedTableRows(tbody, maintenanceDevices, {
            getKey: (device, index) => device.device_id || device.device_ip || `maintenance-${index}`,
            emptyColSpan: 5,
            emptyMessage: 'No devices in maintenance mode.',
            emptyClassName: 'text-center text-muted py-3',
            renderCells: (device) => `
                <td class="fw-bold text-white">${device.device_name || 'Unknown'}</td>
                <td><code>${device.device_ip || '-'}</code></td>
                <td>${device.device_type || 'Unknown'}</td>
                <td><span class="badge bg-warning text-dark"><i class="fas fa-wrench"></i> Maintenance</span></td>
                <td>
                    <button class="btn btn-sm btn-outline-warning maintenance-toggle-btn" data-device-id="${device.device_id}">
                        Disable
                    </button>
                </td>
            `
        });
    } catch (err) {
        console.error("Failed to load maintenance devices", err);
        setTableMessageRow(tbody, 5, 'Failed to load data.', 'text-center text-danger py-3');
    }
}

async function toggleMaintenanceDevice(deviceId, buttonEl) {
    if (!deviceId) return;
    const original = buttonEl ? buttonEl.innerHTML : '';
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    }
    try {
        const res = await fetch('/api/maintenance/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: Number(deviceId) })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to toggle maintenance');
        }
        await renderMaintenanceList();
    } catch (err) {
        console.error('Failed to toggle maintenance', err);
        alert(err.message || 'Failed to toggle maintenance');
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.innerHTML = original;
        }
    }
}

let availabilityInFlight = false;

function getAvailabilityModal() {
    const el = document.getElementById('availability-modal');
    if (!el || !window.bootstrap) return null;
    if (!availabilityModalInstance) {
        availabilityModalInstance = new window.bootstrap.Modal(el);
    }
    return availabilityModalInstance;
}

async function openAvailabilityModal() {
    const modal = getAvailabilityModal();
    if (!modal) return;
    await renderAvailabilityDetails();
    modal.show();
}

async function renderAvailabilityDetails() {
    if (availabilityInFlight) return;
    availabilityInFlight = true;

    const heatmapEl = document.getElementById('availability-heatmap');
    const updatedEl = document.getElementById('availability-modal-updated');
    const downtimeBody = document.getElementById('availability-downtime-body');
    const worstBody = document.getElementById('availability-worst-body');

    if (heatmapEl) heatmapEl.innerHTML = '<div class="text-secondary">Loading...</div>';
    if (downtimeBody) setTableMessageRow(downtimeBody, 4, 'Loading...', 'text-center text-secondary p-3');
    if (worstBody) setTableMessageRow(worstBody, 4, 'Loading...', 'text-center text-secondary p-3');

    try {
        const data = await fetchAvailabilityDetails(true);
        if (updatedEl) {
            const timestamp = data.generated_at ? new Date(data.generated_at) : new Date();
            updatedEl.textContent = timestamp.toLocaleString();
        }
        renderAvailabilityHeatmap(data.heatmap || [], heatmapEl);
        renderAvailabilityRows(data.downtime_contributors || [], downtimeBody, 'downtime');
        renderAvailabilityRows(data.worst_availability || [], worstBody, 'worst');
    } catch (err) {
        console.error('[Dashboard] Availability detail error:', err);
        if (heatmapEl) heatmapEl.innerHTML = '<div class="text-danger">Failed to load heatmap.</div>';
        if (downtimeBody) setTableMessageRow(downtimeBody, 4, 'Failed to load data.', 'text-center text-danger p-3');
        if (worstBody) setTableMessageRow(worstBody, 4, 'Failed to load data.', 'text-center text-danger p-3');
    } finally {
        availabilityInFlight = false;
    }
}

function renderAvailabilityHeatmap(heatmap, targetEl) {
    const el = targetEl || document.getElementById('availability-heatmap');
    if (!el) return;
    if (!Array.isArray(heatmap) || heatmap.length === 0) {
        el.innerHTML = '<div class="text-secondary">No availability data for the last 24 hours.</div>';
        return;
    }

    const cells = heatmap.map((entry) => {
        const online = entry.online ?? 0;
        const total = entry.total ?? 0;
        const hasData = total > 0;
        const value = hasData ? Number(entry.value ?? 0) : 0;
        const className = hasData ? getAvailabilityClass(value) : 'avail-unknown';
        const timeLabel = formatAvailabilityHour(entry.time);
        const tooltip = hasData
            ? `${timeLabel} • ${formatPercent(value)} (${online}/${total})`
            : `${timeLabel} • No data`;
        return `<div class="availability-cell ${className}" title="${tooltip}"></div>`;
    });

    el.innerHTML = cells.join('');
}

function renderAvailabilityRows(rows, tbody, mode) {
    if (!tbody) return;
    if (!Array.isArray(rows) || rows.length === 0) {
        const emptyMessage = mode === 'worst'
            ? 'No availability records yet.'
            : 'No downtime recorded in the last 24 hours.';
        setTableMessageRow(tbody, 4, emptyMessage, 'text-center text-secondary p-3');
        return;
    }

    if (mode === 'downtime') {
        patchKeyedTableRows(tbody, rows, {
            getKey: (row, index) => row.device_id || row.ip || `downtime-${index}`,
            renderCells: (row) => {
                const name = row.device_name || 'Unknown';
                const ip = row.ip || '-';
                const offline = formatNumber(row.offline_scans ?? 0);
                const downtimePct = formatPercent(row.downtime_pct ?? 0);
                return `
                    <td class="fw-bold text-white">${name}</td>
                    <td><code>${ip}</code></td>
                    <td>${offline}</td>
                    <td>${downtimePct}</td>
                `;
            }
        });
        return;
    }

    patchKeyedTableRows(tbody, rows, {
        getKey: (row, index) => row.device_id || row.ip || `worst-${index}`,
        renderCells: (row) => {
            const name = row.device_name || 'Unknown';
            const ip = row.ip || '-';
            const uptime = formatPercent(row.uptime_pct ?? 0);
            const offline = formatNumber(row.offline_scans ?? 0);
            return `
                <td class="fw-bold text-white">${name}</td>
                <td><code>${ip}</code></td>
                <td>${uptime}</td>
                <td>${offline}</td>
            `;
        }
    });
}

function formatAvailabilityHour(isoString) {
    if (!isoString) return 'Unknown';
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function getAvailabilityClass(value) {
    if (value >= 99) return 'avail-excellent';
    if (value >= 95) return 'avail-good';
    if (value >= 90) return 'avail-warning';
    return 'avail-bad';
}

function initServerKpiInteractions() {
    const cards = document.querySelectorAll('.server-kpi-card');
    const target = document.getElementById('server-health-detail');
    cards.forEach(card => {
        card.addEventListener('click', () => {
            const filter = card.dataset.serverFilter || 'all';
            setServerHealthFilter(filter);
            cards.forEach(c => c.classList.remove('server-filter-active'));
            card.classList.add('server-filter-active');
            const state = getState();
            if (state.serverHealth) {
                renderServerHealthTable(state.serverHealth);
            }
            if (target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
        card.style.cursor = 'pointer';
    });
}

// Batch DOM updates to the next animation frame

function scheduleRender(state) {
    latestState = state;
    if (renderScheduled) return;
    renderScheduled = true;
    requestAnimationFrame(() => {
        renderScheduled = false;
        if (latestState) {
            if (booting) {
                // During boot, only render critical components to avoid thrashing
                renderCritical(latestState);
            } else {
                // Otherwise render everything (split into critical + idle)
                renderAll(latestState);
            }
        }
    });
}

function isTabVisible(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    return window.getComputedStyle(el).display !== 'none';
}


function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeSubnetValue(value) {
    const cleaned = String(value ?? '').trim();
    return cleaned || 'Unassigned';
}

function formatSubnetHealthLabel(healthPct) {
    if (healthPct < 50) return 'Critical';
    if (healthPct < 90) return 'Degraded';
    return 'Healthy';
}

function normalizeDeviceTypeLabel(value) {
    const raw = String(value ?? '').trim();
    if (!raw) return 'Unknown';
    return raw.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatTopBreakdown(items, maxItems = 3) {
    if (!Array.isArray(items) || items.length === 0) return '-';
    return items
        .slice(0, maxItems)
        .map((item) => `${item.name} (${item.count})`)
        .join(', ');
}

function getCachedSubnetDetails(subnetKey) {
    const cached = subnetDetailsCache.get(subnetKey);
    if (!cached) return null;
    if ((Date.now() - cached.at) > SUBNET_DETAILS_CACHE_TTL_MS) {
        subnetDetailsCache.delete(subnetKey);
        return null;
    }
    return cached.data;
}

function setCachedSubnetDetails(subnetKey, data) {
    subnetDetailsCache.set(subnetKey, {
        data,
        at: Date.now()
    });
}

function buildFallbackSubnetDetails(subnetKey, state, subnetRow) {
    const allDevices = Array.isArray(state.inventory?.devices) ? state.inventory.devices : [];
    const devices = allDevices
        .filter((device) => normalizeSubnetValue(device.subnet_cidr) === subnetKey)
        .map((device) => ({
            device_id: device.device_id,
            device_name: device.device_name || device.hostname || 'Unknown',
            hostname: device.hostname,
            device_ip: device.device_ip,
            device_type: normalizeDeviceTypeLabel(device.device_type),
            manufacturer: (device.manufacturer || 'Unknown'),
            is_monitored: !!device.is_monitored,
            status: 'unknown',
            last_seen: null
        }));

    const total = Number(subnetRow?.total) || devices.length || 0;
    const online = Number(subnetRow?.online) || 0;
    const offline = Number(subnetRow?.offline) || Math.max(0, total - online);
    const healthPct = total > 0 ? Math.round((online / total) * 1000) / 10 : 0;
    const monitored = devices.filter((device) => device.is_monitored).length;
    const servers = devices.filter(
        (device) => String(device.device_type || '').toLowerCase() === 'server'
    ).length;

    const typeCounts = new Map();
    const vendorCounts = new Map();
    devices.forEach((device) => {
        typeCounts.set(device.device_type, (typeCounts.get(device.device_type) || 0) + 1);
        vendorCounts.set(device.manufacturer, (vendorCounts.get(device.manufacturer) || 0) + 1);
    });

    const topTypes = Array.from(typeCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([name, count]) => ({ name, count }));
    const topVendors = Array.from(vendorCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([name, count]) => ({ name, count }));

    return {
        generated_at: state.summary?.timestamp || (state.lastUpdated ? new Date(state.lastUpdated).toISOString() : null),
        subnet: subnetKey,
        summary: {
            total,
            online,
            offline,
            health_pct: healthPct,
            monitored,
            servers
        },
        top_types: topTypes,
        top_vendors: topVendors,
        devices
    };
}

function renderSubnetModalDetails(details) {
    const payload = details || {};
    const summary = payload.summary || {};

    const total = Number(summary.total) || 0;
    const online = Number(summary.online) || 0;
    const offline = Number(summary.offline) || Math.max(0, total - online);
    const monitored = Number(summary.monitored) || 0;
    const servers = Number(summary.servers) || 0;
    const healthPctRaw = Number(summary.health_pct);
    const healthPct = Number.isFinite(healthPctRaw)
        ? healthPctRaw
        : (total > 0 ? Math.round((online / total) * 1000) / 10 : 0);
    const healthLabel = formatSubnetHealthLabel(healthPct);
    const subnetLabel = normalizeSubnetValue(payload.subnet);
    const generatedAt = payload.generated_at ? new Date(payload.generated_at).toLocaleString() : '-';

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value ?? '-');
    };

    setText('subnet-modal-title', `Subnet ${subnetLabel}`);
    setText('subnet-modal-updated', `Updated: ${generatedAt}`);
    setText('subnet-modal-total', total);
    setText('subnet-modal-online', online);
    setText('subnet-modal-offline', offline);
    setText('subnet-modal-health', `${healthLabel} (${healthPct}%)`);
    setText('subnet-modal-monitored', monitored);
    setText('subnet-modal-servers', servers);
    setText('subnet-modal-types', formatTopBreakdown(payload.top_types));
    setText('subnet-modal-vendors', formatTopBreakdown(payload.top_vendors));

    const tbody = document.getElementById('subnet-modal-devices-body');
    if (!tbody) return;

    patchKeyedTableRows(tbody, Array.isArray(payload.devices) ? payload.devices : [], {
        getKey: (device, index) => device.device_id || device.device_ip || `subnet-device-${index}`,
        emptyColSpan: 5,
        emptyMessage: 'No devices mapped to this subnet yet.',
        emptyClassName: 'text-center text-secondary p-3',
        renderCells: (device) => {
            const name = escapeHtml(device.device_name || device.hostname || 'Unknown');
            const ip = escapeHtml(device.device_ip || '-');
            const type = escapeHtml(normalizeDeviceTypeLabel(device.device_type));
            const vendor = escapeHtml(device.manufacturer || 'Unknown');
            const monitoredText = device.is_monitored ? 'Yes' : 'No';
            const statusRaw = String(device.status || 'unknown').toLowerCase();
            const statusLabel = statusRaw.charAt(0).toUpperCase() + statusRaw.slice(1);
            const statusClass = statusRaw === 'online'
                ? 'text-success'
                : (statusRaw === 'offline' ? 'text-danger' : 'text-secondary');
            const lastSeen = device.last_seen ? timeAgo(device.last_seen) : '-';
            return `
                <td>
                    <div class="fw-bold">${name}</div>
                    <div class="small ${statusClass}">${statusLabel} • Last seen: ${lastSeen}</div>
                </td>
                <td><code>${ip}</code></td>
                <td>${type}</td>
                <td>${vendor}</td>
                <td>${monitoredText}</td>
            `;
        }
    });
}

function getSubnetModal() {
    const el = document.getElementById('subnetDetailsModal');
    if (!el || !window.bootstrap) return null;
    if (!subnetModalInstance) {
        subnetModalInstance = new window.bootstrap.Modal(el);
    }
    return subnetModalInstance;
}

function initSubnetInteractions() {
    document.body.addEventListener('click', (event) => {
        const row = event.target.closest('#subnet-health-body tr[data-subnet-cidr]');
        if (!row) return;
        openSubnetDetails(row.dataset.subnetCidr);
    });

    document.body.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const row = event.target.closest('#subnet-health-body tr[data-subnet-cidr]');
        if (!row) return;
        event.preventDefault();
        openSubnetDetails(row.dataset.subnetCidr);
    });
}

async function openSubnetDetails(subnetCidr) {
    const modal = getSubnetModal();
    if (!modal) return;

    const subnetKey = normalizeSubnetValue(subnetCidr);
    const state = getState();
    const subnetRow = latestSubnetHealthRows.find(
        (row) => normalizeSubnetValue(row.subnet) === subnetKey
    ) || null;

    const fallback = buildFallbackSubnetDetails(subnetKey, state, subnetRow);
    renderSubnetModalDetails(fallback);
    modal.show();

    const requestSeq = ++subnetDetailsRequestSeq;
    try {
        const cached = getCachedSubnetDetails(subnetKey);
        const payload = cached || await fetchSubnetDetails(subnetKey, 800);
        if (!cached) setCachedSubnetDetails(subnetKey, payload);
        if (requestSeq !== subnetDetailsRequestSeq) return;
        renderSubnetModalDetails(payload);
    } catch (error) {
        if (requestSeq !== subnetDetailsRequestSeq) return;
        console.warn('[Dashboard] Failed to fetch subnet details, using fallback data:', error);
    }
}

function renderSubnetHealth(subnetHealth) {
    const tbody = document.getElementById('subnet-health-body');
    if (!tbody) return;

    latestSubnetHealthRows = Array.isArray(subnetHealth) ? subnetHealth : [];

    patchKeyedTableRows(tbody, latestSubnetHealthRows, {
        getKey: (subnet, index) => normalizeSubnetValue(subnet.subnet) || `subnet-${index}`,
        emptyColSpan: 5,
        emptyMessage: 'No subnet data available.',
        emptyClassName: 'text-center text-secondary p-3',
        renderCells: (subnet) => {
            const subnetLabel = escapeHtml(normalizeSubnetValue(subnet.subnet));
            const total = Number(subnet.total) || 0;
            const online = Number(subnet.online) || 0;
            const offline = Number(subnet.offline) || Math.max(0, total - online);
            const healthPct = total > 0 ? Math.round((online / total) * 100) : 0;
            const label = formatSubnetHealthLabel(healthPct);

            let badgeClass = 'tactical-badge tactical-badge-success subnet-health-badge';
            if (healthPct < 50) {
                badgeClass = 'tactical-badge tactical-badge-danger subnet-health-badge';
            } else if (healthPct < 90) {
                badgeClass = 'tactical-badge tactical-badge-warning subnet-health-badge';
            }

            return `
                <td class="fw-bold"><code>${subnetLabel}</code><span class="subnet-row-hint">Details</span></td>
                <td>${total}</td>
                <td class="text-success fw-bold">${online}</td>
                <td class="${offline > 0 ? 'text-danger fw-bold' : ''}">${offline}</td>
                <td><span class="badge ${badgeClass}">${label} (${healthPct}%)</span></td>
            `;
        },
        applyRow: (row, subnet) => {
            const subnetValue = normalizeSubnetValue(subnet.subnet);
            row.classList.add('subnet-row-clickable');
            row.dataset.subnetCidr = subnetValue;
            row.setAttribute('role', 'button');
            row.setAttribute('tabindex', '0');
            row.setAttribute('aria-label', `Open subnet details for ${subnetValue}`);
        }
    });
}

function renderOverallHealth(state) {
    const card = document.getElementById('overall-health-card');
    const statusEl = document.getElementById('overall-health-status');
    const subEl = document.getElementById('overall-health-subtext');
    if (!card || !statusEl) return;

    const summary = state.summary;
    const serverHealth = state.serverHealth;
    const alerts = state.alerts;

    if (!summary) {
        statusEl.textContent = 'Loading...';
        card.classList.remove('health-healthy', 'health-degraded', 'health-critical');
        card.classList.add('health-degraded');
        return;
    }

    const networkState = computeNetworkHealthState(summary, alerts);
    const serverState = computeServerHealthState(serverHealth);
    const alertCounts = getAlertCounts(alerts, summary);

    const overall = computeOverallState(networkState, serverState, alertCounts);

    card.classList.remove('health-healthy', 'health-degraded', 'health-critical');
    card.classList.add(`health-${overall.toLowerCase()}`);
    statusEl.textContent = overall;
    if (subEl) subEl.textContent = 'Based on network health, server telemetry, and critical alerts.';
}

function renderServerLastCheck(serverHealth) {
    const el = document.getElementById('server-last-check');
    if (!el) return;
    if (!serverHealth || !serverHealth.timestamp) {
        el.textContent = '-';
        return;
    }
    el.textContent = timeAgo(serverHealth.timestamp);
}

function computeNetworkHealthState(summary, alerts) {
    const devices = summary?.devices || {};
    const net = summary?.network_health || {};

    const total = devices.total ?? 0;
    const offline = devices.offline ?? devices.down ?? 0;
    // Maintenance devices are excluded from "offline" count in the backend usually, but let's be safe
    // If backend returns them as "maintenance", they might not be in total/offline/online counts depending on aggregation.
    // Assuming "degraded" might capturing them or they are separate. 

    // We treat Maintenance as "Healthy" or "Ignored" for Health State calculation.

    const degraded = devices.degraded ?? 0;

    // Filter out maintenance from total for percentage calc if needed, but usually we just ignore them in "Offline" check.

    const offlinePct = total > 0 ? (offline / total) * 100 : 0;
    const degradedPct = total > 0 ? (degraded / total) * 100 : 0;

    const latency = net.avg_latency_ms ?? 0;
    const loss = net.avg_packet_loss_pct ?? net.packet_loss ?? 0;

    const alertCounts = getAlertCounts(alerts, summary);
    const networkCritical = alertCounts.networkCritical + alertCounts.deviceCritical;
    const networkWarning = alertCounts.networkWarning + alertCounts.deviceWarning;

    if (offlinePct >= 10 || latency >= 300 || loss >= 10 || networkCritical >= 1) return 'Critical';
    if (offlinePct >= 3 || degradedPct >= 10 || latency >= 150 || loss >= 5 || networkWarning >= 1) return 'Degraded';
    return 'Healthy';
}

function computeServerHealthState(serverHealth) {
    const counts = serverHealth?.counts;
    if (!counts) return 'Degraded';

    const total = counts.total ?? 0;
    if (total <= 0) return 'Degraded';

    const critical = counts.critical ?? 0;
    const warning = counts.warning ?? 0;
    const offline = counts.offline ?? 0;

    const criticalPct = (critical / total) * 100;
    const offlinePct = (offline / total) * 100;
    const warningPct = (warning / total) * 100;

    if (criticalPct >= 5 || offlinePct >= 5) return 'Critical';
    if (warningPct >= 10) return 'Degraded';
    return 'Healthy';
}

function computeOverallState(networkState, serverState, alertCounts) {
    const stateScore = {
        Healthy: 100,
        Degraded: 60,
        Critical: 20
    };

    const criticalAlerts = alertCounts.critical;
    const warningAlerts = alertCounts.warning;

    const alertScore = criticalAlerts > 0 ? 30 : (warningAlerts > 0 ? 70 : 100);

    const networkScore = stateScore[networkState] ?? 60;
    const serverScore = stateScore[serverState] ?? 60;

    if (criticalAlerts >= 3 && (networkState !== 'Healthy' || serverState !== 'Healthy')) {
        return 'Critical';
    }

    const overallScore = 0.45 * networkScore + 0.35 * serverScore + 0.20 * alertScore;
    if (overallScore >= 80) return 'Healthy';
    if (overallScore >= 50) return 'Degraded';
    return 'Critical';
}

function getAlertCounts(alerts, summary) {
    const counts = {
        critical: 0,
        warning: 0,
        info: 0,
        networkCritical: 0,
        networkWarning: 0,
        deviceCritical: 0,
        deviceWarning: 0,
        serverCritical: 0,
        serverWarning: 0
    };

    if (Array.isArray(alerts) && alerts.length > 0) {
        alerts.forEach(a => {
            const sev = (a.severity || '').toUpperCase();
            const scope = (a.scope || '').toLowerCase();
            const isCritical = sev === 'CRITICAL';
            const isWarning = sev === 'WARNING';

            if (isCritical) counts.critical += 1;
            else if (isWarning) counts.warning += 1;
            else counts.info += 1;

            if (scope === 'network') {
                if (isCritical) counts.networkCritical += 1;
                if (isWarning) counts.networkWarning += 1;
            } else if (scope === 'server') {
                if (isCritical) counts.serverCritical += 1;
                if (isWarning) counts.serverWarning += 1;
            } else {
                if (isCritical) counts.deviceCritical += 1;
                if (isWarning) counts.deviceWarning += 1;
            }
        });
        return counts;
    }

    const summaryAlerts = summary?.active_alerts;
    if (summaryAlerts) {
        counts.critical = summaryAlerts.critical ?? 0;
        counts.warning = summaryAlerts.warning ?? 0;
        counts.info = summaryAlerts.info ?? 0;
    }
    return counts;
}

// Refresh when page is restored from bfcache
window.addEventListener('pageshow', (evt) => {
    if (evt.persisted) {
        refreshAll().catch(() => { });
        if (!isSSEConnected) startPolling('pageshow');
    }
});


// Smart Visibility Handling
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        console.log('[Dashboard] Tab became visible');
        // If the data is older than 30 seconds, refresh immediately
        const state = getState();
        const lastUpdate = state.lastUpdated ? new Date(state.lastUpdated).getTime() : 0;
        const now = Date.now();

        if (now - lastUpdate > 30000) {
            console.log('[Dashboard] Data stale (>30s), triggering refresh...');
            refreshAll().catch(e => console.error(e));
        } else {
            console.log('[Dashboard] Data fresh enough, skipping immediate refresh.');
        }

        if (!isSSEConnected) {
            startPolling('tab-visible');
        } else {
            stopPolling();
        }
    } else {
        stopPolling();
    }
});
