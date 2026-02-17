/**
 * Dashboard Orchestrator
 */
import { fetchSummary, fetchTopProblems, fetchTrends, fetchInventory, fetchServerHealth, fetchAlerts, fetchFleetMetrics, fetchAvailabilityDetails } from './api.js';
import { updateState, getState, subscribe, loadFromCache } from './state.js';
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

console.log("[Dashboard] Module loading...");

// Prevent double-init (e.g., back/forward cache)
const dashboardBootKey = '__dashboardBooted';

// Polling state
let pollingInterval = null;
const POLLING_INTERVAL_MS = 30000;

// Batch DOM updates to the next animation frame
// Batch DOM updates to the next animation frame
let renderScheduled = false;
let latestState = null;
let booting = true;
let maintenanceModalInstance = null;
let availabilityModalInstance = null;

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
        startPolling();
        return;
    }
    window[dashboardBootKey] = true;

    console.log('[Dashboard] Initializing...');
    const errorEl = document.getElementById('global-error');

    try {
        // 1. Initialize connection indicator (polling mode)
        console.log('[Dashboard] Setting up connection indicator (polling)...');
        initConnectionIndicator();

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
            refreshAll().catch(err => {
                console.error('[Dashboard] Async fetch error:', err);
                showGlobalError(`Fetch Error: ${err.message}`);
            });
        });

        // 5. Start polling loop (30s)
        startPolling();

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
function startPolling() {
    if (pollingInterval) return;
    console.log('[Dashboard] Starting polling (30s interval)');
    pollingInterval = setInterval(refreshAll, POLLING_INTERVAL_MS);
}

async function refreshAll(options = {}) {
    if (window.cleanupBootstrapModal && !document.querySelector('.modal.show')) {
        window.cleanupBootstrapModal();
    }
    if (document.querySelector('.modal.show')) {
        console.log('[Dashboard] Modal open; skipping refresh');
        return;
    }
    console.log('[Dashboard] Refreshing data...');
    updateState('isLoading', true);

    const { forceFreshTopProblems = false } = options;
    const timeRange = document.querySelector('#time-range-container .dropdown-toggle')?.dataset.value || '24h';

    // Helper for timeout
    const fetchWithTimeout = (p, ms = 15000) => Promise.race([
        p,
        new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), ms))
    ]);

    // 1. CRITICAL DATA (Summary + Fleet)
    // These are needed for the top-fold KPIs.
    const criticalPromises = [
        fetchWithTimeout(fetchSummary()).then(summary => updateState('summary', summary)),
        fetchWithTimeout(fetchFleetMetrics()).then(fleetMetrics => updateState('fleetMetrics', fleetMetrics))
    ];

    // Wait for critical data
    await Promise.allSettled(criticalPromises);

    // Initial critical render is done via updateState -> subscribe -> scheduleRender

    // Stop loading indicator immediately after critical data
    updateState('isLoading', false);

    // If this was the boot sequence, we can now allow full rendering
    if (booting) {
        setTimeout(() => {
            booting = false;
            // Force a full re-render state check
            scheduleRender(getState());
        }, 100);
    }

    // 2. SECONDARY DATA (De-prioritized)
    // Fetched in idle time so we don't block the UI thread
    const fetchSecondary = () => {
        // Top Problems
        fetchWithTimeout(fetchTopProblems(forceFreshTopProblems)).then(topProblems => updateState('topProblems', topProblems)).catch(console.error);
        // Trends
        fetchWithTimeout(fetchTrends(timeRange)).then(trends => updateState('trends', trends)).catch(console.error);
        // Inventory
        fetchWithTimeout(fetchInventory()).then(inventory => updateState('inventory', inventory)).catch(console.error);
        // Server Health
        fetchWithTimeout(fetchServerHealth()).then(serverHealth => updateState('serverHealth', serverHealth)).catch(console.error);
        // Alerts
        fetchWithTimeout(fetchAlerts('active', 200)).then(alerts => updateState('alerts', alerts)).catch(console.error);
    };

    if ('requestIdleCallback' in window) {
        requestIdleCallback(fetchSecondary);
    } else {
        setTimeout(fetchSecondary, 50);
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

    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary py-3"><i class="fas fa-spinner fa-spin me-2"></i>Loading...</td></tr>';
    try {
        const res = await fetch('/api/maintenance/devices');
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to load maintenance devices');
        }

        const maintenanceDevices = (data.devices || []).filter(d => d.maintenance_mode);
        if (maintenanceDevices.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No devices in maintenance mode.</td></tr>';
            return;
        }

        tbody.innerHTML = maintenanceDevices.map(d => `
            <tr>
                <td class="fw-bold text-white">${d.device_name || 'Unknown'}</td>
                <td><code>${d.device_ip || '-'}</code></td>
                <td>${d.device_type || 'Unknown'}</td>
                <td><span class="badge bg-warning text-dark"><i class="fas fa-wrench"></i> Maintenance</span></td>
                <td>
                    <button class="btn btn-sm btn-outline-warning maintenance-toggle-btn" data-device-id="${d.device_id}">
                        Disable
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error("Failed to load maintenance devices", err);
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger py-3">Failed to load data.</td></tr>';
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
    if (downtimeBody) downtimeBody.innerHTML = '<tr><td colspan="4" class="text-center text-secondary p-3">Loading...</td></tr>';
    if (worstBody) worstBody.innerHTML = '<tr><td colspan="4" class="text-center text-secondary p-3">Loading...</td></tr>';

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
        if (downtimeBody) downtimeBody.innerHTML = '<tr><td colspan="4" class="text-center text-danger p-3">Failed to load data.</td></tr>';
        if (worstBody) worstBody.innerHTML = '<tr><td colspan="4" class="text-center text-danger p-3">Failed to load data.</td></tr>';
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
        tbody.innerHTML = `<tr><td colspan="4" class="text-center text-secondary p-3">${emptyMessage}</td></tr>`;
        return;
    }

    if (mode === 'downtime') {
        tbody.innerHTML = rows.map((row) => {
            const name = row.device_name || 'Unknown';
            const ip = row.ip || '-';
            const offline = formatNumber(row.offline_scans ?? 0);
            const downtimePct = formatPercent(row.downtime_pct ?? 0);
            return `
                <tr>
                    <td class="fw-bold text-white">${name}</td>
                    <td><code>${ip}</code></td>
                    <td>${offline}</td>
                    <td>${downtimePct}</td>
                </tr>
            `;
        }).join('');
        return;
    }

    tbody.innerHTML = rows.map((row) => {
        const name = row.device_name || 'Unknown';
        const ip = row.ip || '-';
        const uptime = formatPercent(row.uptime_pct ?? 0);
        const offline = formatNumber(row.offline_scans ?? 0);
        return `
            <tr>
                <td class="fw-bold text-white">${name}</td>
                <td><code>${ip}</code></td>
                <td>${uptime}</td>
                <td>${offline}</td>
            </tr>
        `;
    }).join('');
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

        startPolling();
    } else {
        // Tab hidden: keep polling; no SSE used
    }
});
