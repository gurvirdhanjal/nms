/**
 * Card Component: Devices Online
 */
import { checkStale, animateValue } from '../utils.js';

export function renderDevicesOnline(data, timestamp) {
    const cardId = 'card-devices-online';
    const container = document.getElementById(cardId);
    if (!container) return;

    const body = container.querySelector('.card-body');
    if (!data || !data.devices) {
        if (body) body.innerHTML = '<div class="text-secondary">No Data</div>';
        return;
    }

    const devices = data.devices || {};
    const total = devices.total ?? 0;
    const online = devices.online ?? devices.up ?? 0;
    const offline = devices.offline ?? devices.down ?? 0;
    const maintenance = devices.maintenance ?? 0;
    const ratio = total > 0 ? (online / total) * 100 : 0;

    // Status border (keep logic minimal)
    container.classList.remove('warning', 'danger');
    if (total > 0) {
        if (ratio < 70) {
            container.classList.add('danger');
        } else if (ratio < 90) {
            container.classList.add('warning');
        }
    }

    // Main Value (Animated)
    const valueEl = document.getElementById('val-devices-online');
    if (valueEl) {
        let onlineEl = valueEl.querySelector('.anim-online');
        let totalEl = valueEl.querySelector('.anim-total');

        if (!onlineEl) {
            valueEl.innerHTML = `<span class="anim-online">${online}</span>/<span class="anim-total">${total}</span>`;
        } else {
            const currentOnline = parseInt(onlineEl.textContent, 10) || 0;
            const currentTotal = parseInt(totalEl.textContent, 10) || 0;
            animateValue(onlineEl, currentOnline, online);
            animateValue(totalEl, currentTotal, total);
        }
    }

    // Sub Info (keep minimal for KPI row)
    const breakdownEl = document.getElementById('sub-devices-online');
    if (breakdownEl) {
        breakdownEl.innerHTML = `<span class="tactical-text-muted small">${offline} down · ${maintenance} maintenance · last 5 min</span>`;
    }

    const trendEl = document.getElementById('trend-devices-online');
    if (trendEl) {
        trendEl.innerHTML = `${ratio >= 95 ? '&uarr;' : ratio >= 85 ? '&rarr;' : '&darr;'} ${Math.round(ratio)}%`;
    }

    const contextEl = document.getElementById('ctx-devices-online');
    if (contextEl) {
        contextEl.textContent = `${online} responding of ${total} in scope`;
    }

    const stateEl = document.getElementById('state-devices-online');
    if (stateEl) {
        const status = ratio >= 95 ? 'Healthy' : ratio >= 85 ? 'Degraded' : 'Critical';
        stateEl.textContent = status;
        stateEl.className = `soc-kpi-status ${status === 'Healthy' ? 'status-healthy' : status === 'Degraded' ? 'status-warning' : 'status-critical'}`;
    }

    // Stale Check
    checkStale(timestamp, cardId);
}
