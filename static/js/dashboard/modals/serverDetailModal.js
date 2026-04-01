/**
 * serverDetailModal.js — Lightweight operational snapshot modal.
 *
 * Loads /api/server/<id>/snapshot (fast, single device) instead of the
 * heavy telemetry endpoint. Charts are lazy-initialised 400ms after open
 * to avoid blocking the initial paint.
 */

let modalInstance = null;
let modalElement = null;
let currentDeviceId = null;
let refreshTimer = null;
let chartInitTimer = null;
let cpuChart = null;

// ── Formatters ────────────────────────────────────────────────────────────────

function formatUptime(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const total = Math.max(0, Math.floor(Number(seconds)));
    const d = Math.floor(total / 86400);
    const h = Math.floor((total % 86400) / 3600);
    const m = Math.floor((total % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

function formatDateTime(isoStr) {
    if (!isoStr) return '—';
    const s = String(isoStr);
    const d = new Date(/z$/i.test(s) ? s : s + 'Z');
    return Number.isNaN(d.getTime()) ? '—'
        : d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' });
}

function metricColor(value, warn, crit) {
    if (value === null || value === undefined) return 'var(--text-muted)';
    if (value >= crit) return 'var(--danger, #ef4444)';
    if (value >= warn) return 'var(--amber, #f59e0b)';
    return 'var(--ui-accent, #00bcd4)';
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

function qs(selector) {
    return modalElement?.querySelector(selector) ?? null;
}

function setText(id, value) {
    const el = qs(`#${id}`);
    if (el) el.textContent = value ?? '—';
}

function setColor(id, color) {
    const el = qs(`#${id}`);
    if (el) el.style.color = color;
}

// ── Resource bar ─────────────────────────────────────────────────────────────

function renderResourceBar(containerId, value, warn = 80, crit = 90) {
    const el = qs(`#${containerId}`);
    if (!el) return;
    if (value === null || value === undefined) {
        el.innerHTML = '<div class="snap-res-value" style="color:var(--text-muted)">—</div>';
        return;
    }
    const pct = Math.min(100, Math.max(0, Number(value)));
    const color = pct >= crit ? 'var(--danger, #ef4444)'
        : pct >= warn ? 'var(--amber, #f59e0b)'
        : 'var(--ui-accent, #00bcd4)';
    const label = pct >= crit ? 'Critical' : pct >= warn ? 'High' : 'Normal';
    el.innerHTML = `
        <div class="snap-res-value" style="color:${color}">${pct.toFixed(1)}%</div>
        <div class="snap-res-bar-track">
            <div class="snap-res-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="snap-res-status-lbl" style="color:${color}">${label}</div>`;
}

// ── Uptime timeline ───────────────────────────────────────────────────────────

function renderTimeline(timeline) {
    const bar = qs('#snap-timeline-bar');
    if (!bar) return;
    bar.innerHTML = (timeline || []).map(status => {
        const bg = status === 'up'      ? 'var(--ui-accent, #00bcd4)'
                 : status === 'partial' ? 'var(--amber, #f59e0b)'
                 : status === 'down'    ? 'var(--danger, #ef4444)'
                 :                        'rgba(255,255,255,0.07)';
        const title = status === 'up' ? 'Online' : status === 'partial' ? 'Partial' : status === 'down' ? 'Offline' : 'No data';
        return `<span class="snap-tl-seg" style="background:${bg}" title="${title}"></span>`;
    }).join('');
}

// ── Mini CPU chart ────────────────────────────────────────────────────────────

function destroyChart() {
    if (cpuChart) { cpuChart.destroy(); cpuChart = null; }
}

function initMiniCpuChart(labels, data) {
    const canvas = qs('#snap-cpu-canvas');
    if (!canvas || !window.Chart) return;
    destroyChart();

    const displayLabels = labels.map(l => {
        const d = new Date(/z$/i.test(l) ? l : l + 'Z');
        return Number.isNaN(d.getTime()) ? ''
            : d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
    });

    // Current value label in chart header
    const lastVal = [...data].reverse().find(v => v !== null);
    const lbl = qs('#snap-cpu-current-label');
    if (lbl) lbl.textContent = lastVal !== undefined ? `${Number(lastVal).toFixed(1)}%` : '';

    cpuChart = new window.Chart(canvas, {
        type: 'line',
        data: {
            labels: displayLabels,
            datasets: [{
                data,
                borderColor: 'var(--ui-accent, #00bcd4)',
                borderWidth: 1.5,
                fill: true,
                backgroundColor: 'rgba(0,188,212,0.07)',
                pointRadius: 0,
                tension: 0.35,
            }],
        },
        options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: { label: ctx => ` ${ctx.parsed.y?.toFixed(1) ?? '—'}%` },
                },
            },
            scales: {
                x: { display: false },
                y: {
                    min: 0,
                    max: 100,
                    display: true,
                    ticks: {
                        color: 'rgba(255,255,255,0.25)',
                        font: { size: 9 },
                        maxTicksLimit: 4,
                        callback: v => `${v}%`,
                    },
                    grid: { color: 'rgba(255,255,255,0.04)' },
                    border: { display: false },
                },
            },
        },
    });
}

// ── Render snapshot payload ───────────────────────────────────────────────────

function renderSnapshot(data) {
    // Header
    const nameEl = qs('#server-modal-device-name');
    const ipEl   = qs('#server-modal-device-ip');
    const stEl   = qs('#server-modal-device-status');
    if (nameEl) nameEl.textContent = data.device_name || '—';
    if (ipEl)   ipEl.textContent   = data.ip          || '—';
    if (stEl) {
        const score = Number(data.health_score) || 0;
        if (score >= 90)      { stEl.textContent = 'Healthy';  stEl.style.color = 'var(--ui-accent, #00bcd4)'; }
        else if (score >= 70) { stEl.textContent = 'Warning';  stEl.style.color = 'var(--amber, #f59e0b)';     }
        else                  { stEl.textContent = 'Critical'; stEl.style.color = 'var(--danger, #ef4444)';    }
    }

    // Health summary tiles
    setText('snap-uptime',       formatUptime(data.uptime_seconds));
    setText('snap-availability', data.availability_24h_pct !== null && data.availability_24h_pct !== undefined
        ? `${data.availability_24h_pct}%` : '—');
    setText('snap-downtime', (() => {
        const m = data.downtime_24h_min;
        if (m === null || m === undefined) return '—';
        if (m === 0) return '0 min';
        return m < 60 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
    })());
    setText('snap-ping',     data.ping_ms     !== null && data.ping_ms     !== undefined ? `${data.ping_ms} ms`     : '—');
    setText('snap-pkt-loss', data.packet_loss_pct !== null && data.packet_loss_pct !== undefined ? `${data.packet_loss_pct}%` : '—');

    // Colour coding
    const avail = data.availability_24h_pct;
    if (avail !== null && avail !== undefined) {
        setColor('snap-availability', avail >= 99.5 ? 'var(--ui-accent)' : avail >= 95 ? 'var(--amber, #f59e0b)' : 'var(--danger, #ef4444)');
    }
    setColor('snap-ping',     metricColor(data.ping_ms, 100, 200));
    setColor('snap-pkt-loss', metricColor(data.packet_loss_pct, 1, 5));

    // Network health
    setText('snap-net-ping',    data.ping_ms     !== null ? `${data.ping_ms} ms`     : '—');
    setText('snap-net-jitter',  data.jitter_ms   !== null ? `${data.jitter_ms} ms`   : '—');
    setText('snap-net-loss',    data.packet_loss_pct !== null ? `${data.packet_loss_pct}%` : '—');
    const nsLabel = { stable: 'Stable', warning: 'Warning', degraded: 'Degraded', 'no data': 'No Data' }[data.network_status] || '—';
    setText('snap-net-status', nsLabel);
    setColor('snap-net-status',
        data.network_status === 'stable'   ? 'var(--ui-accent)'       :
        data.network_status === 'warning'  ? 'var(--amber, #f59e0b)'  :
        data.network_status === 'degraded' ? 'var(--danger, #ef4444)' :
        'var(--text-muted)');

    // Last seen
    setText('snap-last-seen', formatDateTime(data.last_seen));

    // Resources
    renderResourceBar('snap-res-cpu',  data.cpu_current);
    renderResourceBar('snap-res-mem',  data.memory_current);
    renderResourceBar('snap-res-disk', data.disk_current, 85, 95);

    // Uptime timeline
    renderTimeline(data.uptime_timeline);

    // SNMP section
    const snmpSec = qs('#snap-snmp-section');
    if (snmpSec) {
        if (data.snmp_enabled) {
            snmpSec.classList.remove('d-none');
            setText('snap-snmp-version',   data.snmp_version ? `v${data.snmp_version}` : '—');
            setText('snap-snmp-port',      data.snmp_port    || '—');
            setText('snap-snmp-last-poll', data.snmp_last_poll ? formatDateTime(data.snmp_last_poll) : 'Never');
        } else {
            snmpSec.classList.add('d-none');
        }
    }

    // Alerts section
    const alertSec  = qs('#snap-alerts-section');
    const alertList = qs('#snap-alerts-list');
    const alerts = Array.isArray(data.alerts) ? data.alerts : [];
    if (alertSec) {
        if (alerts.length > 0 && alertList) {
            alertSec.classList.remove('d-none');
            alertList.innerHTML = alerts.slice(0, 4).map(a =>
                `<div class="snap-alert-item">${String(a).slice(0, 140)}</div>`
            ).join('');
        } else {
            alertSec.classList.add('d-none');
        }
    }

    // Swap skeleton → content
    qs('#snap-loading')?.classList.add('d-none');
    qs('#snap-content')?.classList.remove('d-none');

    // Lazy CPU chart — give the browser a frame to paint first
    if (chartInitTimer) clearTimeout(chartInitTimer);
    chartInitTimer = setTimeout(() => {
        initMiniCpuChart(data.cpu_chart_labels || [], data.cpu_chart_data || []);
    }, 400);
}

// ── Fetch ─────────────────────────────────────────────────────────────────────

async function loadSnapshot() {
    if (!currentDeviceId) return;
    try {
        const resp = await fetch(`/api/server/${currentDeviceId}/snapshot`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderSnapshot(data);
    } catch (err) {
        const loadEl = qs('#snap-loading');
        if (loadEl) {
            loadEl.innerHTML = `<div style="color:var(--danger,#ef4444);font-size:12px;padding:16px 0">
                Failed to load snapshot: ${String(err.message).slice(0, 100)}</div>`;
        }
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

export function initServerModal() {
    modalElement = document.getElementById('serverDetailsModal');
    if (!modalElement || !window.bootstrap) return;
    if (!modalInstance) modalInstance = new window.bootstrap.Modal(modalElement);

    if (modalElement.dataset.snapBound === 'true') return;
    modalElement.dataset.snapBound = 'true';

    modalElement.addEventListener('hidden.bs.modal', () => {
        if (refreshTimer)  { clearInterval(refreshTimer);  refreshTimer  = null; }
        if (chartInitTimer){ clearTimeout(chartInitTimer); chartInitTimer = null; }
        destroyChart();
        currentDeviceId = null;
        // Reset to skeleton state for next open
        qs('#snap-loading')?.classList.remove('d-none');
        qs('#snap-content')?.classList.add('d-none');
    });
}

export function openServerModal(deviceId) {
    if (!modalInstance) initServerModal();
    if (!modalInstance) return;

    currentDeviceId = deviceId;

    const link = qs('#server-modal-open-page');
    if (link) link.href = `/devices/${deviceId}/server-monitoring`;

    // Show skeleton while loading
    qs('#snap-loading')?.classList.remove('d-none');
    qs('#snap-content')?.classList.add('d-none');
    const nameEl = qs('#server-modal-device-name');
    if (nameEl) nameEl.textContent = 'Loading…';

    modalInstance.show();
    loadSnapshot();

    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(loadSnapshot, 30000);
}
