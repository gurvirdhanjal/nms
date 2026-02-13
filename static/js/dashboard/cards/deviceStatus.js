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

export function renderDeviceStatusCards(data, timestamp) {
    if (!data || !data.devices) return;

    const devices = data.devices || {};
    const total = devices.total ?? 0;
    const online = devices.online ?? devices.up ?? 0;
    const degraded = devices.degraded ?? 0;
    const healthy = devices.healthy ?? Math.max(0, online - degraded);
    const offline = devices.offline ?? devices.down ?? 0;

    setValue('val-devices-healthy', healthy);
    setValue('val-devices-healthy', healthy);
    // Degraded removed from UI
    setValue('val-devices-offline', offline);
    setValue('val-devices-maintenance', devices.maintenance ?? 0);

    checkStale(timestamp, 'card-devices-healthy');
    checkStale(timestamp, 'card-devices-offline');
    checkStale(timestamp, 'card-devices-maintenance');
}
