/**
 * Dashboard Orchestrator
 */
import { fetchSummary, fetchTopProblems, fetchTrends, fetchInventory, fetchServerHealth, fetchAlerts } from './api.js';
import { updateState, getState, subscribe, mergeRealtimeUpdate, loadFromCache } from './state.js';
import { renderDevicesOnline } from './cards/devicesOnline.js';
import { renderDeviceStatusCards } from './cards/deviceStatus.js';
import { renderNetworkAvailability } from './cards/networkAvailability.js';
import { renderTopLatencyTable, renderTopPacketLossTable, renderTopAffectedDevices } from './tables/topProblems.js';
import { renderInventoryTable, initInventoryInteractions } from './tables/inventoryTable.js';
import { renderInventoryChart } from './charts/inventoryChart.js';
import { initDiscovery } from './discovery.js';
import { initServerModal } from './modals/serverDetailModal.js';
import { renderServerHealthSummary, renderServerHealthTable, initServerHealthTable } from './servers/serverHealth.js';
import { initAlertCenter, renderAlertCenter } from './alerts/alertCenter.js';
import { initSSE, getConnectionStatus, ConnectionStatus } from './sseClient.js';
import { renderConnectionIndicator, initConnectionIndicator } from './connectionIndicator.js';

console.log("[Dashboard] Module loading...");

// Prevent double-init (e.g., back/forward cache)
const dashboardBootKey = '__dashboardBooted';

// Polling fallback state
let pollingInterval = null;
const POLLING_INTERVAL_MS = 30000;

// Batch DOM updates to the next animation frame
let renderScheduled = false;
let latestState = null;

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
        return;
    }
    window[dashboardBootKey] = true;

    console.log('[Dashboard] Initializing...');
    const errorEl = document.getElementById('global-error');

    try {
        // 1. Initialize connection indicator
        console.log('[Dashboard] Setting up connection indicator...');
        initConnectionIndicator();

        // 2. Initialize Discovery UI
        initDiscovery();

        // 3. Try to load from cache for instant render
        if (loadFromCache()) {
            console.log('[Dashboard] Loaded cache, fast rendering...');
            scheduleRender(getState());
        }

        // 4. Initial Fetch (Stale-while-revalidate)
        console.log('[Dashboard] Starting initial fetch (background)...');
        refreshAll().catch(err => {
            console.error('[Dashboard] Async fetch error:', err);
            showGlobalError(`Fetch Error: ${err.message}`);
        });

        // 4. Initialize SSE for real-time updates (DELAYED to allow initial fetch to complete)
        console.log('[Dashboard] Scheduling SSE initialization (3s delay)...');
        setTimeout(() => {
            console.log('[Dashboard] Initializing SSE...');
            initSSE({
                onDeviceStatus: (data) => {
                    console.log('[Dashboard] Device status event:', data);
                    mergeRealtimeUpdate('device_status', data);
                },
                onAlertCreated: (data) => {
                    console.log('[Dashboard] Alert created event:', data);
                    mergeRealtimeUpdate('alert_created', data);
                },
                onLatencySpike: (data) => {
                    console.log('[Dashboard] Latency spike event:', data);
                    mergeRealtimeUpdate('latency_spike', data);
                },
                onInterfaceThreshold: (data) => {
                    console.log("Interface Threshold:", data);
                },
                onClassificationUpdate: (data) => {
                    console.log("Device Classified:", data);
                    const row = document.querySelector(`tr[data-ip="${data.ip_address}"]`);
                    if (row) {
                        row.classList.add('highlight-update');
                        setTimeout(() => row.classList.remove('highlight-update'), 2000);
                        const typeCell = row.querySelector('.device-type-cell');
                        if (typeCell && data.classification) typeCell.textContent = data.classification.device_type;
                    }
                },
                onConnectionChange: (status) => {
                    console.log('[Dashboard] Connection status changed:', status);
                    updateState('connectionStatus', status);
                    renderConnectionIndicator(status);
                    if (status === ConnectionStatus.DISCONNECTED) startPollingFallback();
                    else if (status === ConnectionStatus.CONNECTED) stopPollingFallback();
                }
            });
        }, 3000);

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
        const timeRangeEl = document.getElementById('time-range');
        if (timeRangeEl) {
            const savedRange = localStorage.getItem('tactical_dashboard_range') || '24h';
            timeRangeEl.value = savedRange;
            timeRangeEl.addEventListener('change', (e) => {
                const newValue = e.target.value;
                localStorage.setItem('tactical_dashboard_range', newValue);
                refreshAll();
            });
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

        // 9. Start Live Clock
        startClock();

        console.log('[Dashboard] Initialization sequence complete.');

    } catch (err) {
        console.error('[Dashboard] CRITICAL INIT ERROR:', err);
        showGlobalError(`Initialization Failed: ${err.message}`, true);
    }
}

