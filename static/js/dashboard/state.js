/**
 * Dashboard State Management
 */

const dashboardState = {
    summary: null,
    trends: null,
    topProblems: null,
    inventory: null,
    alerts: null,
    lastUpdated: null,
    isLoading: false,
    error: null,
    // Real-time connection state
    connectionStatus: 'polling',
    lastEventId: null,
    realtimeEvents: []  // Buffer for real-time events (max 50)
    ,
    // Real-time metrics data
    realtimeInterfaces: null,
    networkIOTrend: null,
    serverHealth: null,
    fleetMetrics: null
};

const listeners = [];
const STORAGE_KEY = 'tactical_dashboard_state';
const CACHE_EXPIRY_MS = 1000 * 60 * 60; // 1 hour

let _saveCacheTimer = null;
function scheduleSave() {
    if (_saveCacheTimer) clearTimeout(_saveCacheTimer);
    _saveCacheTimer = window.setTimeout(saveToCache, 2000);
}

export function getState() {
    return { ...dashboardState };
}

export function updateState(key, data) {
    if (key in dashboardState) {
        dashboardState[key] = data;
        dashboardState.lastUpdated = new Date();
        notifyListeners();
        scheduleSave();
    } else {
        console.warn(`Attempted to update invalid state key: ${key}`);
    }
}

export function updateStateBatch(updates) {
    if (!updates || typeof updates !== 'object') return;

    let changed = false;
    Object.entries(updates).forEach(([key, value]) => {
        if (key in dashboardState) {
            dashboardState[key] = value;
            changed = true;
        }
    });

    if (!changed) return;
    dashboardState.lastUpdated = new Date();
    notifyListeners();
    scheduleSave();
}

function saveToCache() {
    try {
        const cacheData = {
            timestamp: Date.now(),
            activeRange: localStorage.getItem('tactical_dashboard_range') || '24h',
            data: {
                summary: dashboardState.summary,
                trends: dashboardState.trends,
                topProblems: dashboardState.topProblems,
                inventory: dashboardState.inventory,
                alerts: dashboardState.alerts,
                realtimeInterfaces: dashboardState.realtimeInterfaces,
                networkIOTrend: dashboardState.networkIOTrend,
                serverHealth: dashboardState.serverHealth,
                fleetMetrics: dashboardState.fleetMetrics
            }
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(cacheData));
    } catch (e) { console.warn('Cache save failed', e); }
}

export function loadFromCache() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return false;
        const cache = JSON.parse(raw);
        // Allow stale data to render while revalidating (stale-while-revalidate)
        // We still check for very old garbage (e.g. > 7 days) but 1 hour is too strict for UI
        if (Date.now() - cache.timestamp > (1000 * 60 * 60 * 24 * 7)) return false;
        // Discard cache if it was saved for a different time range
        const currentRange = localStorage.getItem('tactical_dashboard_range') || '24h';
        if (cache.activeRange && cache.activeRange !== currentRange) return false;

        const data = cache.data;
        if (data.summary) dashboardState.summary = data.summary;
        if (data.trends) dashboardState.trends = data.trends;
        if (data.topProblems) dashboardState.topProblems = data.topProblems;
        if (data.inventory) dashboardState.inventory = data.inventory;
        if (data.alerts) dashboardState.alerts = data.alerts;
        if (data.realtimeInterfaces) dashboardState.realtimeInterfaces = data.realtimeInterfaces;
        if (data.networkIOTrend) dashboardState.networkIOTrend = data.networkIOTrend;
        if (data.serverHealth) dashboardState.serverHealth = data.serverHealth;
        if (data.fleetMetrics) dashboardState.fleetMetrics = data.fleetMetrics;

        dashboardState.lastUpdated = new Date(cache.timestamp);
        console.log('[State] Hydrated from cache');
        return true;
    } catch (e) { console.error('Cache load failed', e); return false; }
}

export function subscribe(listener) {
    listeners.push(listener);
    return () => {
        const index = listeners.indexOf(listener);
        if (index > -1) {
            listeners.splice(index, 1);
        }
    };
}

function notifyListeners() {
    listeners.forEach(listener => listener(dashboardState));
}

/**
 * Merge a real-time event into the current state.
 * Updates relevant parts of the state without requiring a full refresh.
 * 
 * @param {string} eventType - Type of event (device_status, alert_created, etc.)
 * @param {Object} payload - Event payload
 */
