/**
 * Server Details Modal Component
 * Handles fetching and rendering storage/cpu/ram history
 */

let modalInstance = null;
let currentDeviceId = null;
let charts = {};

export function initServerModal() {
    const el = document.getElementById('serverDetailsModal');
    if (el && window.bootstrap) {
        modalInstance = new window.bootstrap.Modal(el);

        // Range selector listener
        document.getElementById('server-modal-range')?.addEventListener('change', (e) => {
            if (currentDeviceId) loadServerMetrics(currentDeviceId, e.target.value);
        });
    }
}

export function openServerModal(deviceId) {
    if (!modalInstance) initServerModal();
    if (!modalInstance) return;

    currentDeviceId = deviceId;
    modalInstance.show();

    // Reset/Loading state
    document.getElementById('server-modal-ip').textContent = '-';
    document.getElementById('server-modal-status').textContent = 'Loading...';
    document.getElementById('server-modal-uptime').textContent = '-';
    const osEl = document.getElementById('server-modal-os');
    if (osEl) osEl.textContent = '-';
    const lastSeenEl = document.getElementById('server-modal-last-seen');
    if (lastSeenEl) lastSeenEl.textContent = '-';

    // Default range
    const range = document.getElementById('server-modal-range')?.value || '24h';
    loadServerMetrics(deviceId, range);
}

async function loadServerMetrics(deviceId, range) {
    try {
        const res = await fetch(`/api/server/${deviceId}/metrics?range=${range}`);
        const data = await res.json();

        if (data.error) throw new Error(data.error);

        // Update Header
        document.getElementById('server-modal-title').textContent = data.device_name || 'Server Details';
        document.getElementById('server-modal-ip').textContent = data.ip;

        // Format uptime
        document.getElementById('server-modal-uptime').textContent = formatUptime(data.uptime);

        // OS info
        const osEl = document.getElementById('server-modal-os');
        if (osEl) {
            const osParts = [data.os?.name, data.os?.version, data.os?.arch].filter(Boolean);
            osEl.textContent = osParts.length ? osParts.join(' ') : '-';
        }

        // Last seen
        const lastSeenEl = document.getElementById('server-modal-last-seen');
        if (lastSeenEl) {
            lastSeenEl.textContent = data.last_seen ? new Date(data.last_seen).toLocaleString() : '-';
        }

        // Render Charts
        renderChart('chart-server-cpu', 'CPU Usage (%)', data.labels, [{ label: 'CPU', data: data.cpu, color: '#0d6efd' }]);
        renderChart('chart-server-mem', 'Memory Usage (%)', data.labels, [{ label: 'Memory', data: data.memory, color: '#6610f2' }]);
        renderChart('chart-server-disk', 'Disk Usage (%)', data.labels, [{ label: 'Disk', data: data.disk, color: '#dc3545' }]);
        renderChart('chart-server-net', 'Network IO (MB)', data.labels, [
            { label: 'In', data: data.net_in.map(v => (v || 0) / (1024 * 1024)), color: '#20c997' },
            { label: 'Out', data: data.net_out.map(v => (v || 0) / (1024 * 1024)), color: '#fd7e14' }
        ], 'MB');

        // Update Status Badge based on latest metrics
        updateStatusBadge(data);

    } catch (e) {
        console.error("Server Metrics Error:", e);
        document.getElementById('server-modal-status').textContent = 'Error loading data';
        document.getElementById('server-modal-status').className = 'fw-bold text-danger';
    }
}

function renderChart(canvasId, label, labels, datasets, unit = '%') {
    const ctx = document.getElementById(canvasId)?.getContext('2d');
    if (!ctx) return;

    labels = Array.isArray(labels) ? labels : [];

    if (charts[canvasId]) {
        charts[canvasId].destroy();
    }

    const labelDates = (labels || []).map(t => new Date(t));
    const spanMs = labelDates.length > 1 ? (labelDates[labelDates.length - 1] - labelDates[0]) : 0;
    const showDate = spanMs > (36 * 60 * 60 * 1000);

    charts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets.map(ds => ({
                label: ds.label,
                data: ds.data,
                borderColor: ds.color,
                backgroundColor: ds.color + '10',
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: datasets.length === 1,
                tension: 0.3
            }))
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: datasets.length > 1, labels: { color: '#fff', boxWidth: 10 } },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const idx = items?.[0]?.dataIndex ?? 0;
                            const ts = labels?.[idx];
                            return ts ? new Date(ts).toLocaleString() : '';
                        },
                        label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} ${unit}`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: label.includes('Usage') ? 100 : undefined,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#888' }
                },
                x: {
                    display: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 6,
                        callback: (value, index) => {
                            const ts = labels?.[index];
                            if (!ts) return '';
                            const dt = new Date(ts);
                            return showDate
                                ? dt.toLocaleDateString()
                                : dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                        }
                    }
                }
            }
        }
    });
}

function updateStatusBadge(data) {
    const el = document.getElementById('server-modal-status');
    const status = data.health || 'Offline';
    let cls = 'text-secondary';
    if (status === 'Healthy') cls = 'text-success';
    else if (status === 'Warning') cls = 'text-warning';
    else if (status === 'Critical') cls = 'text-danger';

    el.textContent = status;
    el.className = `fw-bold ${cls}`;
}

function formatUptime(uptime) {
    if (!uptime || uptime === 'N/A') return '-';
    const secs = parseInt(uptime);
    if (!isNaN(secs)) {
        const days = Math.floor(secs / 86400);
        const hours = Math.floor((secs % 86400) / 3600);
        return `${days}d ${hours}h`;
    }
    return uptime;
}
