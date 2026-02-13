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
    const maintenance = devices.maintenance ?? 0; // Add maintenance count if available

    // Status border (keep logic minimal)
    container.classList.remove('warning', 'danger');
    if (total > 0) {
        const ratio = (online / total) * 100;
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
        breakdownEl.innerHTML = `<span class="tactical-text-muted small">Click to view breakdown</span>`;
    }

    // Stale Check
    checkStale(timestamp, cardId);
}