export function mergeRealtimeUpdate(eventType, payload) {
    console.log(`[State] Merging real-time update: ${eventType}`, payload);

    // Add to recent events buffer
    dashboardState.realtimeEvents.unshift({
        type: eventType,
        payload: payload,
        timestamp: new Date()
    });

    // Keep only last 50 events
    if (dashboardState.realtimeEvents.length > 50) {
        dashboardState.realtimeEvents.pop();
    }
    dashboardState.summary = { ...dashboardState.summary };
    dashboardState.topProblems = { ...dashboardState.topProblems };
    dashboardState.alerts = { ...dashboardState.alerts };
    dashboardState.lastUpdated = new Date();
    // Update summary based on event type
    if (dashboardState.summary) {
        switch (eventType) {
            case 'device_status':
            case 'device_update':
                // Update device counts based on status change
                if (dashboardState.summary.devices) {
                    const devices = dashboardState.summary.devices;
                    const nextState = (payload.new_state || payload.status || '').toString().toLowerCase();
                    const isDown = nextState === 'critical' || nextState === 'down' || nextState === 'offline';
                    const isUp = nextState === 'ok' || nextState === 'up' || nextState === 'online';

                    if (isDown) {
                        // Device went down
                        if (devices.offline != null) devices.offline = (devices.offline || 0) + 1;
                        if (devices.down != null) devices.down = (devices.down || 0) + 1;
                        if (devices.online != null) devices.online = Math.max(0, (devices.online || 0) - 1);
                        if (devices.up != null) devices.up = Math.max(0, (devices.up || 0) - 1);
                    } else if (isUp) {
                        // Device came back up
                        if (devices.online != null) devices.online = (devices.online || 0) + 1;
                        if (devices.up != null) devices.up = (devices.up || 0) + 1;
                        if (devices.offline != null) devices.offline = Math.max(0, (devices.offline || 0) - 1);
                        if (devices.down != null) devices.down = Math.max(0, (devices.down || 0) - 1);
                    }

                    // Recompute online percent for consistency
                    const total = devices.total ?? 0;
                    const online = devices.online ?? devices.up ?? 0;
                    const percent = total > 0 ? Math.round((online / total) * 1000) / 10 : 0;
                    if (devices.online_percent != null || devices.up_percent == null) {
                        devices.online_percent = percent;
                    }
                    devices.up_percent = percent;
                }
                break;

            case 'alert_created':
                // Increment alert counts
                if (dashboardState.summary.active_alerts) {
                    const severity = (payload.severity || 'INFO').toLowerCase();
                    if (severity === 'critical') {
                        dashboardState.summary.active_alerts.critical = (dashboardState.summary.active_alerts.critical || 0) + 1;
                    } else if (severity === 'warning') {
                        dashboardState.summary.active_alerts.warning = (dashboardState.summary.active_alerts.warning || 0) + 1;
                    } else {
                        dashboardState.summary.active_alerts.info = (dashboardState.summary.active_alerts.info || 0) + 1;
                    }
                }
                break;

            case 'latency_spike':
                // Update network health with new latency
                if (dashboardState.summary.network_health && payload.latency_ms) {
                    // Simple moving average approximation
                    const currentAvg = dashboardState.summary.network_health.avg_latency_ms || 0;
                    dashboardState.summary.network_health.avg_latency_ms =
                        Math.round((currentAvg * 0.8 + payload.latency_ms * 0.2) * 100) / 100;
                }
                break;
        }
    }

    // Update topProblems if we have a recent alert
    if (eventType === 'alert_created' && dashboardState.topProblems) {
        const newAlert = {
            device_ip: payload.device_ip,
            message: payload.message || `${payload.metric_name} alert`,
            severity: payload.severity || 'WARNING',
            time: new Date().toISOString()
        };

        // Add to front of recent alerts
        if (!dashboardState.topProblems.recent_alerts) {
            dashboardState.topProblems.recent_alerts = [];
        }
        dashboardState.topProblems.recent_alerts.unshift(newAlert);

        // Keep only last 10
        dashboardState.topProblems.recent_alerts = dashboardState.topProblems.recent_alerts.slice(0, 10);
    }

    // dashboardState.lastUpdated = new Date(); // Don't update timestamp on realtime events (causes clock-like ticking)
    notifyListeners();
    scheduleSave();
}
