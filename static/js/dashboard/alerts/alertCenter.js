import { openServerModal } from '../modals/serverDetailModal.js?v=2.10';
import { setupTacticalDropdown } from '../utils.js';
import { patchKeyedTableRows, setTableMessageRow } from '../domPatch.js';

const ALERT_SEARCH_DEBOUNCE_MS = 140;
const ALERT_RENDER_BATCH_SIZE = 40;
const MAX_SYNC_ALERT_ROWS = 80;
const networkTypes = new Set(['router', 'switch', 'firewall', 'access_point', 'network device']);
const alertTimeFormatter = new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
});

let currentAlerts = [];
let normalizedAlerts = [];
let handlers = {
    onDeviceBreakdown: null,
};

let severityDropdown = null;
let scopeDropdown = null;
let deviceTypeDropdown = null;
let queryInput = null;
let queryDebounceTimer = null;
let renderFrameId = null;
let renderVersion = 0;
let lastAlertSignature = '';
let lastDeviceTypeSignature = '';

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

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatAlertTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '-';
    return alertTimeFormatter.format(date);
}

function buildAlertSignature(alerts) {
    if (!Array.isArray(alerts) || !alerts.length) return 'empty';
    return alerts.map((alert, index) => [
        alert.id || index,
        alert.timestamp || '',
        alert.severity || '',
        alert.resolved ? '1' : '0',
        alert.is_acknowledged ? '1' : '0',
        alert.message || ''
    ].join(':')).join('|');
}

function normalizeAlert(alert, index) {
    const severityLabel = normalizeSeverity(alert.severity);
    const scope = classifyScope(alert);
    const deviceTypeKey = (alert.device_type || '').toLowerCase();
    const deviceLabel = alert.device_name || alert.device_ip || 'Unknown';
    const timestampMs = alert.timestamp ? Date.parse(alert.timestamp) : 0;
    const severityRank = severityLabel === 'Critical' ? 0 : severityLabel === 'Warning' ? 1 : 2;

    return {
        ...alert,
        _key: alert.id || `${alert.timestamp || 'alert'}-${alert.device_id || index}`,
        _severityLabel: severityLabel,
        _severityRank: severityRank,
        _scope: scope,
        _deviceTypeKey: deviceTypeKey,
        _deviceTypeLabel: alert.device_type || '-',
        _deviceLabel: deviceLabel,
        _timestampMs: Number.isFinite(timestampMs) ? timestampMs : 0,
        _timeLabel: formatAlertTime(alert.timestamp),
        _rowClass: `alert-row alert-row-${(alert.severity || 'info').toLowerCase()}`,
        _searchText: [
            deviceLabel,
            alert.device_ip,
            alert.original_device_ip,
            alert.device_type,
            alert.message,
            scope,
            severityLabel
        ].filter(Boolean).join(' ').toLowerCase(),
    };
}

function getFilters() {
    return {
        severity: severityDropdown ? severityDropdown.getValue() : 'all',
        scope: scopeDropdown ? scopeDropdown.getValue() : 'all',
        deviceType: deviceTypeDropdown ? deviceTypeDropdown.getValue() : 'all',
        query: queryInput?.value?.trim().toLowerCase() || ''
    };
}

function matchesFilter(alert, filters) {
    if (filters.severity !== 'all' && alert._severityLabel !== filters.severity) return false;
    if (filters.scope !== 'all' && alert._scope !== filters.scope) return false;
    if (filters.deviceType !== 'all' && alert._deviceTypeKey !== filters.deviceType) return false;
    if (filters.query && !alert._searchText.includes(filters.query)) return false;
    return true;
}

function filterAndSortAlerts(alerts, filters) {
    return alerts
        .filter((alert) => matchesFilter(alert, filters))
        .sort((a, b) => {
            if (a._severityRank !== b._severityRank) return a._severityRank - b._severityRank;
            return b._timestampMs - a._timestampMs;
        });
}

function buildAlertCells(alert) {
    const sevClass = alert._severityLabel === 'Critical'
        ? 'tactical-badge-danger'
        : alert._severityLabel === 'Warning'
            ? 'tactical-badge-warning'
            : 'tactical-badge-info';

    return `
        <td><span class="badge ${sevClass}">${escapeHtml(alert._severityLabel)}</span></td>
        <td>${escapeHtml(alert._scope)}</td>
        <td>
            <div class="fw-bold">${escapeHtml(alert._deviceLabel)}</div>
            <div class="small text-secondary">${escapeHtml(alert._deviceTypeLabel)}</div>
        </td>
        <td>${escapeHtml(alert.message || '-')}</td>
        <td class="text-secondary">${escapeHtml(alert._timeLabel)}</td>
    `;
}