/**
 * Start polling fallback when SSE is disconnected.
 */
function startPollingFallback() {
    if (pollingInterval) return; // Already polling
    console.log('[Dashboard] Starting polling fallback (30s interval)');
    pollingInterval = setInterval(refreshAll, POLLING_INTERVAL_MS);
}

/**
 * Stop polling fallback when SSE reconnects.
 */
function stopPollingFallback() {
    if (!pollingInterval) return;
    console.log('[Dashboard] Stopping polling fallback (SSE active)');
    clearInterval(pollingInterval);
    pollingInterval = null;
}

async function refreshAll() {
    console.log('[Dashboard] Refreshing data...');
    updateState('isLoading', true);

    const timeRange = document.getElementById('time-range')?.value || '24h';
    console.log(`[Dashboard] Refreshing with range: ${timeRange}`);

    // Helper for timeout
    const fetchWithTimeout = (p, ms = 15000) => Promise.race([
        p,
        new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), ms))
    ]);

    // 1. Fetch Summary
    fetchWithTimeout(fetchSummary()).then(summary => {
        updateState('summary', summary);
    }).catch(err => {
        showGlobalError(`Summary Sync Failed: ${err.message}`);
    });

    // 2. Fetch Top Problems
    fetchWithTimeout(fetchTopProblems()).then(topProblems => {
        updateState('topProblems', topProblems);
    }).catch(err => {
        console.error('[Dashboard] Top Problems fetch failed:', err);
    });

    // 3. Fetch Trends
    fetchWithTimeout(fetchTrends(timeRange)).then(trends => {
        console.log('[Dashboard] Trends received:', trends);
        updateState('trends', trends);
    }).catch(err => {
        console.error('[Dashboard] Trends fetch failed:', err);
    });

    // 4. Fetch Inventory
    fetchWithTimeout(fetchInventory()).then(inventory => {
        updateState('inventory', inventory);
    }).catch(err => {
        console.error('[Dashboard] Inventory fetch failed:', err);
    });

    // 5. Fetch Server Health Summary
    fetchWithTimeout(fetchServerHealth()).then(serverHealth => {
        updateState('serverHealth', serverHealth);
    }).catch(err => {
        console.error('[Dashboard] Server health fetch failed:', err);
    });

    // 6. Fetch Alerts (active)
    fetchWithTimeout(fetchAlerts('active', 200)).then(alerts => {
        updateState('alerts', alerts);
    }).catch(err => {
        console.error('[Dashboard] Alerts fetch failed:', err);
    });

    // Clean up loading state eventually
    setTimeout(() => updateState('isLoading', false), 2000);
}

