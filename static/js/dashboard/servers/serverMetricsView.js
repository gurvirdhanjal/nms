import { patchKeyedTableRows } from '../domPatch.js';

function toFiniteNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function parseApiResponse(response) {
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        return await response.json();
    }

    const text = await response.text();
    try {
        return JSON.parse(text);
    } catch (_error) {
        const statusText = `${response.status} ${response.statusText}`.trim();
        const snippet = text ? text.replace(/\s+/g, ' ').trim().slice(0, 160) : '';
        throw new Error(
            `Expected JSON response but received non-JSON (${statusText})${snippet ? `: ${snippet}` : ''}`
        );
    }
}

function getApiErrorMessage(data, fallback = 'Request failed') {
    if (!data) return fallback;
    if (typeof data === 'string') return data;
    if (typeof data.error === 'string') return data.error;
    if (data.error && typeof data.error.message === 'string') return data.error.message;
    if (typeof data.message === 'string') return data.message;
    return fallback;
}

function parseUtcDate(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    const text = String(value).trim();
    if (!text) return null;
    const normalized = /z$|[+-]\d{2}:\d{2}$/i.test(text) ? text : `${text}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateTime(value) {
    const date = parseUtcDate(value);
    return date ? date.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '-';
}

function formatMetricValue(value, unit = '') {
    const numeric = toFiniteNumber(value);
    if (numeric === null) return '-';
    return `${numeric.toFixed(1)}${unit}`.trim();
}

function formatCount(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toLocaleString() : '-';
}

function formatUptime(uptime, uptimeSeconds = null) {
    const numeric = toFiniteNumber(uptimeSeconds ?? uptime);
    if (numeric !== null) {
        const total = Math.max(0, Math.floor(numeric));
        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        if (days > 0) return `${days}d ${hours}h ${minutes}m`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }
    if (!uptime || uptime === 'N/A') return '-';
    return String(uptime);
}

function formatHardwareSpecs(specs) {
    if (!specs || typeof specs !== 'object') return '-';

    const cpuModel = typeof specs.cpu_model === 'string' ? specs.cpu_model.trim() : '';
    const physicalCores = toFiniteNumber(specs.cpu_physical_cores);
    const logicalCores = toFiniteNumber(specs.cpu_logical_cores);
    const memoryGb = toFiniteNumber(specs.memory_total_gb);
    const diskGb = toFiniteNumber(specs.disk_total_gb);
    const parts = [];

    if (cpuModel) {
        let cpuText = cpuModel;
        if (physicalCores !== null && logicalCores !== null && physicalCores !== logicalCores) {
            cpuText += ` (${physicalCores}C/${logicalCores}T)`;
        } else if (logicalCores !== null) {
            cpuText += ` (${logicalCores} cores)`;
        }
        parts.push(cpuText);
    }
    if (memoryGb !== null) parts.push(`${memoryGb.toFixed(memoryGb >= 100 ? 0 : 1)} GB RAM`);
    if (diskGb !== null) parts.push(`${diskGb.toFixed(diskGb >= 100 ? 0 : 1)} GB Disk`);
    return parts.length ? parts.join(' | ') : '-';
}

function detectRateUnit(values) {
    const maxValue = (Array.isArray(values) ? values : [])
        .map((value) => toFiniteNumber(value))
        .filter((value) => value !== null)
        .reduce((max, value) => Math.max(max, value), 0);

    if (maxValue >= 1024 * 1024 * 1024) {
        return { divisor: 1024 * 1024 * 1024, label: 'GB/s' };
    }
    if (maxValue >= 1024 * 1024) {
        return { divisor: 1024 * 1024, label: 'MB/s' };
    }
    return { divisor: 1024, label: 'KB/s' };
}

function formatRate(value, unitInfo = null, precision = 2) {
    const numeric = toFiniteNumber(value);
    if (numeric === null) return '-';
    const info = unitInfo || detectRateUnit([numeric]);
    return `${(numeric / info.divisor).toFixed(precision)} ${info.label}`;
}

function truncateMiddle(value, maxLength = 34) {
    const text = String(value ?? '');
    if (text.length <= maxLength) return text;
    const head = Math.ceil((maxLength - 3) / 2);
    const tail = Math.floor((maxLength - 3) / 2);
    return `${text.slice(0, head)}...${text.slice(text.length - tail)}`;
}

const TELEMETRY_CACHE_TTL_MS = 20000;
const TELEMETRY_RANGE_ORDER = ['15m', '1h', '6h', '24h', '7d'];

export function buildTelemetryCacheKey(deviceId, range) {
    return `${deviceId}:${range || '24h'}`;
}

export function buildTelemetryPrefetchOrder(activeRange) {
    return TELEMETRY_RANGE_ORDER.filter((range) => range !== (activeRange || '24h'));
}

export function calculateSeriesStats(values) {
    const points = Array.isArray(values)
        ? values
            .map((value, index) => ({ value: toFiniteNumber(value), index }))
            .filter((point) => point.value !== null)
        : [];

    if (points.length === 0) {
        return { current: null, min: null, minIndex: null, avg: null, max: null, maxIndex: null };
    }

    const current = points[points.length - 1].value;
    let minPoint = points[0];
    let maxPoint = points[0];
    let total = 0;
    points.forEach((point) => {
        total += point.value;
        if (point.value < minPoint.value) minPoint = point;
        if (point.value > maxPoint.value) maxPoint = point;
    });
    return {
        current,
        min: minPoint.value,
        minIndex: minPoint.index,
        avg: total / points.length,
        max: maxPoint.value,
        maxIndex: maxPoint.index,
    };
}

export function buildMarkerDatasets({ labels, values, baseLabel, color }) {
    const stats = calculateSeriesStats(values);
    const datasets = [];
    if (stats.minIndex === null || stats.maxIndex === null) {
        return datasets;
    }

    const makeData = (index, value) => labels.map((_label, labelIndex) => (labelIndex === index ? value : null));
    const markerConfig = {
        showLine: false,
        pointRadius: 4,
        pointHoverRadius: 5,
        pointBackgroundColor: color,
        pointBorderColor: '#ffffff',
        pointBorderWidth: 1.25,
        borderWidth: 0,
        fill: false,
        isMarker: true,
    };

    if (stats.minIndex === stats.maxIndex) {
        datasets.push({
            ...markerConfig,
            label: `${baseLabel} Min/Max`,
            data: makeData(stats.minIndex, stats.min),
        });
        return datasets;
    }

    datasets.push({
        ...markerConfig,
        label: `${baseLabel} Min`,
        data: makeData(stats.minIndex, stats.min),
        pointStyle: 'triangle',
    });
    datasets.push({
        ...markerConfig,
        label: `${baseLabel} Max`,
        data: makeData(stats.maxIndex, stats.max),
        pointStyle: 'rectRot',
    });
    return datasets;
}

function buildThresholdLineDatasets(labels, thresholdConfig, colors = {}) {
    if (!labels.length || !thresholdConfig || !thresholdConfig.enabled) return [];
    const warning = toFiniteNumber(thresholdConfig.warning);
    const critical = toFiniteNumber(thresholdConfig.critical);
    const datasets = [];
    if (warning !== null) {
        datasets.push({
            label: 'Warning threshold',
            data: labels.map(() => warning),
            borderColor: colors.warning || 'rgba(255, 170, 0, 0.7)',
            borderWidth: 1,
            pointRadius: 0,
            fill: false,
            borderDash: [6, 4],
            tension: 0,
            isThresholdLine: true,
        });
    }
    if (critical !== null) {
        datasets.push({
            label: 'Critical threshold',
            data: labels.map(() => critical),
            borderColor: colors.critical || 'rgba(255, 59, 92, 0.8)',
            borderWidth: 1,
            pointRadius: 0,
            fill: false,
            borderDash: [6, 4],
            tension: 0,
            isThresholdLine: true,
        });
    }
    return datasets;
}

function sortThresholdMetrics(metrics) {
    return Object.entries(metrics || {}).sort((left, right) => {
        const leftCategory = String(left[1]?.category || '');
        const rightCategory = String(right[1]?.category || '');
        if (leftCategory !== rightCategory) return leftCategory.localeCompare(rightCategory);
        const leftLabel = String(left[1]?.label || left[0]);
        const rightLabel = String(right[1]?.label || right[0]);
        return leftLabel.localeCompare(rightLabel);
    });
}

function sortProcesses(processes, mode) {
    const valueKey = mode === 'cpu' ? 'cpu_percent' : 'memory_percent';
    return [...(processes || [])].sort((left, right) => {
        const rightValue = toFiniteNumber(right?.[valueKey]) ?? -1;
        const leftValue = toFiniteNumber(left?.[valueKey]) ?? -1;
        if (rightValue !== leftValue) return rightValue - leftValue;
        return (toFiniteNumber(right?.cpu_percent) ?? -1) - (toFiniteNumber(left?.cpu_percent) ?? -1);
    });
}

function buildChartGradient(ctx, hexColor, chartArea) {
    const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    gradient.addColorStop(0, `${hexColor}40`);
    gradient.addColorStop(1, `${hexColor}02`);
    return gradient;
}

export function createServerMetricsView({ root, prefix }) {
    const charts = {};
    const payloadCache = new Map();
    const prefetchPromises = new Map();
    let currentDeviceId = null;
    let currentRange = '24h';
    let thresholdState = { version: null, metrics: {} };
    let processSortMode = 'memory';
    let currentPayload = null;
    let currentLoadPromise = null;
    let currentLoadKey = null;
    let currentAbortController = null;
    let initialDataLoaded = false;
    let sharedHoveredIndex = null;
    const registeredCharts = [];

    // Disk I/O sparkline state
    const diskReadHistory = [];
    const diskWriteHistory = [];
    const SPARK_MAX_POINTS = 30;
    const sparkCharts = {};

    const crosshairPlugin = {
        id: `${prefix}-crosshair`,
        afterDraw(chart) {
            if (sharedHoveredIndex === null || !chart.chartArea) return;
            const { ctx: c, chartArea, scales } = chart;
            const xScale = scales?.x;
            if (!xScale || !chart.data.labels.length) return;
            const label = chart.data.labels[sharedHoveredIndex];
            if (label == null) return;
            const x = xScale.getPixelForValue(label);
            if (x < chartArea.left || x > chartArea.right) return;
            c.save();
            c.setLineDash([4, 4]);
            c.strokeStyle = 'rgba(255,255,255,0.18)';
            c.lineWidth = 1;
            c.beginPath();
            c.moveTo(x, chartArea.top);
            c.lineTo(x, chartArea.bottom);
            c.stroke();
            c.restore();
        },
    };

    const element = (name) => root?.querySelector(`#${prefix}-${name}`) || null;

    function setText(name, value) {
        const target = element(name);
        if (target) target.textContent = value;
    }

    function setOpenDetailsLink(deviceId) {
        const link = document.getElementById('server-modal-open-page');
        if (link) link.href = `/devices/${deviceId}/server-monitoring`;
    }

    function setChartEmptyState(canvasId, isEmpty, message) {
        const canvas = element(canvasId);
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

    function renderChart({
        canvasId,
        labels,
        series,
        unit = '',
        yAxisLabel = '',
        thresholds = [],
        thresholdConfig = null,
        forceMax = null,
        tickFormatter = null,
        tooltipLabelFormatter = null,
    }) {
        const canvas = element(canvasId);
        const ctx = canvas?.getContext('2d');
        if (!ctx || !window.Chart) return;

        const labelDates = labels.map((label) => parseUtcDate(label)).filter(Boolean);
        const spanMs = labelDates.length > 1 ? (labelDates[labelDates.length - 1] - labelDates[0]) : 0;
        const showDate = spanMs > (36 * 60 * 60 * 1000);

        const thresholdPlugin = {
            id: `${prefix}-${canvasId}-threshold-bands`,
            beforeDraw(chart) {
                const yScale = chart.scales?.y;
                const chartArea = chart.chartArea;
                if (!yScale || !chartArea || !thresholds.length) return;
                const chartCtx = chart.ctx;
                thresholds.forEach((band) => {
                    const from = band.from ?? 0;
                    const to = band.to ?? 0;
                    if (to <= from) return;
                    const yTop = yScale.getPixelForValue(to);
                    const yBottom = yScale.getPixelForValue(from);
                    chartCtx.save();
                    chartCtx.fillStyle = band.color || 'rgba(255,255,255,0.04)';
                    chartCtx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBottom - yTop);
                    chartCtx.restore();
                });
            },
        };

        const datasets = [];
        series.forEach((item) => {
            const itemColor = item.color;
            datasets.push({
                label: item.label,
                data: item.data,
                borderColor: itemColor,
                backgroundColor: (context) => {
                    const { ctx: c, chartArea } = context.chart;
                    if (!chartArea) return `${itemColor}10`;
                    return buildChartGradient(c, itemColor, chartArea);
                },
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: series.length === 1,
                tension: 0.3,
            });
            buildMarkerDatasets({
                labels,
                values: item.data,
                baseLabel: item.label,
                color: itemColor,
            }).forEach((markerDataset) => datasets.push(markerDataset));
        });
        buildThresholdLineDatasets(labels, thresholdConfig).forEach((dataset) => datasets.push(dataset));

        const buildXTick = (_value, index) => {
            const ts = labels[index];
            if (!ts) return '';
            const dt = parseUtcDate(ts);
            if (!dt) return '';
            return showDate
                ? dt.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' })
                : dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
        };

        const formatTick = tickFormatter || ((value) => {
            const numeric = toFiniteNumber(value);
            return numeric === null ? '-' : `${numeric.toFixed(1)}${unit ? ` ${unit}` : ''}`.trim();
        });
        const formatTooltip = tooltipLabelFormatter || ((ctx) => {
            const raw = toFiniteNumber(ctx.parsed.y);
            return raw === null
                ? `${ctx.dataset.label}: -`
                : `${ctx.dataset.label}: ${raw.toFixed(2)}${unit ? ` ${unit}` : ''}`.trim();
        });

        const seriesStats = series.map((item) => calculateSeriesStats(item.data));

        const options = {
            responsive: true,
            maintainAspectRatio: false,
            animation: initialDataLoaded ? false : { duration: 600, easing: 'easeOutQuart' },
            onHover: (evt, elements, chart) => {
                const newIndex = elements?.[0]?.index ?? null;
                if (newIndex !== sharedHoveredIndex) {
                    sharedHoveredIndex = newIndex;
                    registeredCharts.forEach((c) => { if (c !== chart) c.update('none'); });
                }
            },
            plugins: {
                legend: {
                    display: series.length > 1 || datasets.some((dataset) => dataset.isMarker),
                    labels: {
                        color: '#fff',
                        boxWidth: 10,
                        filter: (legendItem, chartData) => !chartData.datasets[legendItem.datasetIndex]?.isThresholdLine,
                    },
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    filter: (ctx) => !ctx.dataset?.isThresholdLine,
                    callbacks: {
                        title: (items) => {
                            const index = items?.[0]?.dataIndex ?? 0;
                            return formatDateTime(labels[index]);
                        },
                        label: formatTooltip,
                        afterBody: () => {
                            const lines = [];
                            seriesStats.forEach((stats, i) => {
                                if (stats.avg !== null) lines.push(`${series[i].label} Avg: ${stats.avg.toFixed(1)}${unit ? ` ${unit}` : ''}`);
                                if (stats.max !== null) lines.push(`${series[i].label} Max: ${stats.max.toFixed(1)}${unit ? ` ${unit}` : ''}`);
                            });
                            if (thresholdConfig?.enabled) {
                                const warn = toFiniteNumber(thresholdConfig?.warning);
                                const crit = toFiniteNumber(thresholdConfig?.critical);
                                if (warn !== null) lines.push(`⚠ Warn: ${warn}${unit ? ` ${unit}` : ''}`);
                                if (crit !== null) lines.push(`⛔ Crit: ${crit}${unit ? ` ${unit}` : ''}`);
                            }
                            return lines;
                        },
                    },
                },
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: forceMax,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 5,
                        callback: formatTick,
                    },
                    title: {
                        display: Boolean(yAxisLabel),
                        text: yAxisLabel,
                        color: '#9aa4b2',
                        font: { size: 11, weight: '600' },
                    },
                },
                x: {
                    display: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#888',
                        maxTicksLimit: 6,
                        autoSkip: true,
                        maxRotation: 0,
                        callback: buildXTick,
                    },
                    title: {
                        display: true,
                        text: 'Time',
                        color: '#9aa4b2',
                        font: { size: 11, weight: '600' },
                    },
                },
            },
        };

        if (charts[canvasId]) {
            charts[canvasId].data.labels = labels;
            charts[canvasId].data.datasets = datasets;
            // Only patch the y-axis label when it actually changes (e.g. KB/s→MB/s on network chart).
            // Avoid reassigning the full options object — Chart.js re-parses all scales/plugins on every update.
            if (yAxisLabel) {
                const yTitle = charts[canvasId].options?.scales?.y?.title;
                if (yTitle && yTitle.text !== yAxisLabel) {
                    yTitle.text = yAxisLabel;
                }
            }
            charts[canvasId].update('none');
            return;
        }

        charts[canvasId] = new window.Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            plugins: [thresholdPlugin, crosshairPlugin],
            options,
        });
        registeredCharts.push(charts[canvasId]);
    }

    function updateMetricStatus(targetId, evaluation) {
        const target = element(targetId);
        if (!target) return;
        const state = evaluation?.state || 'unknown';
        if (state === 'critical') {
            target.textContent = 'Critical';
            target.className = 'metric-status status-critical';
            return;
        }
        if (state === 'warning') {
            target.textContent = 'Warning';
            target.className = 'metric-status status-warning';
            return;
        }
        if (state === 'healthy') {
            target.textContent = 'Healthy';
            target.className = 'metric-status status-healthy';
            return;
        }
        target.textContent = 'Neutral';
        target.className = 'metric-status text-secondary';
    }

    function updateHealthHeader(payload) {
        const statusTarget = element('status');
        const scoreTarget = element('health-score');
        const summaryTarget = element('health-summary');
        const status = payload?.health || 'Offline';

        let className = 'fw-bold text-secondary';
        let badgeClass = 'badge rounded-pill border text-secondary border-secondary';
        if (status === 'Healthy') {
            className = 'fw-bold text-success';
            badgeClass = 'badge rounded-pill border text-success border-success';
        } else if (status === 'Warning') {
            className = 'fw-bold text-warning';
            badgeClass = 'badge rounded-pill border text-warning border-warning';
        } else if (status === 'Critical') {
            className = 'fw-bold text-danger';
            badgeClass = 'badge rounded-pill border text-danger border-danger';
        }

        if (statusTarget) {
            statusTarget.textContent = status;
            statusTarget.className = className;
        }
        if (scoreTarget) {
            const score = toFiniteNumber(payload?.health_score);
            scoreTarget.textContent = score === null ? 'Score -' : `Score ${Math.round(score)}`;
            scoreTarget.className = badgeClass;
        }
        if (summaryTarget) {
            const penalties = Array.isArray(payload?.health_penalties) ? payload.health_penalties : [];
            if (!penalties.length) {
                summaryTarget.textContent = status === 'Offline'
                    ? 'No recent primary telemetry'
                    : 'CPU, memory, disk, and process health within thresholds';
                return;
            }
            const getPenaltyColor = (penalty) => {
                const v = toFiniteNumber(penalty?.value);
                if (v === null) return 'var(--ui-text-dim)';
                const crit = toFiniteNumber(penalty?.critical);
                const warn = toFiniteNumber(penalty?.warning);
                if (crit !== null && v >= crit) return 'var(--ui-danger)';
                if (warn !== null && v >= warn) return 'var(--ui-warning)';
                return 'var(--ui-accent)';
            };
            const getPenaltyPct = (penalty) => {
                const v = toFiniteNumber(penalty?.value);
                const crit = toFiniteNumber(penalty?.critical) || 100;
                return v === null ? 0 : Math.min(100, (v / crit) * 100);
            };
            summaryTarget.innerHTML = penalties.slice(0, 3).map((penalty) => {
                const color = getPenaltyColor(penalty);
                const width = getPenaltyPct(penalty);
                const label = escapeHtml(penalty?.label || penalty?.metric_key || '');
                const value = toFiniteNumber(penalty?.value);
                const unit = escapeHtml(penalty?.unit || '');
                const valStr = value !== null ? `${value.toFixed(1)}${unit}` : '-';
                return `<div style="margin-bottom:4px;">` +
                    `<div style="display:flex;justify-content:space-between;font-size:11px;">` +
                    `<span>${label}</span>` +
                    `<span style="color:${color};font-family:var(--ui-font-mono);">${escapeHtml(valStr)}</span>` +
                    `</div>` +
                    `<div style="height:3px;background:rgba(255,255,255,.08);border-radius:2px;overflow:hidden;">` +
                    `<div style="height:100%;width:${width}%;background:${color};transition:width .4s ease;"></div>` +
                    `</div></div>`;
            }).join('');
        }
    }

    function renderMetricSummaries(payload, evaluations) {
        const cpuStats = calculateSeriesStats(payload.cpu);
        const memStats = calculateSeriesStats(payload.memory);
        const diskStats = calculateSeriesStats(payload.disk);

        const cpuThreshold = payload.thresholds?.metrics?.cpu_usage_pct || {};
        const memoryThreshold = payload.thresholds?.metrics?.memory_usage_pct || {};
        const diskThreshold = payload.thresholds?.metrics?.disk_usage_pct || {};

        setText(
            'cpu-summary',
            `Cur ${formatMetricValue(cpuStats.current, '%')} | Avg ${formatMetricValue(cpuStats.avg, '%')} | Max ${formatMetricValue(cpuStats.max, '%')} | Warn ${formatMetricValue(cpuThreshold.warning, '%')} | Crit ${formatMetricValue(cpuThreshold.critical, '%')}`
        );

        const memoryDetail = payload.memory_detail || {};
        const memoryDescriptor = memoryDetail.used_gb != null && memoryDetail.total_gb != null
            ? ` | ${Number(memoryDetail.used_gb).toFixed(1)} / ${Number(memoryDetail.total_gb).toFixed(1)} GB`
            : '';
        setText(
            'mem-summary',
            `Cur ${formatMetricValue(memStats.current, '%')} | Avg ${formatMetricValue(memStats.avg, '%')} | Max ${formatMetricValue(memStats.max, '%')} | Warn ${formatMetricValue(memoryThreshold.warning, '%')} | Crit ${formatMetricValue(memoryThreshold.critical, '%')}${memoryDescriptor}`
        );

        const diskDetail = payload.disk_detail || {};
        const diskDescriptor = diskDetail.free_gb != null ? ` | Free ${Number(diskDetail.free_gb).toFixed(1)} GB` : '';
        setText(
            'disk-summary',
            `Cur ${formatMetricValue(diskStats.current, '%')} | Avg ${formatMetricValue(diskStats.avg, '%')} | Max ${formatMetricValue(diskStats.max, '%')} | Warn ${formatMetricValue(diskThreshold.warning, '%')} | Crit ${formatMetricValue(diskThreshold.critical, '%')}${diskDescriptor}`
        );

        updateMetricStatus('cpu-status', evaluations?.cpu_usage_pct);
        updateMetricStatus('mem-status', evaluations?.memory_usage_pct);
        updateMetricStatus('disk-status', evaluations?.disk_usage_pct);

        const networkValues = [...(payload.net_in || []), ...(payload.net_out || [])];
        const unitInfo = detectRateUnit(networkValues);
        const inbound = payload.network_summary?.inbound_mb_s || {};
        const outbound = payload.network_summary?.outbound_mb_s || {};
        const scale = unitInfo.label === 'GB/s' ? 1 / 1024 : unitInfo.label === 'MB/s' ? 1 : 1024;
        const renderScaledMb = (value) => {
            const numeric = toFiniteNumber(value);
            if (numeric === null) return '-';
            return `${(numeric * scale).toFixed(2)} ${unitInfo.label}`;
        };

        setText('net-axis-unit', `(${unitInfo.label})`);
        setText('net-current', `In ${renderScaledMb(inbound.current)} | Out ${renderScaledMb(outbound.current)}`);
        setText('net-average', `In ${renderScaledMb(inbound.average)} | Out ${renderScaledMb(outbound.average)}`);
        setText('net-peak', `In ${renderScaledMb(inbound.peak)} | Out ${renderScaledMb(outbound.peak)}`);
    }

    function renderAlertsBanner(alerts) {
        const banner = element('alerts-banner');
        const list = element('alerts-list');
        if (!banner || !list) return;
        if (!alerts || alerts.length === 0) {
            banner.style.display = 'none';
            list.innerHTML = '';
            return;
        }
        banner.style.display = 'block';
        list.innerHTML = alerts.map((alert) => `
            <div class="small mb-1">
                <i class="fas fa-exclamation-circle tactical-text-warning me-2"></i>${escapeHtml(alert)}
            </div>
        `).join('');
    }

    function renderLoadAveragePanel(payload) {
        const isWindows = payload?.os_family === 'windows';
        setText('load-panel-title', isWindows ? 'Processor Queue' : 'Load Average (1/5/15 min)');
        setText('load-panel-note', isWindows ? 'Windows queue pressure and context switching' : 'Linux scheduler pressure');
        setText('load-slot-1-label', isWindows ? 'CPU Queue' : '1min');
        setText('load-slot-2-label', isWindows ? 'Proc Queue' : '5min');
        setText('load-slot-3-label', isWindows ? 'Ctx/s' : '15min');

        const values = isWindows
            ? [
                payload?.queue_metrics?.cpu_queue_length,
                payload?.queue_metrics?.processor_queue_length,
                payload?.processes?.context_switches_per_sec,
              ]
            : [
                payload?.load_average?.['1min'],
                payload?.load_average?.['5min'],
                payload?.load_average?.['15min'],
              ];

        ['load-avg-1min', 'load-avg-5min', 'load-avg-15min'].forEach((id, index) => {
            const target = element(id);
            if (!target) return;
            const numeric = toFiniteNumber(values[index]);
            if (numeric === null) {
                target.textContent = '-';
                target.className = 'fw-bold fs-5 text-secondary';
                return;
            }
            let className = 'fw-bold fs-5 text-success';
            const warningThreshold = isWindows ? (index === 2 ? 50000 : 4) : 2;
            const criticalThreshold = isWindows ? (index === 2 ? 100000 : 10) : 4;
            if (numeric > criticalThreshold) className = 'fw-bold fs-5 text-danger';
            else if (numeric > warningThreshold) className = 'fw-bold fs-5 text-warning';
            target.textContent = isWindows && index === 2 ? formatCount(Math.round(numeric)) : numeric.toFixed(2);
            target.className = className;
        });

        // Trend arrow on 1min vs 15min (Linux only)
        if (!isWindows) {
            const val1 = toFiniteNumber(values[0]);
            const val15 = toFiniteNumber(values[2]);
            const el1min = element('load-avg-1min');
            if (el1min && val1 !== null && val15 !== null) {
                const arrow = val1 > val15 + 0.2 ? '↑' : val1 < val15 - 0.2 ? '↓' : '→';
                const arrowColor = val1 > val15 + 0.2 ? 'var(--ui-warning)' : val1 < val15 - 0.2 ? 'var(--ui-accent)' : 'var(--ui-text-dim)';
                el1min.innerHTML = `${el1min.textContent}&thinsp;<span style="color:${arrowColor};font-size:0.7em;vertical-align:middle;">${arrow}</span>`;
            }
        }
    }

    function renderSwapUsage(swap, label) {
        const usedText = element('swap-used-text');
        const percentText = element('swap-percent-text');
        const totalText = element('swap-total-text');
        const progressBar = element('swap-progress-bar');
        const title = element('paging-panel-title');
        if (!usedText || !percentText || !totalText || !progressBar) return;
        if (title) title.textContent = label || 'Swap Usage';

        if (swap?.total_mb === null || swap?.total_mb === undefined || swap?.total_mb === 0) {
            usedText.textContent = `No ${label || 'Swap'}`;
            percentText.textContent = '-';
            totalText.textContent = '0 MB';
            progressBar.style.width = '0%';
            progressBar.className = 'progress-bar';
            return;
        }

        const percent = toFiniteNumber(swap?.percent) ?? 0;
        const used = toFiniteNumber(swap?.used_mb) ?? 0;
        const total = toFiniteNumber(swap?.total_mb) ?? 0;

        usedText.textContent = `${used.toFixed(0)} MB`;
        percentText.textContent = `${percent.toFixed(1)}%`;
        totalText.textContent = `${total.toFixed(0)} MB`;
        progressBar.style.width = `${percent}%`;
        progressBar.className = percent > 75 ? 'progress-bar bg-danger' : percent > 50 ? 'progress-bar bg-warning' : 'progress-bar bg-success';
    }

    function renderProcessesAndConnections(processes, connections) {
        setText('process-count', processes?.total != null ? String(processes.total) : '-');
        setText('zombie-count', processes?.zombie != null ? String(processes.zombie) : '-');
        setText('connections-total', connections?.total != null ? String(connections.total) : '-');
        setText('connections-established', connections?.established != null ? String(connections.established) : '-');

        const zombieTarget = element('zombie-count');
        if (zombieTarget) {
            zombieTarget.className = Number(processes?.zombie || 0) > 5
                ? 'fw-bold text-danger'
                : Number(processes?.zombie || 0) > 0
                    ? 'fw-bold text-warning'
                    : 'fw-bold text-success';
        }
    }

    function renderDiskIO(diskIo, diskIoRates) {
        setText('disk-read-rate', formatMetricValue(diskIoRates?.current_read_mb_s, ' MB/s'));
        setText('disk-write-rate', formatMetricValue(diskIoRates?.current_write_mb_s, ' MB/s'));
        setText('disk-iops', formatMetricValue(diskIoRates?.current_iops, ''));
        setText('disk-busy-percent', formatMetricValue(diskIoRates?.busy_percent ?? diskIo?.busy_percent, '%'));
        setText('disk-queue-length', formatMetricValue(diskIoRates?.queue_length, ''));
        setText('disk-peak-read-rate', formatMetricValue(diskIoRates?.peak_read_mb_s, ' MB/s'));
        setText('disk-peak-write-rate', formatMetricValue(diskIoRates?.peak_write_mb_s, ' MB/s'));
        setText('disk-read-latency', formatMetricValue(diskIo?.read_latency_ms, ' ms'));
        setText('disk-write-latency', formatMetricValue(diskIo?.write_latency_ms, ' ms'));
    }

    function renderResolvedDeviceCell(entry, ipValue) {
        if (entry?.remote_device_id) {
            const id = Number(entry.remote_device_id);
            const name = escapeHtml(entry.remote_device_name || `Device ${id}`);
            return `<a href="/devices/${id}/details" class="text-info text-decoration-none fw-bold" target="_blank" rel="noopener noreferrer" style="font-size: 11px;"><i class="fas fa-server me-1"></i>${name}</a>`;
        }
        const label = escapeHtml(entry?.resolved_label || entry?.remote_hostname || ipValue || 'N/A');
        const secondary = entry?.resolved_label && entry.resolved_label !== ipValue
            ? `<div class="font-monospace" style="font-size: 10px; color: var(--text-primary); opacity: 0.7;">${escapeHtml(ipValue || 'N/A')}</div>`
            : '';
        return `<div style="line-height:1.2;"><div style="font-size: 10px; color: var(--text-muted); font-style: italic;">${escapeHtml(entry?.resolution_source || 'ip')}</div><div style="font-size: 11px; color: var(--text-primary);">${label}</div>${secondary}</div>`;
    }

    function connectionBadgeClass(count) {
        if (count > 100) return 'text-danger border-danger';
        if (count > 50) return 'text-warning border-warning';
        return 'text-muted border-secondary';
    }

    function renderAgentConnectionSnapshot(snapshot) {
        const badge = element('agent-unique-ips');
        const updated = element('agent-updated');
        const tbody = element('agent-connections-body');
        if (!tbody) return;

        const rows = Array.isArray(snapshot?.rows) ? snapshot.rows : [];
        if (badge) {
            const uniqueCount = toFiniteNumber(snapshot?.meta?.unique_remote_ips_count);
            badge.textContent = uniqueCount === null ? 'Unique IPs: -' : `${Math.round(uniqueCount)} Unique IPs`;
        }
        if (updated) {
            const age = toFiniteNumber(snapshot?.meta?.snapshot_age_seconds);
            updated.textContent = snapshot?.meta?.timestamp
                ? `Snapshot: ${formatDateTime(snapshot.meta.timestamp)}${age !== null ? ` (${Math.round(age)}s old)` : ''}`
                : 'Snapshot: -';
        }

        patchKeyedTableRows(tbody, rows, {
            getKey: (row, index) => row.remote_ip || `agent-ip-${index}`,
            emptyColSpan: 4,
            emptyMessage: 'No agent snapshot data available',
            emptyClassName: 'text-center text-secondary p-3',
            renderCells: (row) => {
                const count = Number.isFinite(Number(row.connection_count)) ? Number(row.connection_count) : 0;
                return `
                    <td class="font-monospace" style="color: var(--text-primary); border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${escapeHtml(row.remote_ip || '-')}</td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px; text-align: center;"><span class="badge rounded border bg-transparent ${connectionBadgeClass(count)}" style="opacity:0.8;">${count}</span></td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${escapeHtml(row.connection_type || 'ESTABLISHED')}</td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${renderResolvedDeviceCell(row, row.remote_ip)}</td>
                `;
            },
        });
    }

    function renderConnectionSnapshotStatus(message, isError = false) {
        const statusEl = element('snapshot-status');
        const tableContainer = element('snapshot-table-container');
        if (!statusEl || !tableContainer) return;

        if (isError || message === 'Loading latest telemetry...' || message === 'Snapshot not loaded yet.') {
            tableContainer.classList.add('d-none');
            statusEl.classList.remove('d-none');
            if (message === 'Loading latest telemetry...') {
                statusEl.innerHTML = '<i class="fas fa-circle-notch fa-spin text-success" style="font-size: 24px; margin-bottom: 8px;"></i><div style="font-size: 12px; color: var(--text-muted);">Refreshing server telemetry...</div>';
            } else if (isError) {
                statusEl.innerHTML = `<i class="fas fa-exclamation-triangle text-danger" style="font-size: 24px; opacity: 0.8; margin-bottom: 8px;"></i><div style="font-size: 12px; color: var(--text-danger);">${escapeHtml(message)}</div>`;
            } else {
                statusEl.innerHTML = '<i class="fas fa-sync-alt" style="font-size: 24px; color: var(--text-muted); opacity: 0.3; margin-bottom: 8px;"></i><div style="font-size: 12px; color: var(--text-muted);">No snapshot loaded</div><div style="font-size: 10px; color: var(--text-muted); margin-top: 2px;">Telemetry refresh will populate the latest connection snapshot</div>';
            }
        } else {
            tableContainer.classList.remove('d-none');
            statusEl.classList.add('d-none');
        }
    }

    function renderConnectionSnapshotTable(rows, meta = {}) {
        const tbody = element('snapshot-connections-body');
        if (!tbody) return;
        patchKeyedTableRows(tbody, rows || [], {
            getKey: (row, index) => `${row.remote_ip || 'ip'}:${index}`,
            emptyColSpan: 4,
            emptyMessage: 'No active remote connections found.',
            emptyClassName: 'text-center text-secondary p-3',
            renderCells: (row) => {
                const count = Number.isFinite(Number(row.connection_count)) ? Number(row.connection_count) : 0;
                return `
                    <td class="font-monospace" style="color: var(--text-primary); border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${escapeHtml(row.remote_ip || '-')}</td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px; text-align: center;"><span class="badge rounded border bg-transparent ${connectionBadgeClass(count)}" style="opacity:0.8;">${count}</span></td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${escapeHtml(row.connection_type || 'ESTABLISHED')}</td>
                    <td style="border-top-color: rgba(255,255,255,0.06); padding: 8px 16px;">${renderResolvedDeviceCell(row, row.remote_ip)}</td>
                `;
            },
        });

        const totalConnections = Number.isFinite(Number(meta.total_connections))
            ? Number(meta.total_connections)
            : (rows || []).reduce((sum, row) => sum + (Number(row.connection_count) || 0), 0);
        const totalIps = Number.isFinite(Number(meta.unique_remote_ips_count))
            ? Number(meta.unique_remote_ips_count)
            : (rows || []).length;
        const snapshotAge = Number.isFinite(Number(meta.snapshot_age_seconds)) ? `${Number(meta.snapshot_age_seconds)}s old` : 'age unknown';
        renderConnectionSnapshotStatus(
            `Snapshot ${snapshotAge}. Showing ${(rows || []).length} entries across ${totalIps} remote IPs (${totalConnections} active connections).`
        );
    }

    function renderTopProcessesTable() {
        const tbody = element('top-processes-body');
        if (!tbody) return;
        const processes = sortProcesses(currentPayload?.process_catalog || [], processSortMode).slice(0, 8);
        patchKeyedTableRows(tbody, processes, {
            getKey: (proc, index) => proc.pid || `${proc.name || 'proc'}-${index}`,
            emptyColSpan: 6,
            emptyMessage: 'No process data available',
            emptyClassName: 'text-center text-secondary p-3',
            renderCells: (proc) => {
                const cpu = toFiniteNumber(proc.cpu_percent);
                const memory = toFiniteNumber(proc.memory_percent);
                const path = proc.path || '-';
                const inlineBar = (pct) => {
                    if (pct === null) return '-';
                    const bg = pct > 50 ? 'rgba(220,53,69,.25)' : pct > 25 ? 'rgba(255,193,7,.2)' : 'rgba(0,212,170,.15)';
                    return `<div style="position:relative;display:inline-block;min-width:60px;">` +
                        `<div style="position:absolute;left:0;top:0;height:100%;width:${Math.min(100, pct)}%;background:${bg};border-radius:2px;pointer-events:none;"></div>` +
                        `<span style="position:relative;z-index:1;">${pct.toFixed(1)}%</span>` +
                        `</div>`;
                };
                return `
                    <td title="${escapeHtml(path)}">${escapeHtml(proc.name || '-')}</td>
                    <td>${escapeHtml(proc.pid || '-')}</td>
                    <td>${inlineBar(cpu)}</td>
                    <td>${inlineBar(memory)}</td>
                    <td>${escapeHtml(proc.status || '-')}</td>
                    <td title="${escapeHtml(path)}">${escapeHtml(truncateMiddle(path))}</td>
                `;
            },
        });
    }

    function bindProcessSortControls() {
        const memoryButton = element('process-sort-memory');
        const cpuButton = element('process-sort-cpu');
        if (memoryButton && memoryButton.dataset.bound !== 'true') {
            memoryButton.dataset.bound = 'true';
            memoryButton.addEventListener('click', () => {
                processSortMode = 'memory';
                memoryButton.classList.add('active', 'tactical-btn-primary');
                memoryButton.classList.remove('tactical-btn-ghost');
                if (cpuButton) {
                    cpuButton.classList.remove('active', 'tactical-btn-primary');
                    cpuButton.classList.add('tactical-btn-ghost');
                }
                renderTopProcessesTable();
            });
        }
        if (cpuButton && cpuButton.dataset.bound !== 'true') {
            cpuButton.dataset.bound = 'true';
            cpuButton.addEventListener('click', () => {
                processSortMode = 'cpu';
                cpuButton.classList.add('active', 'tactical-btn-primary');
                cpuButton.classList.remove('tactical-btn-ghost');
                if (memoryButton) {
                    memoryButton.classList.remove('active', 'tactical-btn-primary');
                    memoryButton.classList.add('tactical-btn-ghost');
                }
                renderTopProcessesTable();
            });
        }
    }

    function renderExtendedMetrics(payload) {
        setText('cpu-iowait', formatMetricValue(payload.cpu_iowait_percent, '%'));
        setText('cpu-steal', formatMetricValue(payload.cpu_steal_percent, '%'));
        setText('fd-open', formatCount(payload.processes?.open_fds));
        setText('fd-limit', formatCount(payload.processes?.fd_limit));
        setText('fd-percent', formatMetricValue(payload.processes?.fd_percent, '%'));

        const interfaceBody = element('net-interfaces-body');
        if (interfaceBody) {
            const entries = Object.entries(payload.network_per_interface || {});
            patchKeyedTableRows(interfaceBody, entries, {
                getKey: ([name]) => name,
                emptyColSpan: 5,
                emptyMessage: 'No interface network stats available',
                emptyClassName: 'text-center text-secondary p-3',
                renderCells: ([name, stats]) => {
                    const inBytes = toFiniteNumber(stats?.bytes_recv_per_sec ?? stats?.rx_bytes_per_sec);
                    const outBytes = toFiniteNumber(stats?.bytes_sent_per_sec ?? stats?.tx_bytes_per_sec);
                    const unitInfo = detectRateUnit([inBytes, outBytes]);
                    return `
                        <td>${escapeHtml(name)}</td>
                        <td>${inBytes === null ? '-' : (inBytes / unitInfo.divisor).toFixed(2)}</td>
                        <td>${outBytes === null ? '-' : (outBytes / unitInfo.divisor).toFixed(2)}</td>
                        <td>${formatCount(stats?.packets_recv ?? stats?.rx_packets)}</td>
                        <td>${formatCount(stats?.packets_sent ?? stats?.tx_packets)}</td>
                    `;
                },
            });
        }

        const cpuBody = element('top-cpu-processes-body');
        if (cpuBody) {
            patchKeyedTableRows(cpuBody, (payload.top_processes_cpu || []).slice(0, 5), {
                getKey: (proc, index) => proc.pid || `${proc.name || 'proc'}-${index}`,
                emptyColSpan: 5,
                emptyMessage: 'No CPU process data available',
                emptyClassName: 'text-center text-secondary p-3',
                renderCells: (proc) => `
                    <td>${escapeHtml(proc.name || '-')}</td>
                    <td>${escapeHtml(proc.pid || '-')}</td>
                    <td>${proc.cpu_percent != null ? `${Number(proc.cpu_percent).toFixed(1)}%` : '-'}</td>
                    <td>${proc.memory_percent != null ? `${Number(proc.memory_percent).toFixed(1)}%` : '-'}</td>
                    <td>${escapeHtml(proc.status || '-')}</td>
                `,
            });
        }
    }

    function collectThresholdPatch() {
        const tbody = element('thresholds-body');
        const rows = Array.from(tbody?.querySelectorAll('tr[data-metric-key]') || []);
        const metrics = {};
        rows.forEach((row) => {
            const metricKey = row.dataset.metricKey;
            metrics[metricKey] = {
                enabled: Boolean(row.querySelector('.threshold-enabled')?.checked),
                warning: toFiniteNumber(row.querySelector('.threshold-warning')?.value) ?? 0,
                critical: toFiniteNumber(row.querySelector('.threshold-critical')?.value) ?? 0,
            };
        });
        return metrics;
    }

    function bindThresholdEditor() {
        const saveButton = element('threshold-save');
        const resetButton = element('threshold-reset');
        const tbody = element('thresholds-body');
        if (!tbody) return;

        if (saveButton && !saveButton.dataset.bound) {
            saveButton.dataset.bound = 'true';
            saveButton.addEventListener('click', async () => {
                const feedback = element('threshold-feedback');
                const reasonInput = element('threshold-reason');
                try {
                    saveButton.disabled = true;
                    if (feedback) feedback.textContent = 'Saving threshold profile...';
                    const response = await fetch('/api/server/thresholds', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            version: thresholdState.version,
                            change_reason: reasonInput?.value || '',
                            metrics: collectThresholdPatch(),
                        }),
                    });
                    const data = await parseApiResponse(response);
                    if (!response.ok || data.error) {
                        throw new Error(getApiErrorMessage(data, 'Failed to save threshold profile'));
                    }
                    thresholdState = { version: data.version, metrics: data.metrics || {} };
                    renderThresholdEditor(data.metrics || {}, data);
                    if (feedback) feedback.textContent = 'Threshold profile saved.';
                    if (currentDeviceId) {
                        await load(currentDeviceId, currentRange);
                    }
                } catch (error) {
                    if (feedback) feedback.textContent = error.message || 'Failed to save threshold profile.';
                } finally {
                    saveButton.disabled = false;
                }
            });
        }

        if (resetButton && !resetButton.dataset.bound) {
            resetButton.dataset.bound = 'true';
            resetButton.addEventListener('click', () => {
                Array.from(tbody.querySelectorAll('tr[data-metric-key]')).forEach((row) => {
                    const enabled = row.querySelector('.threshold-enabled');
                    const warning = row.querySelector('.threshold-warning');
                    const critical = row.querySelector('.threshold-critical');
                    if (enabled) enabled.checked = row.dataset.defaultEnabled === '1';
                    if (warning) warning.value = row.dataset.defaultWarning || '';
                    if (critical) critical.value = row.dataset.defaultCritical || '';
                });
                const feedback = element('threshold-feedback');
                if (feedback) feedback.textContent = 'Reset to catalog defaults. Save to apply.';
            });
        }
    }

    function renderThresholdEditor(metrics, profileMeta) {
        const tbody = element('thresholds-body');
        if (!tbody) return;
        const editable = root?.dataset?.thresholdEditable === '1';
        const orderedMetrics = sortThresholdMetrics(metrics);
        patchKeyedTableRows(tbody, orderedMetrics, {
            getKey: ([metricKey]) => metricKey,
            emptyColSpan: 5,
            emptyMessage: 'No threshold metrics available',
            emptyClassName: 'text-center text-secondary p-3',
            applyRow: (row, [metricKey, config]) => {
                row.dataset.metricKey = metricKey;
                row.dataset.defaultEnabled = config.default_enabled ? '1' : '0';
                row.dataset.defaultWarning = String(config.default_warning ?? config.warning ?? '');
                row.dataset.defaultCritical = String(config.default_critical ?? config.critical ?? '');
            },
            renderCells: ([metricKey, config]) => `
                <td><div class="fw-bold">${escapeHtml(config.label || metricKey)}</div><div class="small text-secondary">${escapeHtml(config.category || '')}</div></td>
                <td><input type="checkbox" class="form-check-input threshold-enabled" ${config.enabled ? 'checked' : ''} ${editable ? '' : 'disabled'}></td>
                <td><input type="number" step="0.1" class="form-control form-control-sm threshold-warning" value="${config.warning}" ${editable ? '' : 'disabled'}></td>
                <td><input type="number" step="0.1" class="form-control form-control-sm threshold-critical" value="${config.critical}" ${editable ? '' : 'disabled'}></td>
                <td>${escapeHtml(config.unit || '')}</td>
            `,
        });

        setText('threshold-version', profileMeta?.version != null ? String(profileMeta.version) : '-');
        setText('threshold-updated', profileMeta?.updated_at ? formatDateTime(profileMeta.updated_at) : '-');
        setText('threshold-meta', profileMeta?.updated_by ? `v${profileMeta.version} by ${profileMeta.updated_by}` : `v${profileMeta?.version ?? '-'}`);
        const feedback = element('threshold-feedback');
        if (feedback && !feedback.dataset.initialized) {
            feedback.dataset.initialized = 'true';
            feedback.textContent = editable ? 'Edit values and save to apply globally.' : 'Read-only threshold profile.';
        }
        bindThresholdEditor();
    }

    function renderDiskIOSparkline(canvasId, data, color) {
        const canvas = root?.querySelector(`#${prefix}-${canvasId}`);
        const ctx = canvas?.getContext('2d');
        if (!ctx || !window.Chart) return;

        if (sparkCharts[canvasId]) {
            sparkCharts[canvasId].data.labels = data.map(() => '');
            sparkCharts[canvasId].data.datasets[0].data = data;
            sparkCharts[canvasId].update('none');
            return;
        }

        sparkCharts[canvasId] = new window.Chart(ctx, {
            type: 'line',
            data: {
                labels: data.map(() => ''),
                datasets: [{
                    data,
                    borderColor: color,
                    backgroundColor: `${color}20`,
                    borderWidth: 1.5,
                    fill: true,
                    pointRadius: 0,
                    tension: 0.3,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    x: { display: false },
                    y: { display: false, beginAtZero: true },
                },
            },
        });
    }

    function getCachedPayload(deviceId, range) {
        const cacheKey = buildTelemetryCacheKey(deviceId, range);
        const entry = payloadCache.get(cacheKey);
        if (!entry) return null;
        if ((Date.now() - entry.cachedAt) > TELEMETRY_CACHE_TTL_MS) {
            payloadCache.delete(cacheKey);
            return null;
        }
        return entry.payload;
    }

    function cachePayload(deviceId, range, payload) {
        const cacheKey = buildTelemetryCacheKey(deviceId, range);
        payloadCache.set(cacheKey, {
            payload,
            cachedAt: Date.now(),
        });
        if (payloadCache.size > 30) {
            const oldestKey = payloadCache.keys().next().value;
            if (oldestKey) payloadCache.delete(oldestKey);
        }
    }

    async function fetchTelemetry(deviceId, range, { signal } = {}) {
        const response = await fetch(`/api/devices/${deviceId}/telemetry?range=${range}`, {
            credentials: 'same-origin',
            signal,
        });
        const data = await parseApiResponse(response);
        if (!response.ok || data.error) {
            throw new Error(getApiErrorMessage(data, 'Failed to load server telemetry'));
        }
        return data;
    }

    function applyPayload(deviceId, data) {
        currentPayload = data;
        bindProcessSortControls();

        setText('title', data.device_name || 'Server Details');
        setText('ip', data.ip || '-');
        setText('hostname', data.hostname || '-');
        setText('uptime', formatUptime(data.uptime, data.uptime_seconds));
        setText('boot-time', formatDateTime(data.boot_time));
        setText('os', [data.os?.name, data.os?.version, data.os?.arch].filter(Boolean).join(' ') || '-');
        setText('hardware', formatHardwareSpecs(data.hardware_specs || {}));
        setText('last-seen', formatDateTime(data.last_seen));
        setOpenDetailsLink(deviceId);

        const labels = Array.isArray(data.labels) ? data.labels : [];
        const cpu = Array.isArray(data.cpu) ? data.cpu : [];
        const memory = Array.isArray(data.memory) ? data.memory : [];
        const disk = Array.isArray(data.disk) ? data.disk : [];
        const netIn = Array.isArray(data.net_in) ? data.net_in : [];
        const netOut = Array.isArray(data.net_out) ? data.net_out : [];
        const hasAnyValue = (values) => Array.isArray(values) && values.some((value) => value !== null && value !== undefined && !Number.isNaN(value));
        const isEmpty = labels.length === 0 || !(hasAnyValue(cpu) || hasAnyValue(memory) || hasAnyValue(disk) || hasAnyValue(netIn) || hasAnyValue(netOut));

        setChartEmptyState('chart-cpu', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-mem', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-disk', isEmpty, 'No telemetry in range');
        setChartEmptyState('chart-net', isEmpty, 'No telemetry in range');

        const thresholds = data.thresholds || { metrics: {} };
        thresholdState = {
            version: data.threshold_profile?.version ?? thresholdState.version,
            metrics: thresholds.metrics || {},
        };

        const evaluations = data.health_evaluations || {};
        updateHealthHeader(data);
        renderMetricSummaries(data, evaluations);
        renderAlertsBanner(data.alerts || []);
        renderLoadAveragePanel(data);
        renderSwapUsage(data.swap || {}, data.memory_paging_label || 'Swap Usage');
        renderProcessesAndConnections(data.processes || {}, data.network_connections || {});
        renderDiskIO(data.disk_io || {}, data.disk_io_rates || {});
        renderTopProcessesTable();

        const snapshot = data.connection_snapshot || {
            rows: data.network_top_remote_ips || [],
            meta: {
                timestamp: data.last_seen || null,
                unique_remote_ips_count: data.network_connections_unique_ips,
            },
        };
        renderAgentConnectionSnapshot(snapshot);
        renderConnectionSnapshotTable(snapshot.rows || [], snapshot.meta || {});
        renderExtendedMetrics(data);
        renderThresholdEditor(thresholds.metrics || {}, data.threshold_profile || {});

        // Disk I/O sparklines — ring buffers capped at SPARK_MAX_POINTS
        const readRate = toFiniteNumber(data.disk_io_rates?.current_read_mb_s);
        const writeRate = toFiniteNumber(data.disk_io_rates?.current_write_mb_s);
        if (readRate !== null) {
            diskReadHistory.push(readRate);
            if (diskReadHistory.length > SPARK_MAX_POINTS) diskReadHistory.shift();
        }
        if (writeRate !== null) {
            diskWriteHistory.push(writeRate);
            if (diskWriteHistory.length > SPARK_MAX_POINTS) diskWriteHistory.shift();
        }
        if (diskReadHistory.length) renderDiskIOSparkline('chart-disk-read-spark', diskReadHistory, '#20c997');
        if (diskWriteHistory.length) renderDiskIOSparkline('chart-disk-write-spark', diskWriteHistory, '#fd7e14');

        if (isEmpty) return data;

        renderChart({
            canvasId: 'chart-cpu',
            labels,
            series: [{ label: 'CPU', data: cpu, color: '#0d6efd' }],
            unit: '%',
            yAxisLabel: 'Utilization %',
            thresholds: thresholds.metrics?.cpu_usage_pct?.bands || [],
            thresholdConfig: thresholds.metrics?.cpu_usage_pct || null,
            forceMax: 100,
        });
        renderChart({
            canvasId: 'chart-mem',
            labels,
            series: [{ label: 'Memory', data: memory, color: '#6610f2' }],
            unit: '%',
            yAxisLabel: 'Utilization %',
            thresholds: thresholds.metrics?.memory_usage_pct?.bands || [],
            thresholdConfig: thresholds.metrics?.memory_usage_pct || null,
            forceMax: 100,
        });
        renderChart({
            canvasId: 'chart-disk',
            labels,
            series: [{ label: 'Disk', data: disk, color: '#dc3545' }],
            unit: '%',
            yAxisLabel: 'Utilization %',
            thresholds: thresholds.metrics?.disk_usage_pct?.bands || [],
            thresholdConfig: thresholds.metrics?.disk_usage_pct || null,
            forceMax: 100,
        });

        const netUnitInfo = detectRateUnit([...netIn, ...netOut]);
        renderChart({
            canvasId: 'chart-net',
            labels,
            series: [
                { label: 'Inbound', data: netIn, color: '#20c997' },
                { label: 'Outbound', data: netOut, color: '#fd7e14' },
            ],
            yAxisLabel: `Bandwidth (${netUnitInfo.label})`,
            tickFormatter: (value) => {
                const numeric = toFiniteNumber(value);
                return numeric === null ? '-' : (numeric / netUnitInfo.divisor).toFixed(2);
            },
            tooltipLabelFormatter: (ctx) => {
                const raw = toFiniteNumber(ctx.parsed.y);
                return raw === null ? `${ctx.dataset.label}: -` : `${ctx.dataset.label}: ${formatRate(raw, netUnitInfo)}`;
            },
        });

        initialDataLoaded = true;
        return data;
    }

    function prefetch(deviceId, activeRange = currentRange) {
        buildTelemetryPrefetchOrder(activeRange).forEach((range, index) => {
            if (getCachedPayload(deviceId, range)) return;

            const cacheKey = buildTelemetryCacheKey(deviceId, range);
            if (prefetchPromises.has(cacheKey)) return;

            const delayMs = 120 * (index + 1);
            window.setTimeout(() => {
                if (getCachedPayload(deviceId, range) || prefetchPromises.has(cacheKey)) return;
                const promise = fetchTelemetry(deviceId, range)
                    .then((payload) => {
                        cachePayload(deviceId, range, payload);
                        return payload;
                    })
                    .catch(() => null)
                    .finally(() => {
                        prefetchPromises.delete(cacheKey);
                    });
                prefetchPromises.set(cacheKey, promise);
            }, delayMs);
        });
    }

    function destroy() {
        if (currentAbortController) {
            currentAbortController.abort();
            currentAbortController = null;
        }
        Object.values(charts).forEach((chart) => chart?.destroy?.());
        Object.keys(charts).forEach((key) => delete charts[key]);
        Object.values(sparkCharts).forEach((chart) => chart?.destroy?.());
        Object.keys(sparkCharts).forEach((key) => delete sparkCharts[key]);
        registeredCharts.length = 0;
        currentDeviceId = null;
        currentPayload = null;
        currentLoadPromise = null;
        currentLoadKey = null;
    }

    async function load(deviceId, range, options = {}) {
        currentDeviceId = deviceId;
        currentRange = range || currentRange;
        const { showSnapshotLoadingState = false, preferCache = false } = options;
        const cachedPayload = getCachedPayload(deviceId, currentRange);
        if (showSnapshotLoadingState && !cachedPayload) {
            renderConnectionSnapshotStatus('Loading latest telemetry...');
        }

        if (preferCache && cachedPayload) {
            applyPayload(deviceId, cachedPayload);
            prefetch(deviceId, currentRange);
            return cachedPayload;
        }

        const requestKey = buildTelemetryCacheKey(deviceId, currentRange);
        if (currentLoadPromise && currentLoadKey === requestKey) {
            return currentLoadPromise;
        }

        if (currentAbortController) {
            currentAbortController.abort();
        }
        currentAbortController = new AbortController();
        currentLoadKey = requestKey;
        currentLoadPromise = (async () => {
            const data = await fetchTelemetry(deviceId, currentRange, {
                signal: currentAbortController?.signal,
            });
            cachePayload(deviceId, currentRange, data);
            applyPayload(deviceId, data);
            prefetch(deviceId, currentRange);
            return data;
        })();

        try {
            return await currentLoadPromise;
        } catch (error) {
            if (error?.name === 'AbortError') {
                return cachedPayload || currentPayload || null;
            }
            throw error;
        } finally {
            if (currentLoadKey === requestKey) {
                currentLoadPromise = null;
                currentLoadKey = null;
                currentAbortController = null;
            }
        }
    }

    async function fetchConnectionSnapshot(deviceId, { showLoadingState = false } = {}) {
        return load(deviceId, currentRange, {
            showSnapshotLoadingState: showLoadingState,
            preferCache: !showLoadingState,
        });
    }

    function invalidateCache(deviceId, range) {
        const key = buildTelemetryCacheKey(deviceId, range || currentRange);
        payloadCache.delete(key);
    }

    return {
        load,
        fetchConnectionSnapshot,
        prefetch,
        destroy,
        invalidateCache,
        setOpenDetailsLink,
        getCurrentRange: () => currentRange,
        getCurrentDeviceId: () => currentDeviceId,
    };
}
