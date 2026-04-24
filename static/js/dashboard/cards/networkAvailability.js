/**
 * Card Component: Network Availability
 */
import { formatPercent, checkStale, timeAgo } from '../utils.js';

let chartInstance = null;
let breakdownChart = null;

function getAvailabilitySeverity(percentValue) {
    const value = Number(percentValue || 0);
    if (value >= 99) return 'success';
    if (value >= 94) return 'warning';
    return 'danger';
}

function getThemeAccentColor() {
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--ui-accent').trim();
    return accent || '#2a8f93';
}

export function renderNetworkAvailability(data, trendsData) {
    const cardId = 'card-network-avail';
    const container = document.getElementById(cardId);
    if (!container) return;

    let percentValue = 0;

    // 1. Update Metrics
    if (data && data.devices) {
        const { online_percent, up_percent } = data.devices;
        percentValue = Number(online_percent ?? up_percent ?? 0);
        const valueEl = document.getElementById('val-availability');
        if (valueEl) valueEl.textContent = formatPercent(percentValue);

        const severity = getAvailabilitySeverity(percentValue);

        // Color
        if (valueEl) {
            valueEl.className = `metric-value text-${severity}`;
        }

        const breakdownVal = document.getElementById('val-availability-breakdown');
        if (breakdownVal) {
            breakdownVal.textContent = formatPercent(percentValue);
            breakdownVal.className = `fw-bold text-${severity}`;
        }

        const stateEl = document.getElementById('state-availability');
        if (stateEl) {
            const status = severity === 'success' ? 'Healthy' : severity === 'warning' ? 'Degraded' : 'Critical';
            stateEl.textContent = status;
            stateEl.className = `soc-kpi-status ${status === 'Healthy' ? 'status-healthy' : status === 'Degraded' ? 'status-warning' : 'status-critical'}`;
        }

        const contextEl = document.getElementById('ctx-availability');
        if (contextEl) {
            contextEl.textContent = `history 24h ${data.availability?.history_24h_pct != null ? formatPercent(data.availability.history_24h_pct) : 'n/a'} · current ${formatPercent(percentValue)}`;
        }
    }

    const freshness = getAvailabilityFreshness(data);
    renderAvailabilityTrendSummary(percentValue, trendsData, freshness);

    if (data && data.availability) {
        const hist = data.availability.history_24h_pct ?? 0;
        const histEl = document.getElementById('val-availability-24h');
        if (histEl) histEl.textContent = formatPercent(hist);
    }

    // 2. Update Sparkline (if trend data available)
    if (trendsData && trendsData.availability_trend) {
        renderSparkline(trendsData.availability_trend);
        renderBreakdownTrend(trendsData.availability_trend);
    }

    checkStale(freshness.displayTimestamp, cardId);
}

function getAvailabilityFreshness(data) {
    const snapshotTimestamp = data?.snapshot_generated_at ?? data?.timestamp ?? null;
    const sourceTimestamp = data?.source_data_freshness_at ?? null;
    const snapshotDate = snapshotTimestamp ? new Date(snapshotTimestamp) : null;
    const sourceDate = sourceTimestamp ? new Date(sourceTimestamp) : null;
    const hasSnapshot = snapshotDate instanceof Date && !Number.isNaN(snapshotDate.getTime());
    const hasSource = sourceDate instanceof Date && !Number.isNaN(sourceDate.getTime());
    const displayDate = hasSource ? sourceDate : (hasSnapshot ? snapshotDate : null);
    const staleGapMs = hasSource && hasSnapshot ? Math.max(0, snapshotDate.getTime() - sourceDate.getTime()) : 0;
    const isStaleGap = staleGapMs > (5 * 60 * 1000);

    return {
        displayTimestamp: displayDate ? displayDate.toISOString() : snapshotTimestamp,
        isStaleGap,
        label: displayDate ? `${isStaleGap ? 'Warning: ' : ''}updated ${timeAgo(displayDate.toISOString())}` : 'updated n/a'
    };
}

function getRangeLabel(range) {
    switch ((range || '').toLowerCase()) {
        case '7d':
            return 'last 7d';
        case '30d':
            return 'last 30d';
        case '1h':
            return 'last 1h';
        case '24h':
        default:
            return 'last 24h';
    }
}

