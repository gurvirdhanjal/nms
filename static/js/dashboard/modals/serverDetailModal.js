let modalInstance = null;
let currentDeviceId = null;
let charts = {};
let refreshTimer = null;
let rangeButtons = null;

export function initServerModal() {
    const el = document.getElementById('serverDetailsModal');
    if (el && window.bootstrap) {
        modalInstance = new window.bootstrap.Modal(el);

        rangeButtons = Array.from(document.querySelectorAll('.server-range-toggle [data-range]'));
        if (rangeButtons.length > 0) {
            rangeButtons.forEach(btn => {
                btn.addEventListener('click', () => {
                    setActiveRange(btn.dataset.range || '24h');
                    if (currentDeviceId) loadServerMetrics(currentDeviceId, getActiveRange());
                });
            });
        }

        if (!el.dataset.bound) {
            el.dataset.bound = 'true';
            el.addEventListener('hidden.bs.modal', () => {
                Object.values(charts).forEach(chart => chart?.destroy());
                charts = {};
                currentDeviceId = null;
                if (refreshTimer) {
                    clearInterval(refreshTimer);
                    refreshTimer = null;
                }
            });
        }
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

    // Default range (reset to 24h on open usually, or keep last? let's keep logic simple)
    // If we want to reset:
    // if (rangeDropdown) rangeDropdown.setValue('24h');

    const range = getActiveRange();
    loadServerMetrics(deviceId, range);

    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
        if (currentDeviceId) {
            const r = getActiveRange();
            loadServerMetrics(currentDeviceId, r);
        }
    }, 30000);
}

function getActiveRange() {
    const active = rangeButtons?.find(btn => btn.classList.contains('active'));
    return active?.dataset.range || '24h';
}

function setActiveRange(range) {
    if (!rangeButtons || rangeButtons.length === 0) return;
    rangeButtons.forEach(btn => {
        if (btn.dataset.range === range) btn.classList.add('active');
        else btn.classList.remove('active');
    });
}

async function loadServerMetrics(deviceId, range) {
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 8000);
        const res = await fetch(`/api/server/${deviceId}/metrics?range=${range}`, { signal: controller.signal });
        clearTimeout(timer);
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

        // Downsample on the client as a safety net (prevents UI freezes if payload is large)
        let labels = Array.isArray(data.labels) ? data.labels : [];
        let cpu = Array.isArray(data.cpu) ? data.cpu : [];
        let memory = Array.isArray(data.memory) ? data.memory : [];
        let disk = Array.isArray(data.disk) ? data.disk : [];
        let netIn = Array.isArray(data.net_in) ? data.net_in : [];
        let netOut = Array.isArray(data.net_out) ? data.net_out : [];

        const hasAnyValue = (arr) => Array.isArray(arr) && arr.some(v => v !== null && v !== undefined && !Number.isNaN(v));
        const isEmpty = labels.length === 0 || !(
            hasAnyValue(cpu) ||
            hasAnyValue(memory) ||
            hasAnyValue(disk) ||
            hasAnyValue(netIn) ||
            hasAnyValue(netOut)
        );
        setChartEmptyState('chart-server-cpu', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-server-mem', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-server-disk', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-server-net', isEmpty, 'No telemetry in range');
        if (isEmpty) {
            updateStatusBadge({ health: data.health || 'Offline' });
            return;
        }

        const maxPoints = range === '15m' ? 120 : range === '1h' ? 120 : range === '6h' ? 240 : range === '7d' ? 336 : 240;
        if (labels.length > maxPoints) {
            const step = Math.ceil(labels.length / maxPoints);
            const sample = (arr) => arr.filter((_, i) => i % step === 0);
            labels = sample(labels);
            cpu = sample(cpu);
            memory = sample(memory);
            disk = sample(disk);
            netIn = sample(netIn);
            netOut = sample(netOut);
        }

        // Render Charts
        renderChart('chart-server-cpu', 'CPU Usage (%)', labels, [{ label: 'CPU', data: cpu, color: '#0d6efd' }], '%', {
            thresholds: [
                { from: 0, to: 60, color: 'rgba(0, 255, 136, 0.08)' },
                { from: 60, to: 80, color: 'rgba(255, 170, 0, 0.12)' },
                { from: 80, to: 100, color: 'rgba(255, 59, 92, 0.16)' }
            ]
        });
        renderChart('chart-server-mem', 'Memory Usage (%)', labels, [{ label: 'Memory', data: memory, color: '#6610f2' }], '%', {
            thresholds: [
                { from: 0, to: 75, color: 'rgba(0, 255, 136, 0.06)' },
                { from: 75, to: 90, color: 'rgba(255, 170, 0, 0.12)' },
                { from: 90, to: 100, color: 'rgba(255, 59, 92, 0.18)' }
            ]
        });
        renderChart('chart-server-disk', 'Disk Usage (%)', labels, [{ label: 'Disk', data: disk, color: '#dc3545' }]);
        renderChart('chart-server-net', 'Network IO (MB)', labels, [
            { label: 'In', data: netIn.map(v => (v || 0) / (1024 * 1024)), color: '#20c997' },
            { label: 'Out', data: netOut.map(v => (v || 0) / (1024 * 1024)), color: '#fd7e14' }
        ], 'MB');

        updateMetricSummaries({
            cpu,
            memory,
            disk,
            memory_detail: data.memory_detail || {},
            disk_detail: data.disk_detail || {}
        });

        // Update Status Badge based on latest metrics
        updateStatusBadge(data);

        // Render Enhanced Metrics
        renderAlertsBanner(data.alerts || []);
        renderLoadAverage(data.load_average || {});
        renderSwapUsage(data.swap || {});
        renderProcessesAndConnections(data.processes || {}, data.network_connections || {});
        renderDiskIO(data.disk_io || {});
        renderTopProcesses(data.top_processes || []);

    } catch (e) {
        console.error("Server Metrics Error:", e);
        document.getElementById('server-modal-status').textContent = e.name === 'AbortError'
            ? 'Timeout loading data'
            : 'Error loading data';
        document.getElementById('server-modal-status').className = 'fw-bold text-danger';
    }
}