function applyAlertRow(row, alert) {
    row.className = alert._rowClass;
    row.dataset.scope = alert._scope;
    row.dataset.deviceId = alert.device_id || '';
    row.onclick = () => {
        if (alert._scope === 'Server' && alert.device_id) {
            openServerModal(alert.device_id);
        } else if (handlers.onDeviceBreakdown) {
            handlers.onDeviceBreakdown();
        }
    };
}

function createAlertRow(alert) {
    const row = document.createElement('tr');
    row.dataset.rowKey = alert._key;
    row.innerHTML = buildAlertCells(alert);
    applyAlertRow(row, alert);
    return row;
}

function renderAlertRowsProgressively(tbody, alerts) {
    const activeVersion = ++renderVersion;
    let index = 0;
    tbody.textContent = '';

    const appendBatch = () => {
        if (activeVersion !== renderVersion) return;

        const batch = document.createDocumentFragment();
        const upperBound = Math.min(index + ALERT_RENDER_BATCH_SIZE, alerts.length);
        for (; index < upperBound; index += 1) {
            batch.appendChild(createAlertRow(alerts[index]));
        }
        tbody.appendChild(batch);

        if (index < alerts.length) {
            if ('requestIdleCallback' in window) {
                window.requestIdleCallback(appendBatch, { timeout: 120 });
            } else {
                window.requestAnimationFrame(appendBatch);
            }
        }
    };

    appendBatch();
}

function scheduleAlertTableRender() {
    if (renderFrameId) {
        window.cancelAnimationFrame(renderFrameId);
    }
    renderFrameId = window.requestAnimationFrame(() => {
        renderFrameId = null;
        renderAlertTable(normalizedAlerts);
    });
}

function handleFilterChange() {
    scheduleAlertTableRender();
}

function handleQueryInput() {
    if (queryDebounceTimer) {
        window.clearTimeout(queryDebounceTimer);
    }
    queryDebounceTimer = window.setTimeout(() => {
        queryDebounceTimer = null;
        scheduleAlertTableRender();
    }, ALERT_SEARCH_DEBOUNCE_MS);
}

export function initAlertCenter(options = {}) {
    handlers = { ...handlers, ...options };

    severityDropdown = setupTacticalDropdown('filter-severity-container', handleFilterChange);
    scopeDropdown = setupTacticalDropdown('filter-scope-container', handleFilterChange);
    deviceTypeDropdown = setupTacticalDropdown('filter-device-type-container', handleFilterChange);

    queryInput = document.getElementById('filter-alert-device');
    if (queryInput) {
        queryInput.addEventListener('input', handleQueryInput);
        queryInput.addEventListener('search', handleQueryInput);
    }
}

export function renderAlertCenter(alerts) {
    currentAlerts = Array.isArray(alerts) ? alerts : [];
    const alertSignature = buildAlertSignature(currentAlerts);
    if (alertSignature === lastAlertSignature) {
        return;
    }

    lastAlertSignature = alertSignature;
    normalizedAlerts = currentAlerts.map(normalizeAlert);
    renderAlertSummary(normalizedAlerts);
    scheduleAlertTableRender();
}

function renderAlertSummary(alerts) {
    const total = alerts.length;
    const counts = { Critical: 0, Warning: 0, Informational: 0 };
    const scopes = { Network: 0, Device: 0, Server: 0 };

    alerts.forEach((alert) => {
        counts[alert._severityLabel] = (counts[alert._severityLabel] || 0) + 1;
        scopes[alert._scope] = (scopes[alert._scope] || 0) + 1;
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

    if (deviceTypeDropdown) {
        const types = Array.from(new Set(alerts.map((alert) => alert._deviceTypeKey).filter(Boolean))).sort();
        const typeSignature = types.join('|');

        if (typeSignature !== lastDeviceTypeSignature) {
            lastDeviceTypeSignature = typeSignature;
            const options = [{ value: 'all', label: 'All Device Types' }];
            types.forEach((typeKey) => {
                options.push({ value: typeKey, label: titleCase(typeKey) });
            });
            deviceTypeDropdown.updateOptions(options);
        }
    }
}

function renderAlertTable(alerts) {
    const tbody = document.getElementById('table-alerts-body');
    if (!tbody) return;

    const filtered = filterAndSortAlerts(alerts, getFilters());
    if (!filtered.length) {
        renderVersion += 1;
        setTableMessageRow(tbody, 5, 'No alerts match filters', 'text-center text-muted');
        return;
    }

    if (filtered.length > MAX_SYNC_ALERT_ROWS) {
        renderAlertRowsProgressively(tbody, filtered);
        return;
    }

    renderVersion += 1;
    patchKeyedTableRows(tbody, filtered, {
        getKey: (alert) => alert._key,
        emptyColSpan: 5,
        emptyMessage: 'No alerts match filters',
        emptyClassName: 'text-center text-muted',
        renderCells: (alert) => buildAlertCells(alert),
        applyRow: (row, alert) => applyAlertRow(row, alert)
    });
}
