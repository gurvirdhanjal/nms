import { openServerModal } from '../modals/serverDetailModal.js?v=2.10';
import { timeAgo } from '../utils.js';
import { patchKeyedTableRows } from '../domPatch.js';

let currentFilter = 'all';
let activeRange = '24h';
const chartRegistry = new Map();

const STATE_TO_BADGE = {
    Healthy: 'tactical-badge-success',
    Warning: 'tactical-badge-warning',
    Critical: 'tactical-badge-danger',
    Offline: 'tactical-badge-danger',
    Unknown: 'tactical-badge-secondary',
};

function safeText(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = value;
    }
}

function toNumber(value, fallback = null) {
    if (value === null || value === undefined || value === '') return fallback;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
}

function formatPercent(value, digits = 1, empty = '-') {
    const numeric = toNumber(value);
    if (numeric === null) return empty;
    return `${numeric.toFixed(digits)}%`;
}

function formatDelta(value) {
    const numeric = toNumber(value);
    if (numeric === null) return 'No 24h baseline yet';
    const prefix = numeric >= 0 ? '+' : '';
    return `${prefix}${numeric.toFixed(1)}% vs previous 24h`;
}

function formatMs(value, digits = 1, empty = '-') {
    const numeric = toNumber(value);
    if (numeric === null) return empty;
    return `${numeric.toFixed(digits)} ms`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeSeverityLabel(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'offline') return 'Offline';
    if (normalized === 'critical') return 'Critical';
    if (normalized === 'warning') return 'Warning';
    if (normalized === 'healthy') return 'Healthy';
    return 'Unknown';
}

function normalizeSeverityClass(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'critical' || normalized === 'offline') return 'critical';
    if (normalized === 'warning') return 'warning';
    return 'healthy';
}

function severityRank(issue) {
    return Number(issue?.severity_rank ?? 0);
}

function sortServers(servers) {
    return [...(servers || [])].sort((left, right) => {
        const leftIssue = left.primary_issue || {};
        const rightIssue = right.primary_issue || {};
        const bySeverity = severityRank(rightIssue) - severityRank(leftIssue);
        if (bySeverity !== 0) return bySeverity;

        const byScore = Number(rightIssue.breach_score || 0) - Number(leftIssue.breach_score || 0);
        if (byScore !== 0) return byScore;

        const leftHealth = normalizeSeverityClass(left.health);
        const rightHealth = normalizeSeverityClass(right.health);
        const byHealth = ['healthy', 'warning', 'critical'].indexOf(leftHealth) - ['healthy', 'warning', 'critical'].indexOf(rightHealth);
        if (byHealth !== 0) return byHealth;

        return String(left.hostname || left.device_name || left.ip || '').localeCompare(
            String(right.hostname || right.device_name || right.ip || '')
        );
    });
}

function filteredServers(payload) {
    const servers = sortServers(payload?.servers || []);
    if (currentFilter === 'problem') {
        return servers.filter((server) => Boolean(server.primary_issue) || server.health !== 'Healthy');
    }
    if (currentFilter === 'healthy') {
        return servers.filter((server) => !server.primary_issue && server.health === 'Healthy');
    }
    return servers;
}

function updateFleetCardState(cardId, stateId, impactId, deltaId, card) {
    const el = document.getElementById(cardId);
    const stateClass = normalizeSeverityClass(card?.severity);
    if (el) {
        el.classList.remove('state-critical', 'state-warning');
        if (stateClass === 'critical') {
            el.classList.add('state-critical');
        } else if (stateClass === 'warning') {
            el.classList.add('state-warning');
        }
    }

    safeText(stateId, normalizeSeverityLabel(card?.severity_label || card?.severity));
    safeText(impactId, `${card?.impacted_servers ?? 0} ${card?.impacted_servers === 1 ? 'server' : 'servers'} impacted`);
    safeText(deltaId, formatDelta(card?.delta_24h));
}

