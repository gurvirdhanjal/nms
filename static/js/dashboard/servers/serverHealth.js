import { openServerModal } from '../modals/serverDetailModal.js';
import { timeAgo } from '../utils.js';
import { patchKeyedTableRows } from '../domPatch.js';

let currentFilter = 'all';

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

const statusBadge = {
    'Healthy': 'tactical-badge-success',
    'Warning': 'tactical-badge-warning',
    'Critical': 'tactical-badge-danger',
    'Offline': 'tactical-badge-secondary',
    'Unknown': 'tactical-badge-secondary'
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

    let servers = payload?.servers || [];
    if (currentFilter !== 'all') {
        servers = servers.filter(s => (s.health || '').toLowerCase() === currentFilter);
    }

    if (servers.length === 0) {
        const msg = currentFilter === 'all'
            ? 'No servers found'
            : `No servers match "${currentFilter}"`;
        patchKeyedTableRows(tableBody, [], {
            emptyColSpan: 10,
            emptyMessage: msg,
            emptyClassName: 'text-center text-secondary p-3'
        });
        return;
    }

    const formatPct = (val) => (val !== null && val !== undefined) ? `${parseFloat(val).toFixed(1)}%` : '-';
    const formatMs = (val) => (val !== null && val !== undefined) ? `${parseFloat(val).toFixed(2)} ms` : '-';

    patchKeyedTableRows(tableBody, servers, {
        getKey: (server, index) => server.device_id || server.ip || `server-${index}`,
        renderCells: (server) => {
            const name = server.hostname || server.device_name || server.ip || 'Unknown';
            const health = server.health || 'Unknown';
            const healthClass = statusColors[health] || 'text-muted';
            const dotClass = statusDot[health] || 'status-dot status-unknown';
            const badgeClass = statusBadge[health] || 'tactical-badge-secondary';
            const statusHint = health === 'Offline' ? 'Agent offline' : (health === 'Unknown' ? 'No data yet' : 'Agent reporting');
            const lastSeenLabel = server.last_seen ? timeAgo(server.last_seen) : 'Never';
            const lastSeenExact = server.last_seen ? new Date(server.last_seen).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '-';

            return `
                <td>
                    <div class="fw-bold text-truncate" style="max-width: 180px;">${name}</div>
                    <div class="small text-secondary font-monospace">${server.ip || '-'}</div>
                </td>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <span class="${dotClass}"></span>
                        <span class="tactical-badge ${badgeClass}">${health}</span>
                    </div>
                </td>
                <td>
                    <div class="fw-bold">${formatPct(server.cpu_usage)}</div>
                </td>
                <td>
                    <div class="fw-bold">${formatPct(server.memory_usage)}</div>
                </td>
                <td>
                    <div class="fw-bold">${formatPct(server.disk_usage)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="fw-bold">${formatMs(server.latency)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="fw-bold text-secondary">${formatPct(server.packet_loss)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="fw-bold text-secondary">${formatMs(server.jitter)}</div>
                </td>
                <td>
                    <div class="fw-bold text-nowrap">${lastSeenLabel}</div>
                    <div class="small text-secondary text-nowrap">${lastSeenExact}</div>
                </td>
                <td class="text-end">
                    <div class="dropdown">
                        <button class="btn btn-xs tactical-btn-outline dropdown-toggle no-caret" data-bs-toggle="dropdown">
                            <i class="fas fa-ellipsis-v"></i>
                        </button>
                        <ul class="dropdown-menu tactical-dropdown">
                            <li><a class="dropdown-item" href="/device/${server.device_id}">View Details</a></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item text-danger" href="#">Acknowledge</a></li>
                        </ul>
                    </div>
                </td>
            `;
        },
        applyRow: (row, server) => {
            row.className = 'server-health-row';
            row.dataset.id = server.device_id || '';
        }
    });
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

export function setServerHealthFilter(filter) {
    currentFilter = (filter || 'all').toLowerCase();
}

// Global chart instances for sparklines
let cpuSparkChart = null;
let memSparkChart = null;

export function renderFleetOverview(data) {
    if (!data) return;

    // 1. Health Cards
    const health = data.health || {};
    document.getElementById('val-fleet-health-percent').textContent =
        health.total > 0 ? `${Math.round((health.healthy / health.total) * 100)}%` : '-';
    document.getElementById('val-fleet-health-counts').textContent =
        `${health.healthy}/${health.total} Servers Healthy`;

    // 2. Capacity Metrics
    const agg = data.aggregates || {};
    const p95 = data.p95 || {};
    document.getElementById('val-fleet-avg-cpu').textContent = `${agg.cpu}%`;
    document.getElementById('val-fleet-p95-cpu').textContent = p95.cpu;
    document.getElementById('val-fleet-avg-mem').textContent = `${agg.memory}%`;
    document.getElementById('val-fleet-p95-mem').textContent = p95.memory;
    document.getElementById('val-fleet-avg-disk').textContent = `${agg.disk}%`;

    // 3. Alerts Bar
    const alertBar = document.getElementById('fleet-alerts-bar');
    const alertText = document.getElementById('fleet-alerts-text');
    const criticals = data.alerts || [];

    // Count disk warnings locally if needed, or rely on backend
    // For now, just show critical servers
    if (criticals.length > 0) {
        alertBar.style.display = 'block';
        const serverNames = criticals.map(c => c.name).slice(0, 3).join(', ');
        const more = criticals.length > 3 ? ` and ${criticals.length - 3} more` : '';
        alertText.innerHTML = `<strong>Attention Needed:</strong> High load detected on ${serverNames}${more}.`;
    } else {
        alertBar.style.display = 'none';
    }

    // 4. Sparklines
    renderSparkline('chart-spark-cpu', data.trends?.labels, data.trends?.cpu, '#0d6efd', 'cpu');
    renderSparkline('chart-spark-mem', data.trends?.labels, data.trends?.memory, '#6610f2', 'mem');
}

function renderSparkline(canvasId, labels, data, color, type) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const chartRef = type === 'cpu' ? cpuSparkChart : memSparkChart;
    if (chartRef && chartRef.canvas === ctx) {
        chartRef.data.labels = labels || [];
        chartRef.data.datasets[0].data = data || [];
        chartRef.data.datasets[0].borderColor = color;
        chartRef.update('none');
        return;
    }

    // Canvas might have been re-rendered; recreate only in that case.
    if (chartRef) chartRef.destroy();

    const nextChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels || [],
            datasets: [{
                data: data || [],
                borderColor: color,
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: {
                x: { display: false },
                y: { display: false, min: 0, max: 100 }
            },
            animation: false
        }
    });

    if (type === 'cpu') {
        cpuSparkChart = nextChart;
    } else {
        memSparkChart = nextChart;
    }
}