function setChartEmptyState(canvasId, isEmpty, message) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const wrapper = canvas.parentElement;
    if (!wrapper) return;
    wrapper.style.position = 'relative';

    let msg = wrapper.querySelector('.chart-empty-state');
    if (!msg) {
        msg = document.createElement('div');
        msg.className = 'chart-empty-state';
        msg.style.position = 'absolute';
        msg.style.inset = '0';
        msg.style.display = 'flex';
        msg.style.alignItems = 'center';
        msg.style.justifyContent = 'center';
        msg.style.color = '#6c757d';
        msg.style.fontSize = '0.9rem';
        msg.style.background = 'rgba(0,0,0,0.25)';
        msg.style.borderRadius = '12px';
        msg.style.pointerEvents = 'none';
        wrapper.appendChild(msg);
    }
    msg.textContent = message || 'No data';
    msg.style.opacity = isEmpty ? '1' : '0';
    msg.style.visibility = isEmpty ? 'visible' : 'hidden';
    canvas.style.opacity = isEmpty ? '0.25' : '1';
}

function renderChart(canvasId, label, labels, datasets, unit = '%', extras = {}) {
    const ctx = document.getElementById(canvasId)?.getContext('2d');
    if (!ctx) return;

    labels = Array.isArray(labels) ? labels : [];

    const labelDates = (labels || []).map(t => new Date(t));
    const spanMs = labelDates.length > 1 ? (labelDates[labelDates.length - 1] - labelDates[0]) : 0;
    const showDate = spanMs > (36 * 60 * 60 * 1000);

    const thresholdBands = Array.isArray(extras.thresholds) ? extras.thresholds : [];
    const thresholdPlugin = {
        id: 'thresholdBands',
        beforeDraw(chart, args, opts) {
            const bands = Array.isArray(opts) ? opts : [];
            if (bands.length === 0) return;
            const yScale = chart.scales?.y;
            const chartArea = chart.chartArea;
            if (!yScale || !chartArea) return;
            const ctx = chart.ctx;
            bands.forEach(band => {
                const from = band.from ?? 0;
                const to = band.to ?? 0;
                const color = band.color || 'rgba(255,255,255,0.04)';
                const yTop = yScale.getPixelForValue(to);
                const yBottom = yScale.getPixelForValue(from);
                ctx.save();
                ctx.fillStyle = color;
                ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBottom - yTop);
                ctx.restore();
            });
        }
    };
    const chartData = {
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
    };

    if (charts[canvasId]) {
        charts[canvasId].data.labels = chartData.labels;
        charts[canvasId].data.datasets = chartData.datasets;
        charts[canvasId].options.plugins.thresholdBands = thresholdBands;
        if (charts[canvasId].options?.scales?.y) {
            charts[canvasId].options.scales.y.max = label.includes('Usage') ? 100 : undefined;
        }
        if (charts[canvasId].options?.scales?.x?.ticks) {
            charts[canvasId].options.scales.x.ticks.callback = (value, index) => {
                const ts = labels?.[index];
                if (!ts) return '';
                const dt = new Date(ts);
                return showDate
                    ? dt.toLocaleDateString()
                    : dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            };
        }
        if (charts[canvasId].options?.plugins?.tooltip?.callbacks) {
            charts[canvasId].options.plugins.tooltip.callbacks.title = (items) => {
                const idx = items?.[0]?.dataIndex ?? 0;
                const ts = labels?.[idx];
                return ts ? new Date(ts).toLocaleString() : '';
            };
        }
        charts[canvasId].update('none');
        return;
    }

    charts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: chartData,
        plugins: [thresholdPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                thresholdBands,
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

function updateMetricSummaries({ cpu, memory, disk, memory_detail, disk_detail }) {
    const cpuSummary = document.getElementById('server-cpu-summary');
    const memSummary = document.getElementById('server-mem-summary');
    const memPeak = document.getElementById('server-mem-peak');
    const diskSummary = document.getElementById('server-disk-summary');
    const diskFree = document.getElementById('server-disk-free');

    const cpuCurrent = getLastValue(cpu);
    const cpuAvg = avg(cpu);
    const cpuPeak = max(cpu);
    if (cpuSummary) {
        cpuSummary.textContent = `Current: ${fmtPct(cpuCurrent)} | Avg: ${fmtPct(cpuAvg)} | Peak: ${fmtPct(cpuPeak)}`;
    }

    const memCurrent = getLastValue(memory);
    const memPeakVal = max(memory);
    if (memSummary) {
        const used = memory_detail?.used_gb;
        const total = memory_detail?.total_gb;
        const memDetail = (used !== null && used !== undefined && total)
            ? `${fmtPct(memCurrent)} (${used} GB / ${total} GB)`
            : `${fmtPct(memCurrent)}`;
        memSummary.textContent = memDetail;
    }
    if (memPeak) {
        memPeak.textContent = `Peak Today: ${fmtPct(memPeakVal)}`;
    }

    const diskCurrent = getLastValue(disk);
    if (diskSummary) {
        diskSummary.textContent = `${fmtPct(diskCurrent)} Used`;
    }
    if (diskFree) {
        const free = disk_detail?.free_gb;
        diskFree.textContent = free !== null && free !== undefined ? `Free: ${free} GB` : 'Free: -';
    }

    updateMetricStatus('server-cpu-status', cpuCurrent, { warning: 70, critical: 85 });
    updateMetricStatus('server-mem-status', memCurrent, { warning: 75, critical: 90 });
    updateMetricStatus('server-disk-status', diskCurrent, { warning: 80, critical: 90 });
}

function updateMetricStatus(elementId, value, thresholds) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const v = Number(value);
    let status = 'Healthy';
    let cls = 'status-healthy';
    if (!Number.isFinite(v)) {
        el.textContent = '-';
        el.className = 'metric-status';
        return;
    }
    if (v >= thresholds.critical) {
        status = 'Critical';
        cls = 'status-critical';
    } else if (v >= thresholds.warning) {
        status = 'Warning';
        cls = 'status-warning';
    }
    el.textContent = status;
    el.className = `metric-status ${cls}`;
}

