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
    const total = devices.total ?? 0;
    const online = devices.online ?? devices.up ?? 0;
    const degraded = devices.degraded ?? 0;
    const healthy = devices.healthy ?? Math.max(0, online - degraded);
    const offline = devices.offline ?? devices.down ?? 0;

    setValue('val-devices-healthy', healthy);
    // Degraded removed from UI
    setValue('val-devices-offline', offline);
    setValue('val-devices-maintenance', devices.maintenance ?? 0);
    renderOfflineTrendMeta(offline, total, trendsData);

    checkStale(timestamp, 'card-devices-healthy');
    checkStale(timestamp, 'card-devices-offline');
    checkStale(timestamp, 'card-devices-maintenance');
}