function wireBannerActions(issue) {
    const viewBtn = document.getElementById('btn-fleet-banner-view');
    const ackBtn = document.getElementById('btn-fleet-banner-ack');
    if (viewBtn) {
        viewBtn.onclick = () => {
            if (issue?.device_id) {
                openServerModal(issue.device_id);
                return;
            }
            document.getElementById('server-health-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        };
    }

    if (ackBtn) {
        ackBtn.disabled = !issue?.event_id;
        ackBtn.textContent = issue?.is_acknowledged ? 'Acknowledged' : 'Acknowledge';
        ackBtn.onclick = async () => {
            if (!issue?.event_id || issue?.is_acknowledged) return;
            ackBtn.disabled = true;
            ackBtn.textContent = 'Acknowledging...';
            try {
                const response = await fetch(`/api/dashboard/alerts/${issue.event_id}/acknowledge`, {
                    method: 'POST',
                    credentials: 'same-origin',
                });
                if (!response.ok) {
                    throw new Error(`Failed to acknowledge (${response.status})`);
                }
                ackBtn.textContent = 'Acknowledged';
            } catch (_error) {
                ackBtn.disabled = false;
                ackBtn.textContent = 'Retry Ack';
            }
        };
    }
}

function renderBanner(issue) {
    const banner = document.getElementById('fleet-priority-banner');
    if (!banner) return;

    if (!issue) {
        banner.classList.add('initially-hidden');
        return;
    }

    const severityClass = normalizeSeverityClass(issue.severity);
    banner.classList.remove('initially-hidden', 'severity-critical', 'severity-warning');
    if (severityClass === 'critical') {
        banner.classList.add('severity-critical');
    } else if (severityClass === 'warning') {
        banner.classList.add('severity-warning');
    }

    const pill = document.getElementById('fleet-banner-severity');
    if (pill) {
        pill.classList.remove('severity-critical', 'severity-warning');
        if (severityClass === 'critical') {
            pill.classList.add('severity-critical');
        } else if (severityClass === 'warning') {
            pill.classList.add('severity-warning');
        }
        pill.textContent = normalizeSeverityLabel(issue.severity);
    }

    const issueLabel = issue.metric_label || 'Incident';
    const issueValue = issue.formatted_value || '';
    safeText('fleet-banner-message', `${issueLabel.toUpperCase()} - ${issue.hostname || issue.device_name} (${issueValue})`);
    safeText(
        'fleet-banner-subtext',
        issue.message || `${issue.hostname || issue.device_name} requires investigation.`
    );
    wireBannerActions(issue);
}

function renderImpactSummary(summary = {}) {
    const affected = Number(summary.affected_servers || 0);
    const total = Number(summary.total_servers || 0);
    const primaryLabel = summary.primary_issue_label || 'No active server issues';
    const severity = summary.primary_issue_severity || 'Healthy';
    const unaffected = Array.isArray(summary.unaffected_domains) && summary.unaffected_domains.length
        ? summary.unaffected_domains.join(', ')
        : 'None';

    safeText('fleet-impact-title', `${primaryLabel} (${severity})`);
    safeText(
        'fleet-impact-footprint',
        `${affected} of ${total} servers affected (${Number(summary.fleet_pct || 0).toFixed(1)}% of fleet)`
    );
    safeText('fleet-impact-unaffected', unaffected);
}

function renderActiveIssues(issues = []) {
    const container = document.getElementById('fleet-active-issues-list');
    safeText('fleet-active-issues-count', String(issues.length));
    if (!container) return;

    if (!issues.length) {
        const emptyHtml = '<div class="fleet-empty-state">No active server incidents.</div>';
        if (container.dataset.renderKey !== 'empty') {
            container.innerHTML = emptyHtml;
            container.dataset.renderKey = 'empty';
        }
        return;
    }

    const renderKey = issues.map((i) => `${i.device_id}:${i.severity}:${i.formatted_value}`).join('|');
    if (container.dataset.renderKey === renderKey) return;
    container.dataset.renderKey = renderKey;

    container.innerHTML = issues.map((issue) => {
        const severityClass = normalizeSeverityClass(issue.severity);
        return `
            <article class="fleet-issue-card severity-${severityClass}">
                <div class="fleet-issue-head">
                    <div>
                        <div class="fleet-issue-title">${escapeHtml(issue.hostname || issue.device_name || issue.ip || 'Unknown')}</div>
                        <div class="fleet-issue-sub">${escapeHtml(issue.ip || '-')}</div>
                    </div>
                    <span class="badge ${STATE_TO_BADGE[normalizeSeverityLabel(issue.severity)] || 'tactical-badge-secondary'}">${escapeHtml(normalizeSeverityLabel(issue.severity))}</span>
                </div>
                <div class="fleet-issue-metric">${escapeHtml(issue.metric_label || 'Issue')} ${escapeHtml(issue.formatted_value || '')}</div>
                <div class="fleet-issue-stats">
                    <div class="fleet-issue-stat">
                        <span class="fleet-issue-stat-label">CPU</span>
                        <strong>${formatPercent(issue.metrics?.cpu)}</strong>
                    </div>
                    <div class="fleet-issue-stat">
                        <span class="fleet-issue-stat-label">Memory</span>
                        <strong>${formatPercent(issue.metrics?.memory)}</strong>
                    </div>
                    <div class="fleet-issue-stat">
                        <span class="fleet-issue-stat-label">Disk</span>
                        <strong>${formatPercent(issue.metrics?.disk)}</strong>
                    </div>
                </div>
                <div class="fleet-issue-actions">
                    <button class="btn btn-sm tactical-btn-outline" type="button" data-issue-open="${escapeHtml(issue.device_id)}">Investigate</button>
                    <a class="btn btn-sm tactical-btn-outline" href="/devices/${encodeURIComponent(issue.device_id)}/server-monitoring">Open Details</a>
                </div>
            </article>
        `;
    }).join('');

    container.querySelectorAll('[data-issue-open]').forEach((button) => {
        button.addEventListener('click', () => {
            const deviceId = Number(button.getAttribute('data-issue-open'));
            if (Number.isFinite(deviceId)) {
                openServerModal(deviceId);
            }
        });
    });
}

function formatTrendLabel(label, rangeHours = 24) {
    if (!label) return '';
    // Backend emits bare UTC timestamps (no Z). Append Z so the browser
    // treats them as UTC before converting to IST, not as local time.
    const normalized = /z$|[+-]\d{2}:\d{2}$/i.test(String(label)) ? String(label) : `${label}Z`;
    const parsed = new Date(normalized);
    if (Number.isNaN(parsed.getTime())) return String(label).slice(11, 16) || String(label);
    if (rangeHours > 168) {
        // Daily labels: show "Apr 1"
        return parsed.toLocaleDateString('en-IN', {
            timeZone: 'Asia/Kolkata',
            month: 'short',
            day: 'numeric',
        });
    }
    if (rangeHours > 24) {
        // Multi-day: show "Apr 1 14:00"
        return parsed.toLocaleString('en-IN', {
            timeZone: 'Asia/Kolkata',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        });
    }
    return parsed.toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function makeGradient(ctx, canvas, hexColor) {
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 160);
    gradient.addColorStop(0, hexColor + '55');
    gradient.addColorStop(1, hexColor + '00');
    return gradient;
}

function buildTrendPlugin(metric) {
    return {
        id: `fleet-thresholds-${metric}`,
        beforeDatasetsDraw(chart) {
            const y = chart.scales.y;
            const x = chart.chartArea;
            if (!y || !x) return;

            const ctx = chart.ctx;
            const bands = chart.options.plugins?.fleetBands?.bands || [];
            ctx.save();
            bands.forEach((band) => {
                const from = Number(band.from || 0);
                const to = Number(band.to || 0);
                const top = y.getPixelForValue(to);
                const bottom = y.getPixelForValue(from);
                ctx.fillStyle = band.color || 'rgba(255,255,255,0.04)';
                ctx.fillRect(x.left, top, x.right - x.left, bottom - top);
            });
            ctx.restore();
        },
        afterDatasetsDraw(chart) {
            const y = chart.scales.y;
            const x = chart.chartArea;
            if (!y || !x) return;

            const ctx = chart.ctx;
            const warning = chart.options.plugins?.fleetBands?.warning;
            const critical = chart.options.plugins?.fleetBands?.critical;
            ctx.save();
            [warning, critical].forEach((value, index) => {
                if (typeof value !== 'number' || value <= 0) return;
                const py = y.getPixelForValue(value);
                ctx.strokeStyle = index === 0 ? 'rgba(255, 193, 7, 0.85)' : 'rgba(220, 53, 69, 0.92)';
                ctx.setLineDash([5, 4]);
                ctx.beginPath();
                ctx.moveTo(x.left, py);
                ctx.lineTo(x.right, py);
                ctx.stroke();
            });
            ctx.restore();
        },
    };
}

function renderTrendChart(metric, trend, metaId) {
    const canvasId = `chart-fleet-trend-${metric}`;
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;
    if (typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || '')) return;

    const rangeHours = toNumber(trend?.range_hours, 24);
    const labels = Array.isArray(trend?.labels) ? trend.labels.map((l) => formatTrendLabel(l, rangeHours)) : [];
    const values = Array.isArray(trend?.values) ? trend.values : [];
    const peakValues = Array.isArray(trend?.peak) ? trend.peak : [];
    const markers = Array.isArray(trend?.markers) ? trend.markers : [];

    const metaText = trend?.delta === null || trend?.delta === undefined
        ? 'Awaiting enough history for comparison'
        : `${trend.delta >= 0 ? '+' : ''}${Number(trend.delta).toFixed(1)}% vs previous period`;
    safeText(metaId, metaText);

    const colorMap = { cpu: '#74c0fc', memory: '#ffd166', disk: '#a7f3d0' };
    const datasetColor = colorMap[metric] || '#74c0fc';

    let ctx = null;
    try {
        ctx = canvas.getContext('2d');
    } catch (_error) {
        return;
    }
    if (!ctx || typeof ctx.createLinearGradient !== 'function') return;
    const gradient = makeGradient(ctx, canvas, datasetColor);

    const datasets = [
        {
            type: 'line',
            label: `${metric} avg`,
            data: values,
            borderColor: datasetColor,
            backgroundColor: gradient,
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.3,
            fill: true,
            order: 2,
        },
    ];

    // Peak (fleet max per bucket) as dashed secondary line
    if (peakValues.some((v) => v !== null && v !== 0)) {
        datasets.push({
            type: 'line',
            label: `${metric} peak`,
            data: peakValues,
            borderColor: datasetColor + '88',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [4, 3],
            pointRadius: 0,
            tension: 0.3,
            fill: false,
            order: 3,
        });
    }

    // Threshold breach markers
    if (markers.length) {
        datasets.push({
            type: 'scatter',
            label: `${metric}-markers`,
            data: markers.map((m) => ({ x: labels[m.index] ?? '', y: m.value })),
            pointRadius: 4,
            pointHoverRadius: 5,
            pointBackgroundColor: markers.map((m) => m.state === 'critical' ? '#dc3545' : '#ffc107'),
            pointBorderColor: '#0f1720',
            pointBorderWidth: 1.5,
            showLine: false,
            order: 1,
        });
    }

    const existing = chartRegistry.get(canvasId);
    if (existing) {
        existing.destroy();
        chartRegistry.delete(canvasId);
    }

    const chart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        plugins: [buildTrendPlugin(metric)],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label(context) {
                            if (context.dataset.label?.endsWith('-markers')) return null;
                            return `${context.dataset.label}: ${context.parsed.y?.toFixed?.(1) ?? context.parsed.y}%`;
                        },
                    },
                },
                fleetBands: {
                    bands: trend?.bands || [],
                    warning: toNumber(trend?.warning),
                    critical: toNumber(trend?.critical),
                },
            },
            scales: {
                x: {
                    ticks: { color: '#8a97a6', maxTicksLimit: 6 },
                    grid: { color: 'rgba(148, 163, 184, 0.08)' },
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: {
                        color: '#8a97a6',
                        callback(value) { return `${value}%`; },
                    },
                    grid: { color: 'rgba(148, 163, 184, 0.08)' },
                },
            },
        },
    });

    chartRegistry.set(canvasId, chart);
}

