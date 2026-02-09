import { openServerModal } from '../modals/serverDetailModal.js';

let currentAlerts = [];
let handlers = {
    onDeviceBreakdown: null,
};

const networkTypes = new Set(['router', 'switch', 'firewall', 'access_point', 'network device']);

function classifyScope(alert) {
    if (alert.scope) return alert.scope;
    const t = (alert.device_type || '').toLowerCase();
    if (t === 'server') return 'Server';
    if (networkTypes.has(t)) return 'Network';
    return 'Device';
}

function titleCase(value) {
    return value.replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function normalizeSeverity(sev) {
    const s = (sev || '').toUpperCase();
    if (s === 'CRITICAL') return 'Critical';
    if (s === 'WARNING') return 'Warning';
    return 'Informational';
}

function matchesFilter(alert, filters) {
    if (filters.severity !== 'all' && normalizeSeverity(alert.severity) !== filters.severity) return false;
    if (filters.scope !== 'all' && classifyScope(alert) !== filters.scope) return false;
    if (filters.deviceType !== 'all' && (alert.device_type || '').toLowerCase() !== filters.deviceType) return false;
    if (filters.query) {
        const q = filters.query.toLowerCase();
        const hay = [
            alert.device_name,
            alert.device_ip,
            alert.message
        ].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
    }
    return true;
}

export function initAlertCenter(options = {}) {
    handlers = { ...handlers, ...options };

    const sevFilter = document.getElementById('filter-alert-severity');
    const scopeFilter = document.getElementById('filter-alert-scope');
    const typeFilter = document.getElementById('filter-alert-device-type');
    const queryInput = document.getElementById('filter-alert-device');

    const onChange = () => renderAlertTable(currentAlerts);
    if (sevFilter) sevFilter.addEventListener('change', onChange);
    if (scopeFilter) scopeFilter.addEventListener('change', onChange);
    if (typeFilter) typeFilter.addEventListener('change', onChange);
    if (queryInput) queryInput.addEventListener('input', onChange);
}

export function renderAlertCenter(alerts) {
    currentAlerts = Array.isArray(alerts) ? alerts : [];
    renderAlertSummary(currentAlerts);
    renderAlertTable(currentAlerts);
}

function renderAlertSummary(alerts) {
    const total = alerts.length;
    const counts = { Critical: 0, Warning: 0, Informational: 0 };
    const scopes = { Network: 0, Device: 0, Server: 0 };

    alerts.forEach(a => {
        const sev = normalizeSeverity(a.severity);
        counts[sev] = (counts[sev] || 0) + 1;
        const scope = classifyScope(a);
        scopes[scope] = (scopes[scope] || 0) + 1;
    });

    const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    set('val-alerts-total', total);
    set('val-alerts-critical', counts.Critical);
    set('val-alerts-warning', counts.Warning);
    set('val-alerts-info', counts.Informational);

    const scopeEl = document.getElementById('sub-alerts-scope');
    if (scopeEl) {
        scopeEl.textContent = `Network: ${scopes.Network} | Device: ${scopes.Device} | Server: ${scopes.Server}`;
    }

    // Populate device type filter options
    const typeFilter = document.getElementById('filter-alert-device-type');
    if (typeFilter) {
        const current = typeFilter.value || 'all';
        const types = Array.from(new Set(alerts.map(a => (a.device_type || '').toLowerCase()).filter(Boolean))).sort();
        const options = ['all', ...types];
        typeFilter.innerHTML = options.map(t => {
            const label = t === 'all' ? 'All Device Types' : t;
            const selected = t === current ? 'selected' : '';
            return `<option value="${t}" ${selected}>${label === 'All Device Types' ? label : titleCase(label)}</option>`;
        }).join('');
    }
}

function renderAlertTable(alerts) {
    const tbody = document.getElementById('table-alerts-body');
    if (!tbody) return;

    const sevFilter = document.getElementById('filter-alert-severity');
    const scopeFilter = document.getElementById('filter-alert-scope');
    const typeFilter = document.getElementById('filter-alert-device-type');
    const queryInput = document.getElementById('filter-alert-device');

    const filters = {
        severity: sevFilter?.value || 'all',
        scope: scopeFilter?.value || 'all',
        deviceType: typeFilter?.value || 'all',
        query: queryInput?.value?.trim() || ''
    };

    const filtered = alerts.filter(a => matchesFilter(a, filters));
    const rank = { Critical: 0, Warning: 1, Informational: 2 };
    filtered.sort((a, b) => {
        const ra = rank[normalizeSeverity(a.severity)] ?? 9;
        const rb = rank[normalizeSeverity(b.severity)] ?? 9;
        if (ra !== rb) return ra - rb;
        const ta = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const tb = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        return tb - ta;
    });
    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No alerts match filters</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(a => {
        const sev = normalizeSeverity(a.severity);
        const scope = classifyScope(a);
        const time = a.timestamp ? new Date(a.timestamp).toLocaleString() : '-';
        const deviceLabel = a.device_name || a.device_ip || 'Unknown';
        const deviceType = a.device_type || '-';
        const sevClass = sev === 'Critical' ? 'tactical-badge-danger' : sev === 'Warning' ? 'tactical-badge-warning' : 'tactical-badge-info';

        return `
            <tr class="alert-row" data-scope="${scope}" data-device-id="${a.device_id || ''}">
                <td><span class="badge ${sevClass}">${sev}</span></td>
                <td>${scope}</td>
                <td>
                    <div class="fw-bold">${deviceLabel}</div>
                    <div class="small text-secondary">${deviceType}</div>
                </td>
                <td>${a.message || '-'}</td>
                <td class="text-secondary">${time}</td>
            </tr>
        `;
    }).join('');

    tbody.querySelectorAll('tr.alert-row').forEach(row => {
        row.addEventListener('click', () => {
            const scope = row.dataset.scope;
            const deviceId = row.dataset.deviceId;
            if (scope === 'Server' && deviceId) {
                openServerModal(deviceId);
            } else if (handlers.onDeviceBreakdown) {
                handlers.onDeviceBreakdown();
            }
        });
    });
}
