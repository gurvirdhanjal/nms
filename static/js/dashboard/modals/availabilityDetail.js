import { patchKeyedTableRows, setTableMessageRow } from '../domPatch.js';
import { formatPercent, formatNumber } from '../utils.js';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function toFiniteNumber(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function getBucketMinutes(rows) {
    const bucketHours = Number(rows?.[0]?.bucket_hours ?? 1) || 1;
    return bucketHours * 60;
}

function getIntervalLabel(rows) {
    const bucketMinutes = getBucketMinutes(rows);
    return bucketMinutes >= 60
        ? `${bucketMinutes / 60}h intervals`
        : `${bucketMinutes}m intervals`;
}

function getWindowDescription(rows) {
    const bucketCount = Array.isArray(rows) ? rows.length : 0;
    return bucketCount === 1 ? '1 bucket' : `${bucketCount} buckets`;
}

function getAxisLabel(isoString, bucketHours = 1) {
    if (!isoString) return '--';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return '--';
    if (bucketHours >= 24) {
        return date.toLocaleDateString('en-IN', { month: 'short', day: '2-digit', timeZone: 'Asia/Kolkata' });
    }
    if (bucketHours >= 12) {
        const day = date.toLocaleDateString('en-IN', { month: 'short', day: '2-digit', timeZone: 'Asia/Kolkata' });
        const hour = date.toLocaleTimeString('en-IN', { hour: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
        return `${day} ${hour}`;
    }
    return date.toLocaleTimeString('en-IN', { hour: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

export function formatAvailabilityHour(isoString) {
    if (!isoString) return 'Unknown';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

export function getAvailabilityClass(value) {
    if (value >= 99) return 'avail-excellent';
    if (value >= 95) return 'avail-good';
    if (value >= 90) return 'avail-warning';
    return 'avail-bad';
}

export function buildAvailabilitySummary(heatmap) {
    const rows = Array.isArray(heatmap) ? heatmap : [];
    const observed = rows
        .map((entry) => ({
            ...entry,
            value: toFiniteNumber(entry?.value),
            online: Number(entry?.online ?? 0),
            total: Number(entry?.total ?? 0),
        }))
        .filter((entry) => entry.total > 0 && entry.value !== null);

    if (observed.length === 0) {
        return {
            observedCount: 0,
            averagePct: null,
            criticalCount: 0,
            stableCount: 0,
            worst: null,
        };
    }

    const averagePct = observed.reduce((sum, entry) => sum + entry.value, 0) / observed.length;
    const criticalCount = observed.filter((entry) => entry.value < 90).length;
    const stableCount = observed.filter((entry) => entry.value >= 99).length;
    const worst = observed.reduce((lowest, entry) => (lowest === null || entry.value < lowest.value ? entry : lowest), null);

    return {
        observedCount: observed.length,
        averagePct,
        criticalCount,
        stableCount,
        worst: worst
            ? {
                value: worst.value,
                online: worst.online,
                total: worst.total,
                time: worst.time,
                label: formatAvailabilityHour(worst.time),
            }
            : null,
    };
}

function renderAvailabilitySummary(heatmap, summaryEl) {
    if (!summaryEl) return;

    const rows = Array.isArray(heatmap) ? heatmap : [];
    const intervalLabel = getIntervalLabel(rows);
    const windowDescription = getWindowDescription(rows);
    const summary = buildAvailabilitySummary(heatmap);
    if (!summary.observedCount) {
        summaryEl.innerHTML = `<div class="availability-summary-empty">No observed buckets in this ${windowDescription} window.</div>`;
        return;
    }

    const worstMeta = summary.worst
        ? `${summary.worst.label} · ${summary.worst.online}/${summary.worst.total} online`
        : `No ${intervalLabel} sample`;

    summaryEl.innerHTML = `
        <div class="availability-summary-kpi">
            <div class="availability-summary-label">Avg Window</div>
            <div class="availability-summary-value">${formatPercent(summary.averagePct)}</div>
            <div class="availability-summary-meta">${summary.observedCount}/${rows.length} buckets observed</div>
        </div>
        <div class="availability-summary-kpi">
            <div class="availability-summary-label">Worst Interval</div>
            <div class="availability-summary-value">${summary.worst ? formatPercent(summary.worst.value) : '-'}</div>
            <div class="availability-summary-meta">${escapeHtml(worstMeta)}</div>
        </div>
        <div class="availability-summary-kpi">
            <div class="availability-summary-label">Critical Buckets</div>
            <div class="availability-summary-value">${summary.criticalCount}</div>
            <div class="availability-summary-meta">${escapeHtml(intervalLabel)} below 90% uptime</div>
        </div>
        <div class="availability-summary-kpi">
            <div class="availability-summary-label">Stable Buckets</div>
            <div class="availability-summary-value">${summary.stableCount}</div>
            <div class="availability-summary-meta">${escapeHtml(intervalLabel)} at 99% or better</div>
        </div>
    `;
}

function renderAvailabilityAxis(heatmap, axisEl) {
    if (!axisEl) return;
    const rows = Array.isArray(heatmap) ? heatmap : [];
    if (!rows.length) {
        axisEl.innerHTML = '';
        return;
    }

    const bucketHours = Number(rows[0]?.bucket_hours ?? 1) || 1;
    axisEl.style.gridTemplateColumns = `repeat(${rows.length}, minmax(24px, 1fr))`;
    axisEl.innerHTML = rows.map((entry, index) => {
        const showMajor = index % 4 === 0;
        const label = showMajor ? getAxisLabel(entry?.time, bucketHours) : '·';
        return `<div class="availability-axis-label ${showMajor ? 'is-major' : 'is-minor'}">${escapeHtml(label)}</div>`;
    }).join('');
}

export function renderAvailabilityHeatmap(heatmap, targetEl, options = {}) {
    const el = targetEl;
    if (!el) return;

    const { axisEl = null, summaryEl = null } = options;
    renderAvailabilitySummary(heatmap, summaryEl);
    renderAvailabilityAxis(heatmap, axisEl);

    if (!Array.isArray(heatmap) || heatmap.length === 0) {
        el.innerHTML = '<div class="text-secondary">No availability data for the selected range.</div>';
        return;
    }

    el.style.gridTemplateColumns = `repeat(${heatmap.length}, minmax(24px, 1fr))`;
    const cells = heatmap.map((entry, index) => {
        const online = Number(entry?.online ?? 0);
        const total = Number(entry?.total ?? 0);
        const hasData = total > 0;
        const value = hasData ? Number(entry?.value ?? 0) : 0;
        const className = hasData ? getAvailabilityClass(value) : 'avail-unknown';
        const timeLabel = formatAvailabilityHour(entry?.time) || `Bucket ${index + 1}`;
        const tooltip = hasData
            ? `${timeLabel} · ${formatPercent(value)} · ${online}/${total} online`
            : `${timeLabel} · No data`;
        const tooltipText = escapeHtml(tooltip);
        return `<div class="availability-cell ${className}" role="img" aria-label="${tooltipText}" title="${tooltipText}"></div>`;
    });

    el.innerHTML = cells.join('');
}

function availabilityToneClass(value, mode) {
    const numeric = Number(value ?? 0);
    if (mode === 'downtime') {
        if (numeric <= 1) return 'tone-excellent';
        if (numeric <= 5) return 'tone-good';
        if (numeric <= 15) return 'tone-warning';
        return 'tone-bad';
    }

    if (numeric >= 99) return 'tone-excellent';
    if (numeric >= 95) return 'tone-good';
    if (numeric >= 90) return 'tone-warning';
    return 'tone-bad';
}

export function renderAvailabilityRows(rows, tbody, mode) {
    if (!tbody) return;
    if (!Array.isArray(rows) || rows.length === 0) {
        const windowDescription = getWindowDescription(rows);
        const emptyMessage = mode === 'worst'
            ? 'No availability records yet.'
            : `No downtime recorded in this ${windowDescription} window.`;
        setTableMessageRow(tbody, 4, emptyMessage, 'text-center text-secondary p-3');
        return;
    }

    if (mode === 'downtime') {
        patchKeyedTableRows(tbody, rows, {
            getKey: (row, index) => row.device_id || row.ip || `downtime-${index}`,
            renderCells: (row) => {
                const name = escapeHtml(row.device_name || 'Unknown');
                const type = escapeHtml(row.device_type || 'Unknown');
                const ip = escapeHtml(row.ip || '-');
                const downIntervals = formatNumber(row.down_intervals ?? row.offline_scans ?? 0);
                const downtimePctRaw = Number(row.downtime_pct ?? 0);
                const downtimePct = formatPercent(downtimePctRaw);

                return `
                    <td>
                        <div class="availability-device-name">${name}</div>
                        <div class="availability-device-meta">${type}</div>
                    </td>
                    <td><span class="availability-inline-pill availability-inline-pill-ip">${ip}</span></td>
                    <td><span class="availability-inline-pill availability-inline-pill-count">${downIntervals}</span></td>
                    <td><span class="availability-inline-pill ${availabilityToneClass(downtimePctRaw, 'downtime')}">${downtimePct}</span></td>
                `;
            }
        });
        return;
    }

    patchKeyedTableRows(tbody, rows, {
        getKey: (row, index) => row.device_id || row.ip || `worst-${index}`,
        renderCells: (row) => {
            const name = escapeHtml(row.device_name || 'Unknown');
            const type = escapeHtml(row.device_type || 'Unknown');
            const ip = escapeHtml(row.ip || '-');
            const uptimeRaw = Number(row.uptime_pct ?? 0);
            const uptime = formatPercent(uptimeRaw);
            const downIntervals = formatNumber(row.down_intervals ?? row.offline_scans ?? 0);
            return `
                <td>
                    <div class="availability-device-name">${name}</div>
                    <div class="availability-device-meta">${type}</div>
                </td>
                <td><span class="availability-inline-pill availability-inline-pill-ip">${ip}</span></td>
                <td><span class="availability-inline-pill ${availabilityToneClass(uptimeRaw, 'uptime')}">${uptime}</span></td>
                <td><span class="availability-inline-pill availability-inline-pill-count">${downIntervals}</span></td>
            `;
        }
    });
}