function renderNetworkTrendChart(networkIn, networkOut) {
    const canvasId = 'chart-fleet-trend-network';
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;

    const rangeHours = toNumber(networkIn?.range_hours, 24);
    const labels = Array.isArray(networkIn?.labels) ? networkIn.labels.map((l) => formatTrendLabel(l, rangeHours)) : [];
    const inValues = Array.isArray(networkIn?.values) ? networkIn.values : [];
    const outValues = Array.isArray(networkOut?.values) ? networkOut.values : [];

    // Auto-scale: find peak value and pick unit
    const allValues = [...inValues, ...outValues].filter(Boolean);
    const peak = allValues.length ? Math.max(...allValues) : 0;
    let divisor = 1;
    let unit = 'bps';
    if (peak >= 1e9) { divisor = 1e9; unit = 'Gbps'; }
    else if (peak >= 1e6) { divisor = 1e6; unit = 'Mbps'; }
    else if (peak >= 1e3) { divisor = 1e3; unit = 'Kbps'; }

    const scaleValues = (arr) => arr.map((v) => v !== null ? +(v / divisor).toFixed(2) : null);

    const ctx = canvas.getContext('2d');
    const gradIn = makeGradient(ctx, canvas, '#74c0fc');
    const gradOut = makeGradient(ctx, canvas, '#f38ba8');

    const existing = chartRegistry.get(canvasId);
    if (existing) { existing.destroy(); chartRegistry.delete(canvasId); }

    const chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: `In (${unit})`,
                    data: scaleValues(inValues),
                    borderColor: '#74c0fc',
                    backgroundColor: gradIn,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true,
                },
                {
                    label: `Out (${unit})`,
                    data: scaleValues(outValues),
                    borderColor: '#f38ba8',
                    backgroundColor: gradOut,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label(context) {
                            return `${context.dataset.label}: ${context.parsed.y?.toFixed?.(2)}`;
                        },
                    },
                },
            },
            scales: {
                x: { ticks: { color: '#8a97a6', maxTicksLimit: 6 }, grid: { color: 'rgba(148,163,184,0.08)' } },
                y: {
                    min: 0,
                    ticks: {
                        color: '#8a97a6',
                        callback(value) { return `${value} ${unit}`; },
                    },
                    grid: { color: 'rgba(148,163,184,0.08)' },
                },
            },
        },
    });
    chartRegistry.set(canvasId, chart);
}