function getFirstTrendPoint(trendData = []) {
    if (!Array.isArray(trendData) || trendData.length === 0) return null;

    // Prefer first bucket with actual data when backend provides `total`
    const withData = trendData.find(p => typeof p?.total === 'number' && p.total > 0);
    if (withData) return withData;

    // Fallback for older cached payloads without totals
    const nonZero = trendData.find(p => typeof p?.value === 'number' && p.value > 0);
    if (nonZero) return nonZero;
    return trendData.find(p => typeof p?.value === 'number') || null;
}

function formatDelta(deltaValue) {
    const rounded = Math.abs(Number(deltaValue || 0));
    if (rounded >= 10) return Math.round(rounded).toString();
    const oneDecimal = Math.round(rounded * 10) / 10;
    return Number.isInteger(oneDecimal) ? oneDecimal.toFixed(0) : oneDecimal.toFixed(1);
}

function renderAvailabilityTrendSummary(currentPercent, trendsData, freshness = null) {
    const subEl = document.getElementById('sub-availability-trend');
    if (!subEl) return;

    const trend = trendsData?.availability_trend;
    const firstPoint = getFirstTrendPoint(trend);
    const rangeLabel = getRangeLabel(trendsData?.range);
    const freshnessClass = freshness?.isStaleGap ? 'text-warning' : 'text-secondary';
    const freshnessMarkup = `<span class="${freshnessClass}">${freshness?.label || 'updated n/a'}</span>`;

    if (!firstPoint || typeof firstPoint.value !== 'number') {
        subEl.innerHTML = `<span class="text-secondary">Availability: ${formatPercent(currentPercent)} (${rangeLabel})</span> <span class="text-secondary">·</span> ${freshnessMarkup}`;
        const trendEl = document.getElementById('trend-availability');
        if (trendEl) trendEl.innerHTML = '&rarr; steady';
        return;
    }

    const delta = Number(currentPercent) - Number(firstPoint.value);
    const deltaText = formatDelta(delta);

    let arrow = '&rarr;';
    let deltaClass = 'text-secondary';
    if (delta > 0.05) {
        arrow = '&uarr;';
        deltaClass = 'text-success';
    } else if (delta < -0.05) {
        arrow = '&darr;';
        deltaClass = 'text-danger';
    }

    subEl.innerHTML = `Availability: ${formatPercent(currentPercent)} <span class="${deltaClass} fw-semibold">${arrow} ${deltaText}%</span> <span class="text-secondary">(${rangeLabel})</span> <span class="text-secondary">·</span> ${freshnessMarkup}`;
    const trendEl = document.getElementById('trend-availability');
    if (trendEl) {
        trendEl.innerHTML = `<span class="${deltaClass}">${arrow} ${deltaText}%</span>`;
    }
}

function renderSparkline(trendData) {
    const canvas = document.getElementById('chart-availability-spark');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const labels = trendData.map(d => d.time);
    const values = trendData.map(d => d.value);

    // Destroy previous instance if needed
    if (chartInstance) {
        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = values;
        chartInstance.update('none'); // Update without full animation
        return;
    }

    // Destroy any stale instance on the canvas
    // @ts-ignore
    Chart.getChart(canvas)?.destroy();
    // Create new Chart
    // @ts-ignore
    const accent = getThemeAccentColor();
    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                borderColor: accent,
                borderWidth: 2,
                backgroundColor: 'rgba(42, 143, 147, 0.12)',
                fill: true,
                pointRadius: 0,
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
}

function renderBreakdownTrend(trendData) {
    const canvas = document.getElementById('chart-availability-breakdown');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const labels = trendData.map(d => d.time);
    const values = trendData.map(d => d.value);

    if (breakdownChart) {
        breakdownChart.data.labels = labels;
        breakdownChart.data.datasets[0].data = values;
        breakdownChart.update('none');
        return;
    }

    // Destroy any stale instance on the canvas
    // @ts-ignore
    Chart.getChart(canvas)?.destroy();
    // @ts-ignore
    const accent = getThemeAccentColor();
    breakdownChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                borderColor: accent,
                borderWidth: 2,
                backgroundColor: 'rgba(42, 143, 147, 0.09)',
                fill: true,
                pointRadius: 0,
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false },
                y: { display: false, min: 0, max: 100 }
            },
            animation: false
        }
    });
}
