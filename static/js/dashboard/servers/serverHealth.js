import { openServerModal } from '../modals/serverDetailModal.js';

const statusColors = {
    'Healthy': 'text-success',
    'Warning': 'text-warning',
    'Critical': 'text-danger',
    'Offline': 'text-secondary',
    'Unknown': 'text-muted'
};

const statusDot = {
    'Healthy': 'status-dot status-healthy',
    'Warning': 'status-dot status-warning',
    'Critical': 'status-dot status-critical',
    'Offline': 'status-dot status-offline',
    'Unknown': 'status-dot status-unknown'
};

export function renderServerHealthSummary(payload) {
    const counts = payload?.counts;
    if (!counts) return;

    const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    set('val-servers-total', counts.total ?? 0);
    set('val-servers-healthy', counts.healthy ?? 0);
    set('val-servers-warning', counts.warning ?? 0);
    set('val-servers-critical', counts.critical ?? 0);
    set('val-servers-offline', counts.offline ?? 0);
}

export function renderServerHealthTable(payload) {
    const tableBody = document.getElementById('table-server-health-body');
    if (!tableBody) return;

    const servers = payload?.servers || [];
    if (servers.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="3" class="text-center text-secondary p-3">No servers found</td></tr>';
        return;
    }

    tableBody.innerHTML = servers.map(s => {
        const name = s.hostname || s.device_name || s.ip || 'Unknown';
        const health = s.health || 'Unknown';
        const healthClass = statusColors[health] || 'text-muted';
        const dotClass = statusDot[health] || 'status-dot status-unknown';
        const lastSeen = s.last_seen ? new Date(s.last_seen).toLocaleString() : 'Never';

        return `
            <tr class="server-health-row" data-id="${s.device_id}">
                <td>
                    <div class="fw-bold">${name}</div>
                    <div class="small text-secondary font-monospace">${s.ip || '-'}</div>
                </td>
                <td>
                    <span class="${dotClass}"></span>
                    <span class="${healthClass} fw-bold">${health}</span>
                </td>
                <td class="text-secondary">${lastSeen}</td>
            </tr>
        `;
    }).join('');
}

export function initServerHealthTable() {
    const table = document.getElementById('table-server-health');
    if (!table) return;

    table.addEventListener('click', (e) => {
        const row = e.target.closest('tr.server-health-row');
        if (!row) return;
        const deviceId = row.dataset.id;
        if (deviceId) openServerModal(deviceId);
    });
}