function renderLatencyTrendChart(latencyData) {
    const canvasId = 'chart-fleet-trend-latency';
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;

    const rangeHours = toNumber(latencyData?.range_hours, 24);
    const labels = Array.isArray(latencyData?.labels) ? latencyData.labels.map((l) => formatTrendLabel(l, rangeHours)) : [];
    const values = Array.isArray(latencyData?.values) ? latencyData.values : [];

    const avgLatency = values.length ? (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1) : null;
    safeText('val-fleet-latency-avg', avgLatency !== null ? `Avg: ${avgLatency} ms` : 'No data');

    const ctx = canvas.getContext('2d');
    const gradient = makeGradient(ctx, canvas, '#a9dc76');

    const existing = chartRegistry.get(canvasId);
    if (existing) { existing.destroy(); chartRegistry.delete(canvasId); }

    if (!values.length) {
        safeText('fleet-trend-latency-meta', 'No ICMP data available');
        return;
    }

    const chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Latency (ms)',
                data: values,
                borderColor: '#a9dc76',
                backgroundColor: gradient,
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.3,
                fill: true,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label(context) { return `Latency: ${context.parsed.y?.toFixed?.(1)} ms`; },
                    },
                },
            },
            scales: {
                x: { ticks: { color: '#8a97a6', maxTicksLimit: 6 }, grid: { color: 'rgba(148,163,184,0.08)' } },
                y: {
                    min: 0,
                    ticks: {
                        color: '#8a97a6',
                        callback(value) { return `${value} ms`; },
                    },
                    grid: { color: 'rgba(148,163,184,0.08)' },
                },
            },
        },
    });
    chartRegistry.set(canvasId, chart);
}