function getLastValue(values) {
    if (!Array.isArray(values)) return null;
    for (let i = values.length - 1; i >= 0; i--) {
        const v = values[i];
        if (v !== null && v !== undefined && !Number.isNaN(v)) return v;
    }
    return null;
}

function avg(values) {
    const nums = (values || []).filter(v => v !== null && v !== undefined && !Number.isNaN(v));
    if (nums.length === 0) return null;
    return nums.reduce((a, b) => a + b, 0) / nums.length;
}

function max(values) {
    const nums = (values || []).filter(v => v !== null && v !== undefined && !Number.isNaN(v));
    if (nums.length === 0) return null;
    return Math.max(...nums);
}

function fmtPct(value) {
    if (value === null || value === undefined || Number.isNaN(value)) return '-';
    return `${Number(value).toFixed(1)}%`;
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

// ============ Enhanced Metrics Rendering Functions ============

function renderAlertsBanner(alerts) {
    const banner = document.getElementById('server-alerts-banner');
    const alertsList = document.getElementById('server-alerts-list');

    if (!alerts || alerts.length === 0) {
        banner.style.display = 'none';
        return;
    }

    banner.style.display = 'block';
    alertsList.innerHTML = alerts.map(alert => `
        <div class="small mb-1">
            <i class="fas fa-exclamation-circle tactical-text-warning me-2"></i>${alert}
        </div>
    `).join('');
}

function renderLoadAverage(loadAvg) {
    const setLoadValue = (id, value) => {
        const el = document.getElementById(id);
        if (!el) return;

        if (value === null || value === undefined) {
            el.textContent = '-';
            el.className = 'fw-bold fs-5';
            return;
        }

        // Color code based on load (assuming 4-core system as baseline)
        let colorClass = 'text-success';
        if (value > 4.0) colorClass = 'text-danger';
        else if (value > 2.0) colorClass = 'text-warning';

        el.textContent = value.toFixed(2);
        el.className = `fw-bold fs-5 ${colorClass}`;
    };

    setLoadValue('load-avg-1min', loadAvg['1min']);
    setLoadValue('load-avg-5min', loadAvg['5min']);
    setLoadValue('load-avg-15min', loadAvg['15min']);
}

function renderSwapUsage(swap) {
    const usedText = document.getElementById('swap-used-text');
    const percentText = document.getElementById('swap-percent-text');
    const totalText = document.getElementById('swap-total-text');
    const progressBar = document.getElementById('swap-progress-bar');

    if (swap.total_mb === null || swap.total_mb === undefined || swap.total_mb === 0) {
        usedText.textContent = 'No Swap';
        percentText.textContent = '-';
        totalText.textContent = '0 MB';
        progressBar.style.width = '0%';
        progressBar.className = 'progress-bar';
        return;
    }

    const percent = swap.percent || 0;
    const used = swap.used_mb || 0;
    const total = swap.total_mb || 0;

    usedText.textContent = `${used.toFixed(0)} MB`;
    percentText.textContent = `${percent.toFixed(1)}%`;
    totalText.textContent = `${total.toFixed(0)} MB`;
    progressBar.style.width = `${percent}%`;

    // Color code progress bar
    let barClass = 'progress-bar bg-success';
    if (percent > 75) barClass = 'progress-bar bg-danger';
    else if (percent > 50) barClass = 'progress-bar bg-warning';
    progressBar.className = barClass;
}

function renderProcessesAndConnections(processes, connections) {
    const processCount = document.getElementById('process-count');
    const zombieCount = document.getElementById('zombie-count');
    const connectionsTotal = document.getElementById('connections-total');
    const connectionsEstablished = document.getElementById('connections-established');

    processCount.textContent = processes.total !== null && processes.total !== undefined ? processes.total : '-';
    zombieCount.textContent = processes.zombie !== null && processes.zombie !== undefined ? processes.zombie : '-';
    connectionsTotal.textContent = connections.total !== null && connections.total !== undefined ? connections.total : '-';
    connectionsEstablished.textContent = connections.established !== null && connections.established !== undefined ? connections.established : '-';

    // Highlight zombie processes if any
    if (processes.zombie && processes.zombie > 0) {
        zombieCount.className = 'fw-bold text-danger';
    } else {
        zombieCount.className = 'fw-bold text-success';
    }
}

function renderDiskIO(diskIO) {
    const readCount = document.getElementById('disk-read-count');
    const writeCount = document.getElementById('disk-write-count');
    const readMB = document.getElementById('disk-read-mb');
    const writeMB = document.getElementById('disk-write-mb');

    const formatCount = (val) => val !== null && val !== undefined ? val.toLocaleString() : '-';
    const formatMB = (bytes) => bytes !== null && bytes !== undefined ? (bytes / (1024 * 1024)).toFixed(2) : '-';

    readCount.textContent = formatCount(diskIO.read_count);
    writeCount.textContent = formatCount(diskIO.write_count);
    readMB.textContent = formatMB(diskIO.read_bytes);
    writeMB.textContent = formatMB(diskIO.write_bytes);
}

function renderTopProcesses(processes) {
    const tbody = document.getElementById('top-processes-body');

    if (!processes || processes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary p-3">No process data available</td></tr>';
        return;
    }

    tbody.innerHTML = processes.slice(0, 5).map(proc => {
        const cpuClass = proc.cpu_percent > 50 ? 'text-danger' : proc.cpu_percent > 25 ? 'text-warning' : '';
        const memClass = proc.memory_percent > 50 ? 'text-danger' : proc.memory_percent > 25 ? 'text-warning' : '';

        return `
            <tr>
                <td>${proc.name || '-'}</td>
                <td>${proc.pid || '-'}</td>
                <td class="${cpuClass}">${proc.cpu_percent !== null && proc.cpu_percent !== undefined ? proc.cpu_percent.toFixed(1) + '%' : '-'}</td>
                <td class="${memClass}">${proc.memory_percent !== null && proc.memory_percent !== undefined ? proc.memory_percent.toFixed(1) + '%' : '-'}</td>
                <td>${proc.status || '-'}</td>
            </tr>
        `;
    }).join('');
}
