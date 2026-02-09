import { timeAgo } from '../utils.js';

export function renderTopLatencyTable(data) {
    const tbody = document.getElementById('table-top-latency-body');
    if (!tbody) return;

    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No high latency devices</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(device => `
        <tr onclick="window.location.href='/devices?edit_id=${device.device_id}'" style="cursor: pointer;" title="View View Device Details">
            <td>${device.device_name || device.ip}</td>
            <td class="tactical-text-danger">${device.value} ms</td>
            <td class="tactical-text-muted d-none d-md-table-cell">${device.ip}</td>
            <td class="tactical-text-muted d-none d-md-table-cell" title="${device.time ? new Date(device.time).toLocaleString() : '-'}">${device.time ? timeAgo(device.time) : '-'}</td>
        </tr>
    `).join('');
}

export function renderTopPacketLossTable(data) {
    const tbody = document.getElementById('table-top-loss-body');
    if (!tbody) return;

    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center tactical-text-muted">No packet loss detected</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(device => `
        <tr onclick="window.location.href='/devices?edit_id=${device.device_id}'" style="cursor: pointer;" title="View Device Details">
            <td>${device.device_name || device.ip}</td>
            <td class="tactical-text-danger">${device.value}%</td>
            <td class="tactical-text-muted d-none d-md-table-cell">${device.ip}</td>
            <td class="tactical-text-muted d-none d-md-table-cell" title="${device.time ? new Date(device.time).toLocaleString() : '-'}">${device.time ? timeAgo(device.time) : '-'}</td>
        </tr>
    `).join('');
}

export function renderRecentAlertsTable(data) {
    const tbody = document.getElementById('table-recent-alerts-body');
    if (!tbody) return;

    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center tactical-text-muted">No recent alerts</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(alert => `
        <tr>
            <td><span class="badge ${getSeverityClass(alert.severity)}">${alert.severity}</span></td>
            <td>${alert.message}</td>
            <td class="tactical-text-muted d-none d-md-table-cell" title="${new Date(alert.time).toLocaleString()}">
                <div style="font-size: 0.9em; font-weight: 600; color: var(--tactical-accent);">${timeAgo(alert.time)}</div>
                <div style="font-size: 0.75em; opacity: 0.7">${new Date(alert.time).toLocaleTimeString()}</div>
                ${!alert.is_acknowledged ?
            `<button class="btn btn-sm btn-link tactical-text-accent p-0 ms-2" onclick="acknowledgeAlert('${alert.id}', event)" title="Acknowledge">
                        <i class="far fa-check-circle"></i>
                    </button>` :
            '<i class="fas fa-check-circle text-success ms-2" title="Acknowledged"></i>'
        }
            </td>
        </tr>
    `).join('');
}

export function renderTopAffectedDevices(data) {
    const tbody = document.getElementById('table-top-affected-body');
    if (!tbody) return;

    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center tactical-text-muted">No affected devices</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(device => `
        <tr onclick="window.location.href='/devices?edit_id=${device.device_id}'" style="cursor: pointer;" title="View Device Details">
            <td>${device.device_name || device.ip}</td>
            <td class="tactical-text-muted">${device.ip}</td>
            <td class="tactical-text-muted" title="${device.time ? new Date(device.time).toLocaleString() : '-'}">${device.time ? timeAgo(device.time) : '-'}</td>
        </tr>
    `).join('');
}

function getSeverityClass(severity) {
    switch (severity?.toUpperCase()) {
        case 'CRITICAL': return 'tactical-badge-danger';
        case 'WARNING': return 'tactical-badge-warning';
        default: return 'tactical-badge-info';
    }
}

// Global function for onclick handler
window.acknowledgeAlert = async function (id, event) {
    if (event) event.stopPropagation();

    try {
        const response = await fetch(`/api/dashboard/alerts/${id}/acknowledge`, { method: 'POST' });
        if (response.ok) {
            // Reload the table or row
            // Ideally trigger a dashboard refresh. For now, simple reload.
            // But verify route: /api/alerts/... (previous step added route at /alerts, but blueprint prefix is /api/dashboard)
            // Wait, route definition: @dashboard_bp.route('/alerts/<event_id>/acknowledge')
            // dashboard_bp is usually at /api/dashboard. So /api/dashboard/alerts/...
            // Checking route again...

            // Just refresh dashboard for now
            // Or find the button and change it to checked
            const btn = event.target.closest('button');
            if (btn) {
                btn.outerHTML = '<i class="fas fa-check-circle text-success ms-2" title="Acknowledged"></i>';
            }
        }
    } catch (e) {
        console.error("Ack error", e);
    }
}