function renderTrendSection(trends = {}, p95 = {}) {
    // Pass range_hours down to each trend so labels format correctly
    const rangeHours = toNumber(trends.range_hours, 24);
    const withRange = (series) => series ? { ...series, range_hours: rangeHours } : {};

    // Update "Peak" labels from per-bucket peak arrays (last non-null value as proxy)
    const lastPeak = (series) => {
        const peakArr = Array.isArray(series?.peak) ? series.peak : [];
        const vals = peakArr.filter((v) => v !== null && v !== undefined);
        return vals.length ? vals[vals.length - 1] : null;
    };
    safeText('val-fleet-p95-cpu', `Peak: ${formatPercent(lastPeak(trends.cpu))}`);
    safeText('val-fleet-p95-mem', `Peak: ${formatPercent(lastPeak(trends.memory))}`);
    safeText('val-fleet-p95-disk', `Peak: ${formatPercent(lastPeak(trends.disk))}`);

    // Update range label in panel header
    safeText('val-trends-range-label', trends.range_label || '24h');

    renderTrendChart('cpu', withRange(trends.cpu || {}), 'fleet-trend-cpu-meta');
    renderTrendChart('memory', withRange(trends.memory || {}), 'fleet-trend-memory-meta');
    renderTrendChart('disk', withRange(trends.disk || {}), 'fleet-trend-disk-meta');
    renderNetworkTrendChart(withRange(trends.network_in || {}), withRange(trends.network_out || {}));
    renderLatencyTrendChart(withRange(trends.latency || {}));
}

