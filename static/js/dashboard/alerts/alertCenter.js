import { openServerModal } from '../modals/serverDetailModal.js';
import { setupTacticalDropdown } from '../utils.js';

let currentAlerts = [];
let handlers = {
    onDeviceBreakdown: null,
};

// Dropdown instances
let severityDropdown = null;
let scopeDropdown = null;
let deviceTypeDropdown = null;

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

    const onChange = () => renderAlertTable(currentAlerts);

    severityDropdown = setupTacticalDropdown('filter-severity-container', onChange);
    scopeDropdown = setupTacticalDropdown('filter-scope-container', onChange);
    deviceTypeDropdown = setupTacticalDropdown('filter-device-type-container', onChange);

    const queryInput = document.getElementById('filter-alert-device');
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
    if (deviceTypeDropdown) {
        const current = deviceTypeDropdown.getValue();
        const types = Array.from(new Set(alerts.map(a => (a.device_type || '').toLowerCase()).filter(Boolean))).sort();

        const options = [{ value: 'all', label: 'All Device Types' }];
        types.forEach(t => {
            options.push({ value: t, label: titleCase(t) });
        });

        deviceTypeDropdown.updateOptions(options);
    }
}

function renderAlertTable(alerts) {
    const tbody = document.getElementById('table-alerts-body');
    if (!tbody) return;

    const queryInput = document.getElementById('filter-alert-device');

    const filters = {
        severity: severityDropdown ? severityDropdown.getValue() : 'all',
        scope: scopeDropdown ? scopeDropdown.getValue() : 'all',
        deviceType: deviceTypeDropdown ? deviceTypeDropdown.getValue() : 'all',
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

    tbody.innerHTML = '';
    filtered.forEach(a => {
        const sev = normalizeSeverity(a.severity);
        const scope = classifyScope(a);
        const time = a.timestamp ? new Date(a.timestamp).toLocaleString() : '-';
        const deviceLabel = a.device_name || a.device_ip || 'Unknown';
        const deviceType = a.device_type || '-';
        const sevClass = sev === 'Critical' ? 'tactical-badge-danger' : sev === 'Warning' ? 'tactical-badge-warning' : 'tactical-badge-info';
        const severityClass = `alert-row-${(a.severity || 'info').toLowerCase()}`;

        const row = document.createElement('tr');
        row.classList.add('alert-row');
        row.classList.add(severityClass);
        row.dataset.scope = scope;
        row.dataset.deviceId = a.device_id || '';
        row.innerHTML = `
            <td><span class="badge ${sevClass}">${sev}</span></td>
            <td>${scope}</td>
            <td>
                <div class="fw-bold">${deviceLabel}</div>
                <div class="small text-secondary">${deviceType}</div>
            </td>
            <td>${a.message || '-'}</td>
            <td class="text-secondary">${time}</td>
        `;
        row.addEventListener('click', () => {
            const rowScope = row.dataset.scope;
            const deviceId = row.dataset.deviceId;
            if (rowScope === 'Server' && deviceId) {
                openServerModal(deviceId);
            } else if (handlers.onDeviceBreakdown) {
                handlers.onDeviceBreakdown();
            }
        });
        tbody.appendChild(row);
    });
}
