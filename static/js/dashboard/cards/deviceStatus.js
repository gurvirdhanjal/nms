/**
 * Card Component: Device Health KPI Cards
 */
import { animateValue, checkStale } from '../utils.js';

function setValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const currentVal = parseInt(el.textContent, 10) || 0;
    if (currentVal !== value) {
        animateValue(el, currentVal, value);
    } else {
        el.textContent = value;
    }
}

function getRangeLabel(range) {
    switch ((range || '').toLowerCase()) {
        case '7d':
            return 'last 7d';
        case '30d':
            return 'last 30d';
        case '1h':
            return 'last 1h';
        case '24h':
        default:
            return 'last 24h';
    }
}

function getFirstTrendPoint(trendData = []) {
    if (!Array.isArray(trendData) || trendData.length === 0) return null;
    const withData = trendData.find(p => typeof p?.total === 'number' && p.total > 0);
    if (withData) return withData;
    const nonZero = trendData.find(p => typeof p?.value === 'number' && p.value > 0);
    if (nonZero) return nonZero;
    return trendData.find(p => typeof p?.value === 'number') || null;
}

function renderOfflineTrendMeta(offlineNow, totalDevices, trendsData) {
    const subEl = document.getElementById('sub-devices-offline');
    if (!subEl) return;

    const rangeLabel = getRangeLabel(trendsData?.range);
    const firstPoint = getFirstTrendPoint(trendsData?.availability_trend);

    if (!firstPoint || typeof firstPoint.value !== 'number' || !Number.isFinite(totalDevices) || totalDevices <= 0) {
        subEl.innerHTML = `<span class="text-secondary">Offline: ${offlineNow} (${rangeLabel})</span>`;
        return;
    }

    const baselineOffline = Math.max(0, Math.round(totalDevices * (1 - (Number(firstPoint.value) / 100))));
    const delta = Math.round(offlineNow - baselineOffline);
    const absDelta = Math.abs(delta);

    let arrow = '&rarr;';
    let deltaClass = 'text-secondary';
    if (delta > 0) {
        arrow = '&uarr;';
        deltaClass = 'text-danger';
    } else if (delta < 0) {
        arrow = '&darr;';
        deltaClass = 'text-success';
    }

    subEl.innerHTML = `Offline: ${offlineNow} <span class="${deltaClass} fw-semibold">${arrow} ${absDelta}</span> <span class="text-secondary">(${rangeLabel})</span>`;
}

export function renderDeviceStatusCards(data, timestamp, trendsData = null) {
    if (!data || !data.devices) return;

    const devices = data.devices || {};
    const toCount = (value, fallback = 0) => {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : fallback;
    };

    const total = toCount(devices.total);
    const healthy = toCount(devices.healthy);
    const degraded = toCount(devices.degraded);
    const online = toCount(devices.online ?? devices.up, healthy + degraded);
    const reachable = Math.max(online, healthy + degraded);
    const offline = toCount(devices.offline ?? devices.down);

    setValue('val-devices-healthy', reachable);
    // Degraded removed from UI
    setValue('val-devices-offline', offline);
    setValue('val-devices-maintenance', toCount(devices.maintenance));

    const healthyMetaEl = document.getElementById('sub-devices-healthy');
    if (healthyMetaEl) {
        if (reachable <= 0) {
            healthyMetaEl.innerHTML = '<span class="text-secondary">No reachable devices</span>';
        } else if (degraded > 0) {
            healthyMetaEl.innerHTML = `<span class="text-secondary">${healthy} healthy, ${degraded} degraded</span>`;
        } else {
            healthyMetaEl.innerHTML = '<span class="text-secondary">All reachable devices are healthy</span>';
        }
    }

    renderOfflineTrendMeta(offline, total, trendsData);

    checkStale(timestamp, 'card-devices-healthy');
    checkStale(timestamp, 'card-devices-offline');
    checkStale(timestamp, 'card-devices-maintenance');
}