function updateFilterButtonLabels(filters = {}) {
    document.querySelectorAll('[data-server-filter]').forEach((button) => {
        const filter = button.getAttribute('data-server-filter') || 'all';
        let label = 'All';
        if (filter === 'problem') label = 'Problems';
        if (filter === 'healthy') label = 'Healthy';
        const count = Number(filters[filter] ?? 0);
        button.textContent = `${label} (${count})`;
        button.classList.toggle('active', filter === currentFilter);
    });
}

export function renderServerHealthSummary(payload) {
    const counts = payload?.counts || {};
    safeText('val-servers-total', counts.total ?? 0);
    safeText('val-servers-healthy', counts.healthy ?? 0);
    safeText('val-servers-warning', counts.warning ?? 0);
    safeText('val-servers-critical', counts.critical ?? 0);
    safeText('val-servers-offline', counts.offline ?? 0);
    updateFilterButtonLabels(payload?.filters || {});
}

export function renderFleetOverview(data) {
    if (!data) return;

    const health = data.health || {};
    const countsText = `${health.healthy ?? 0} healthy, ${(health.warning ?? 0) + (health.critical ?? 0) + (health.offline ?? 0)} degraded`;
    const fleetState = data?.dominant_issue
        ? normalizeSeverityLabel(data.dominant_issue.severity)
        : (data?.impact_summary?.affected_servers > 0 ? 'Warning' : 'Healthy');

    safeText('val-fleet-health-percent', `${Number(data?.impact_summary?.fleet_pct || 0).toFixed(0)}%`);
    safeText('val-fleet-health-state', fleetState);
    safeText('val-fleet-health-counts', countsText);

    const healthCard = document.getElementById('card-fleet-health');
    if (healthCard) {
        healthCard.classList.remove('state-critical', 'state-warning');
        if (fleetState === 'Critical') {
            healthCard.classList.add('state-critical');
        } else if (fleetState === 'Warning') {
            healthCard.classList.add('state-warning');
        }
    }

    const cpuCard = data.metric_cards?.cpu || {};
    const memoryCard = data.metric_cards?.memory || {};
    const diskCard = data.metric_cards?.disk || {};

    safeText('val-fleet-avg-cpu', formatPercent(cpuCard.value));
    safeText('val-fleet-avg-mem', formatPercent(memoryCard.value));
    safeText('val-fleet-avg-disk', formatPercent(diskCard.value));

    updateFleetCardState('fleet-card-cpu', 'val-fleet-cpu-state', 'val-fleet-cpu-impact', 'val-fleet-cpu-delta', cpuCard);
    updateFleetCardState('fleet-card-memory', 'val-fleet-memory-state', 'val-fleet-memory-impact', 'val-fleet-memory-delta', memoryCard);
    updateFleetCardState('fleet-card-disk', 'val-fleet-disk-state', 'val-fleet-disk-impact', 'val-fleet-disk-delta', diskCard);

    safeText(
        'val-fleet-uptime',
        `${formatPercent(data?.uptime?.current_24h_pct)} last 24h (${(Number(data?.uptime?.delta_pct || 0) >= 0 ? '+' : '') + Number(data?.uptime?.delta_pct || 0).toFixed(1)}% vs yesterday)`
    );

    renderBanner(data.dominant_issue);
    renderImpactSummary(data.impact_summary || {});
    renderActiveIssues(data.active_issues || []);
    renderTrendSection(data.trends || {}, data.p95 || {});
    renderKpiWorstServer(data.servers || []);
    updateFilterButtonLabels(data.filters || {});
}