function renderAll(state) {
    if (state.error) return;

    try {
        const ts = state.lastUpdated ? state.lastUpdated.toISOString() : null;

        if (state.summary) {
            safeRender('Devices Online', () => renderDevicesOnline(state.summary, ts));
            safeRender('Device Status Cards', () => renderDeviceStatusCards(state.summary, ts));
            safeRender('Network Availability', () => renderNetworkAvailability(state.summary, state.trends));
        }

        if (state.topProblems) {
            safeRender('Top Latency Table', () => renderTopLatencyTable(state.topProblems.high_latency));
            safeRender('Top Packet Loss Table', () => renderTopPacketLossTable(state.topProblems.high_packet_loss));
            if (state.topProblems.recently_down) {
                safeRender('Top Affected Devices', () => renderTopAffectedDevices(state.topProblems.recently_down));
            }
        }

        if (state.serverHealth) {
            safeRender('Server Health Summary', () => renderServerHealthSummary(state.serverHealth));
            safeRender('Server Health Table', () => renderServerHealthTable(state.serverHealth));
        }

        if (state.alerts) {
            safeRender('Alert Center', () => renderAlertCenter(state.alerts));
        }

        // Render Inventory List
        if (state.inventory) {
            if (isTabVisible('tab-inventory-list')) {
                safeRender('Inventory List', () => renderInventoryTable(state.inventory.devices));
            }
            if (typeof Chart !== 'undefined') {
                safeRender('Inventory Chart', () => renderInventoryChart(state.inventory));
            }
        }

        // Update Last Updated Text
        const timeEl = document.getElementById('last-updated-text');
        if (timeEl && state.lastUpdated) {
            timeEl.textContent = state.lastUpdated.toLocaleTimeString();
        }
        const breakdownUpdated = document.getElementById('device-breakdown-updated');
        if (breakdownUpdated && state.lastUpdated) {
            breakdownUpdated.textContent = state.lastUpdated.toLocaleString();
        }
        const alertsUpdated = document.getElementById('alerts-last-updated');
        if (alertsUpdated && state.lastUpdated) {
            alertsUpdated.textContent = state.lastUpdated.toLocaleString();
        }

        if (!state.error) {
            const errorEl = document.getElementById('global-error');
            if (errorEl && !errorEl.dataset.hasErrors) {
                errorEl.style.display = 'none';
            }
        }
    } catch (e) {
        console.error("Critical Render Error", e);
        showGlobalError(`Rendering Failed: ${e.message}`);
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
        errorEl.textContent = msg;
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

function openDeviceBreakdown() {
    const el = document.getElementById('device-breakdown');
    if (!el) return;
    el.style.display = 'block';
    el.classList.add('is-active');
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
    }, 150);
}

function closeDeviceBreakdown() {
    const el = document.getElementById('device-breakdown');
    if (!el) return;
    el.style.display = 'none';
    el.classList.remove('is-active');
}

function initDeviceBreakdown() {
    const cards = document.querySelectorAll('.device-kpi-card');
    cards.forEach(card => {
        card.addEventListener('click', () => openDeviceBreakdown());
        card.style.cursor = 'pointer';
    });
    const closeBtn = document.getElementById('device-breakdown-close');
    if (closeBtn) closeBtn.addEventListener('click', closeDeviceBreakdown);
}

function initServerKpiInteractions() {
    const cards = document.querySelectorAll('.server-kpi-card');
    const target = document.getElementById('server-health-detail');
    cards.forEach(card => {
        card.addEventListener('click', () => {
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
        if (latestState) renderAll(latestState);
    });
}

function isTabVisible(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    return window.getComputedStyle(el).display !== 'none';
}

// Refresh when page is restored from bfcache
window.addEventListener('pageshow', (evt) => {
    if (evt.persisted) {
        refreshAll().catch(() => { });
    }
});

function startClock() {
    const timeEl = document.getElementById('clock-time');
    const dateEl = document.getElementById('clock-date');
    if (!timeEl || !dateEl) return;

    function update() {
        const now = new Date();
        timeEl.textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        dateEl.textContent = now.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
    }
    update();
    setInterval(update, 1000);
}

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

        // Resume polling if disconnected
        if (state.connectionStatus === 'disconnected') {
            startPollingFallback();
        }
    } else {
        // Tab hidden: We could pause polling here if we wanted to be very efficient
        // But for now, we leave SSE open. Polling fallback (if active) continues.
    }
});
