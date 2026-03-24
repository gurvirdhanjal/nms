import { openServerModal } from '../modals/serverDetailModal.js';
import { timeAgo } from '../utils.js';
import { patchKeyedTableRows } from '../domPatch.js';

let currentFilter = 'all';
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
        container.innerHTML = '<div class="fleet-empty-state">No active server incidents.</div>';
        return;
    }

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

function formatTrendLabel(label) {
    if (!label) return '';
    const parsed = new Date(label);
    if (!Number.isNaN(parsed.getTime())) {
        return parsed.toLocaleTimeString('en-IN', {
            timeZone: 'Asia/Kolkata',
            hour: '2-digit',
            minute: '2-digit',
        });
    }
    return String(label).slice(11, 16) || String(label);
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

    const labels = Array.isArray(trend?.labels) ? trend.labels.map(formatTrendLabel) : [];
    const values = Array.isArray(trend?.values) ? trend.values : [];
    const markers = Array.isArray(trend?.markers) ? trend.markers : [];
    const metaText = trend?.delta === null || trend?.delta === undefined
        ? 'Awaiting enough history for a 24h comparison'
        : `${trend.delta >= 0 ? '+' : ''}${Number(trend.delta).toFixed(1)}% vs previous 24h`;

    safeText(metaId, metaText);

    const datasetColor = metric === 'cpu'
        ? '#74c0fc'
        : metric === 'memory'
            ? '#ffd166'
            : '#a7f3d0';

    const chartData = {
        labels,
        datasets: [
            {
                type: 'line',
                label: metric,
                data: values,
                borderColor: datasetColor,
                backgroundColor: datasetColor,
                pointRadius: 0,
                borderWidth: 2,
                tension: 0.3,
                fill: false,
            },
            {
                type: 'scatter',
                label: `${metric}-markers`,
                data: markers.map((marker) => ({
                    x: labels[marker.index] ?? labels[labels.length - 1] ?? '',
                    y: marker.value,
                })),
                pointRadius: 4,
                pointHoverRadius: 4,
                pointBackgroundColor: markers.map((marker) => marker.state === 'critical' ? '#dc3545' : '#ffc107'),
                pointBorderColor: '#0f1720',
                pointBorderWidth: 1.5,
                showLine: false,
            },
        ],
    };

    const existing = chartRegistry.get(canvasId);
    if (existing) {
        existing.destroy();
        chartRegistry.delete(canvasId);
    }

    const chart = new Chart(canvas, {
        type: 'line',
        data: chartData,
        plugins: [buildTrendPlugin(metric)],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label(context) {
                            return `${context.parsed.y?.toFixed?.(1) ?? context.parsed.y}%`;
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
                    ticks: {
                        color: '#8a97a6',
                        maxTicksLimit: 6,
                    },
                    grid: {
                        color: 'rgba(148, 163, 184, 0.08)',
                    },
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: {
                        color: '#8a97a6',
                        callback(value) {
                            return `${value}%`;
                        },
                    },
                    grid: {
                        color: 'rgba(148, 163, 184, 0.08)',
                    },
                },
            },
        },
    });

    chartRegistry.set(canvasId, chart);
}

function renderTrendSection(trends = {}, p95 = {}) {
    safeText('val-fleet-p95-cpu', `P95: ${formatPercent(p95.cpu)}`);
    safeText('val-fleet-p95-mem', `P95: ${formatPercent(p95.memory)}`);
    safeText('val-fleet-p95-disk', `P95: ${formatPercent(p95.disk)}`);

    renderTrendChart('cpu', trends.cpu || {}, 'fleet-trend-cpu-meta');
    renderTrendChart('memory', trends.memory || {}, 'fleet-trend-memory-meta');
    renderTrendChart('disk', trends.disk || {}, 'fleet-trend-disk-meta');
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

export function setServerHealthFilter(filter) {
    currentFilter = String(filter || 'all').toLowerCase();
    if (typeof document === 'undefined') {
        return;
    }
    document.querySelectorAll('[data-server-filter]').forEach((button) => {
        button.classList.toggle('active', (button.getAttribute('data-server-filter') || 'all') === currentFilter);
    });
}