export function renderServerHealthTable(payload) {
    renderEnhancedServerTable(payload);
}

export function renderEnhancedServerTable(payload) {
    const tableBody = document.getElementById('table-server-health-body');
    if (!tableBody) return;

    const servers = filteredServers(payload);
    if (!servers.length) {
        patchKeyedTableRows(tableBody, [], {
            emptyColSpan: 10,
            emptyMessage: currentFilter === 'healthy' ? 'No healthy servers in current scope' : 'No servers match filter',
            emptyClassName: 'text-center text-secondary p-3',
        });
        return;
    }

    patchKeyedTableRows(tableBody, servers, {
        getKey: (server, index) => server.device_id || server.ip || `server-${index}`,
        renderCells: (server) => {
            const name = server.hostname || server.device_name || server.ip || 'Unknown';
            const stateLabel = normalizeSeverityLabel(server.health);
            const stateBadge = STATE_TO_BADGE[stateLabel] || 'tactical-badge-secondary';
            const issueLabel = server.primary_issue?.metric_label || (stateLabel === 'Healthy' ? 'Nominal' : 'Attention Required');
            const issueValue = server.primary_issue?.formatted_value || formatPercent(server.memory_usage, 1, 'N/A');
            const lastSeenLabel = server.last_seen ? timeAgo(server.last_seen) : 'Never';
            const lastSeenExact = server.last_seen ? new Date(server.last_seen).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : '-';

            return `
                <td>
                    <div class="fw-bold text-truncate" style="max-width: 210px;">${escapeHtml(name)}</div>
                    <div class="small text-secondary font-monospace">${escapeHtml(server.ip || '-')}</div>
                </td>
                <td>
                    <div class="d-flex flex-column gap-1">
                        <span class="badge ${stateBadge}">${escapeHtml(stateLabel)}</span>
                        <div class="small text-secondary">${escapeHtml(issueLabel)}${issueValue && issueValue !== 'N/A' ? ` - ${escapeHtml(issueValue)}` : ''}</div>
                    </div>
                </td>
                <td class="${(toNumber(server.cpu_usage, 0) >= 95 ? 'text-danger fw-bold' : toNumber(server.cpu_usage, 0) >= 80 ? 'text-warning fw-bold' : '')}">${formatPercent(server.cpu_usage)}</td>
                <td class="${(toNumber(server.memory_usage, 0) >= 95 ? 'text-danger fw-bold' : toNumber(server.memory_usage, 0) >= 85 ? 'text-warning fw-bold' : '')}">${formatPercent(server.memory_usage)}</td>
                <td class="${(toNumber(server.disk_usage, 0) >= 95 ? 'text-danger fw-bold' : toNumber(server.disk_usage, 0) >= 80 ? 'text-warning fw-bold' : '')}">${formatPercent(server.disk_usage)}</td>
                <td class="d-none d-xl-table-cell">
                    <div class="fw-semibold">${formatMs(server.latency)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="${toNumber(server.packet_loss, 0) > 0 ? 'text-warning fw-semibold' : 'text-secondary'}">${formatPercent(server.packet_loss, 1)}</div>
                </td>
                <td class="d-none d-xl-table-cell">
                    <div class="text-secondary">${formatMs(server.jitter)}</div>
                </td>
                <td>
                    <div class="fw-semibold text-nowrap">${escapeHtml(lastSeenLabel)}</div>
                    <div class="small text-secondary text-nowrap">${escapeHtml(lastSeenExact)}</div>
                </td>
                <td class="text-end">
                    <div class="server-action-group">
                        <button type="button" class="btn btn-sm btn-dark border-secondary px-2 server-modal-btn" data-device-id="${escapeHtml(server.device_id)}" title="Investigate">
                            <i class="fas fa-chart-line"></i>
                        </button>
                        <a href="/devices/${encodeURIComponent(server.device_id)}/server-monitoring" class="btn btn-sm btn-dark border-secondary px-2" title="Open Details">
                            <i class="fas fa-search"></i>
                        </a>
                        <button type="button" class="btn btn-sm btn-dark border-secondary px-2" title="Restart unavailable" disabled>
                            <i class="fas fa-power-off"></i>
                        </button>
                    </div>
                </td>
            `;
        },
        applyRow: (row, server) => {
            const tone = normalizeSeverityClass(server.primary_issue?.severity || server.health);
            row.className = `server-health-row row-${tone}`;
            row.dataset.id = server.device_id || '';
            row.onclick = () => {
                if (server.device_id) {
                    openServerModal(server.device_id);
                }
            };

            row.querySelectorAll('.server-modal-btn').forEach((button) => {
                button.addEventListener('click', (event) => {
                    event.stopPropagation();
                    if (server.device_id) {
                        openServerModal(server.device_id);
                    }
                });
            });

            row.querySelectorAll('a, button').forEach((control) => {
                if (!control.classList.contains('server-modal-btn')) {
                    control.addEventListener('click', (event) => event.stopPropagation());
                }
            });
        },
    });
}