export function renderEnhancedServerTable(payload) {
    const tableBody = document.getElementById('table-server-health-body');
    if (!tableBody) return;

    let servers = payload?.servers || [];

    // Apply Filter
    if (currentFilter !== 'all') {
        if (currentFilter === 'problem') {
            servers = servers.filter(s => s.health !== 'Healthy');
        } else {
            servers = servers.filter(s => (s.health || '').toLowerCase() === currentFilter);
        }
    }

    if (servers.length === 0) {
        patchKeyedTableRows(tableBody, [], {
            emptyColSpan: 10,
            emptyMessage: 'No servers match filter',
            emptyClassName: 'text-center text-secondary p-3'
        });
        return;
    }

    patchKeyedTableRows(tableBody, servers, {
        getKey: (server, index) => server.device_id || server.ip || `server-enhanced-${index}`,
        renderCells: (server) => {
            const name = server.hostname || server.device_name || server.ip || 'Unknown';
            const health = server.health || 'Unknown';

            let dotClass = 'bg-secondary';
            if (health === 'Healthy') dotClass = 'bg-success';
            else if (health === 'Warning') dotClass = 'bg-warning';
            else if (health === 'Critical') dotClass = 'bg-danger';

            const cpu = server.cpu_usage ?? 0;
            const mem = server.memory_usage ?? 0;
            const disk = server.disk_usage ?? 0;
            const latency = server.latency;
            const packetLoss = server.packet_loss;
            const jitter = server.jitter;
            const lastSeenLabel = server.last_seen ? timeAgo(server.last_seen) : 'Never';
            const lastSeenExact = server.last_seen ? new Date(server.last_seen).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '-';

            const getValClass = (val) => val > 90 ? 'text-danger fw-bold' : (val > 75 ? 'text-warning fw-bold' : '');
            const formatPct = (val) => (val !== null && val !== undefined) ? `${Number(val).toFixed(1)}%` : '-';
            const formatMs = (val) => (val !== null && val !== undefined) ? `${Number(val).toFixed(1)} ms` : '-';

            return `
                <td>
                    <div class="d-flex align-items-center">
                        <div class="rounded-circle ${dotClass} me-3" style="width: 10px; height: 10px;"></div>
                        <div>
                            <div class="fw-bold text-light">${name}</div>
                            <div class="small text-secondary" style="font-size: 0.75rem;">${server.ip || ''}</div>
                        </div>
                    </div>
                </td>
                <td>
                    <span class="badge ${health === 'Healthy' ? 'bg-success-subtle text-success' : (health === 'Critical' ? 'bg-danger-subtle text-danger' : 'bg-warning-subtle text-warning')}">
                        ${health}
                    </span>
                </td>
                <td class="${getValClass(cpu)}">${cpu.toFixed(1)}%</td>
                <td class="${getValClass(mem)}">${mem.toFixed(1)}%</td>
                <td class="${getValClass(disk)}">${disk.toFixed(1)}%</td>
                <td class="d-none d-xl-table-cell">
                    <div class="fw-semibold">${formatMs(latency)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="${packetLoss > 0 ? 'text-warning fw-semibold' : 'text-secondary'}">${formatPct(packetLoss)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="text-secondary">${formatMs(jitter)}</div>
                </td>
                <td>
                    <div class="fw-semibold text-nowrap">${lastSeenLabel}</div>
                    <div class="small text-secondary text-nowrap">${lastSeenExact}</div>
                </td>
                <td class="text-end">
                    <div class="btn-group btn-group-sm" role="group">
                        <button type="button" class="btn btn-sm btn-dark border-secondary px-2 server-modal-btn" data-device-id="${server.device_id}" title="Quick View Modal">
                            <i class="fas fa-chart-line"></i>
                        </button>
                        <a href="/devices/${server.device_id}/server-monitoring" class="btn btn-sm btn-dark border-secondary px-2" title="Full Page Monitoring">
                            <i class="fas fa-external-link-alt"></i>
                        </a>
                    </div>
                </td>
            `;
        },
        applyRow: (row, server) => {
            row.className = 'server-health-row';
            row.dataset.id = server.device_id || '';
            
            // Add click handler for modal button
            const modalBtn = row.querySelector('.server-modal-btn');
            if (modalBtn) {
                modalBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const deviceId = modalBtn.dataset.deviceId;
                    if (deviceId) openServerModal(deviceId);
                });
            }
        }
    });
}
