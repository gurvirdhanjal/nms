import { openServerModal } from '../modals/serverDetailModal.js';
import { timeAgo } from '../utils.js';

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
        tableBody.innerHTML = `<tr><td colspan="3" class="text-center text-secondary p-3">${msg}</td></tr>`;
        return;
    }

    tableBody.innerHTML = servers.map(s => {
        const name = s.hostname || s.device_name || s.ip || 'Unknown';
        const health = s.health || 'Unknown';
        const healthClass = statusColors[health] || 'text-muted';
        const dotClass = statusDot[health] || 'status-dot status-unknown';
        const badgeClass = statusBadge[health] || 'tactical-badge-secondary';
        const statusHint = health === 'Offline' ? 'Agent offline' : (health === 'Unknown' ? 'No data yet' : 'Agent reporting');
        const lastSeenLabel = s.last_seen ? timeAgo(s.last_seen) : 'Never';
        const lastSeenExact = s.last_seen ? new Date(s.last_seen).toLocaleString() : '-';

        return `
            <tr class="server-health-row" data-id="${s.device_id}">
                <td>
                    <div class="fw-bold">${name}</div>
                    <div class="small text-secondary font-monospace">${s.ip || '-'}</div>
                </td>
                <td>
                    <span class="${dotClass}"></span>
                    <span class="tactical-badge ${badgeClass}">${health}</span>
                    <div class="small ${healthClass} mt-1">${statusHint}</div>
                </td>
                <td>
                    <div class="fw-bold">${lastSeenLabel}</div>
                    <div class="small text-secondary">${lastSeenExact}</div>
                </td>
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

    const chartConfig = {
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
    };

    if (type === 'cpu') {
        if (cpuSparkChart) cpuSparkChart.destroy();
        cpuSparkChart = new Chart(ctx, chartConfig);
    } else {
        if (memSparkChart) memSparkChart.destroy();
        memSparkChart = new Chart(ctx, chartConfig);
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
        tableBody.innerHTML = `<tr><td colspan="7" class="text-center text-secondary p-3">No servers match filter</td></tr>`;
        return;
    }

    tableBody.innerHTML = servers.map(s => {
        const name = s.hostname || s.device_name || s.ip || 'Unknown';
        const health = s.health || 'Unknown';

        // Status Dot Logic
        let dotClass = 'bg-secondary';
        if (health === 'Healthy') dotClass = 'bg-success';
        else if (health === 'Warning') dotClass = 'bg-warning';
        else if (health === 'Critical') dotClass = 'bg-danger';

        // Resource Usage (with color coding)
        const cpu = s.cpu_usage ?? 0;
        const mem = s.memory_usage ?? 0;
        const disk = s.disk_usage ?? 0;

        const getValClass = (val) => val > 90 ? 'text-danger fw-bold' : (val > 75 ? 'text-warning fw-bold' : '');

        return `
            <tr class="server-health-row" data-id="${s.device_id}" style="cursor: pointer;">
                <td>
                    <div class="d-flex align-items-center">
                        <div class="rounded-circle ${dotClass} me-3" style="width: 10px; height: 10px;"></div>
                        <div>
                            <div class="fw-bold text-light">${name}</div>
                            <div class="small text-secondary" style="font-size: 0.75rem;">${s.ip || ''}</div>
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
                <td>
                    <div class="text-secondary small">${s.last_seen ? timeAgo(s.last_seen) : 'Never'}</div>
                </td>
                <td>
                    <button class="btn btn-sm btn-dark border-secondary px-3">View</button>
                </td>
            </tr>
        `;
    }).join('');
}