export function initServerHealthTable() {
    return;
}

function renderKpiWorstServer(servers = []) {
    // For each metric, find the worst server by that metric and show in the KPI card
    const metricMap = [
        { metric: 'cpu_usage', elId: 'val-fleet-cpu-worst' },
        { metric: 'memory_usage', elId: 'val-fleet-memory-worst' },
        { metric: 'disk_usage', elId: 'val-fleet-disk-worst' },
    ];
    for (const { metric, elId } of metricMap) {
        const online = servers.filter((s) => s.health !== 'Offline' && s[metric] !== null && s[metric] !== undefined);
        if (!online.length) { safeText(elId, ''); continue; }
        const worst = online.reduce((a, b) => (toNumber(a[metric], 0) >= toNumber(b[metric], 0) ? a : b));
        const name = worst.hostname || worst.device_name || worst.ip || '?';
        const val = formatPercent(worst[metric]);
        safeText(elId, `↑ ${escapeHtml(name.length > 14 ? name.slice(0, 12) + '…' : name)}: ${val}`);
    }
}

export function bindRangePills(onRangeChange) {
    document.querySelectorAll('.fleet-range-pill[data-range]').forEach((pill) => {
        pill.addEventListener('click', () => {
            const range = pill.getAttribute('data-range') || '24h';
            activeRange = range;
            document.querySelectorAll('.fleet-range-pill').forEach((p) => p.classList.toggle('active', p === pill));
            if (typeof onRangeChange === 'function') onRangeChange(range);
        });
    });
}

export function getActiveRange() {
    return activeRange;
}

export function setServerHealthFilter(filter) {
    currentFilter = String(filter || 'all').toLowerCase();
    if (typeof document === 'undefined') {
        return;
    }
    document.querySelectorAll('[data-server-filter]').forEach((button) => {
        button.classList.toggle('active', (button.getAttribute('data-server-filter') || 'all') === currentFilter);
    });
}
