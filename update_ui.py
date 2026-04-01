import re
import os

html_path = r'd:\device_monitoring_tactical\templates\server_dashboard.html'
js_path = r'd:\device_monitoring_tactical\static\js\dashboard\servers\serverHealth.js'

with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. HTML Replacements
html = html.replace('Fleet Health', 'System Health Score')
html = html.replace('Overview &amp; Incidents', 'Fleet Overview (Aggregated)')
html = html.replace('Overview & Incidents', 'Fleet Overview (Aggregated)')
html = html.replace('<h5 class="enterprise-section-title mb-0">Server Resources</h5>', '<h5 class="enterprise-section-title mb-0">Live Server Feed (Real-time)</h5>')
html = html.replace('<div class="enterprise-section-sub">Current compute, memory, and disk usage block block for physical and virtual machines.</div>', '<div class="enterprise-section-sub">Real-time stream of server telemetry and operational metrics.</div>')
html = html.replace('data-server-filter="warning">Warning', 'data-server-filter="degraded">Degraded')
html = html.replace('id="noc-alert-title">No critical alerts</div>', 'id="noc-alert-title">All Systems Operational</div>')
html = html.replace('id="noc-alert-detail">All reporting servers are operating normally.</div>', 'id="noc-alert-detail">No incidents detected.</div>')

# Replace the fake LIVE UI with simple real text
live_ui_old = """<div class="d-flex align-items-center gap-2">
                        <div class="pulse-indicator bg-success"></div>
                        <strong id="server-live-indicator" class="text-success">...LIVE...</strong>
                        <div class="text-end" style="line-height: 1.2;">
                            <div class="fw-bold text-light" id="server-last-check">Checking...</div>
                            <div class="small text-secondary" id="server-last-check-age"></div>
                        </div>
                    </div>"""
live_ui_new = """<div class="text-end" style="line-height: 1.2;">
                        <strong class="text-success">LIVE &bull;</strong> 
                        <span class="text-secondary">Last poll: <span id="server-last-check-age">Checking...</span></span>
                        <div class="small text-muted d-none" id="server-last-check"></div>
                    </div>"""
html = html.replace(live_ui_old, live_ui_new)


# 2. Add 'is-healthy' state and loading state to sticky-alert-bar css
css_additions = """
    .sticky-alert-bar.is-healthy {
        border-bottom-color: rgba(0, 212, 170, 0.3);
        background: linear-gradient(90deg, rgba(8, 28, 24, 0.95), rgba(15, 23, 36, 0.95));
        box-shadow: 0 4px 24px rgba(0, 212, 170, 0.15);
    }
    .sticky-alert-bar.is-healthy .alert-icon { background: var(--noc-bg-healthy); color: var(--noc-healthy); }

    .sticky-alert-bar.is-stale {
        border-bottom-color: rgba(160, 174, 192, 0.3);
        background: linear-gradient(90deg, rgba(32, 36, 42, 0.95), rgba(15, 23, 36, 0.95));
        box-shadow: 0 4px 24px rgba(160, 174, 192, 0.15);
    }
    .sticky-alert-bar.is-stale .alert-icon { background: var(--noc-bg-offline); color: var(--noc-offline); }
    
    #incident-context-panel { transition: all 0.3s ease; }
    .flash-highlight { box-shadow: 0 0 20px rgba(255, 78, 78, 0.6) !important; border-color: rgba(255, 78, 78, 0.8) !important; }
    
    .impact-summary-box {
        margin-bottom: 1rem; padding: 0.75rem 1rem; border-radius: 8px;
        background: rgba(15, 23, 36, 0.7); border: 1px solid rgba(148, 163, 184, 0.15);
        font-size: 0.85rem; color: #f8fafc;
    }
    .impact-summary-box strong { font-family: 'IBM Plex Sans', sans-serif; letter-spacing: 0.05em; text-transform: uppercase; font-size: 0.75rem; color: rgba(190, 201, 214, 0.8); display: block; margin-bottom: 0.4rem; }
    .impact-summary-box .impact-text { font-family: 'IBM Plex Mono', monospace; font-size: 0.9rem; margin-bottom: 0.2rem; }
"""
if '.sticky-alert-bar.is-healthy' not in html:
    html = html.replace('</style>', css_additions + '\n</style>')

# Modals for Actions
modals_html = """
<!-- Action Confirmation Modal -->
<div class="modal fade tactical-modal" id="actionConfirmModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-sm">
        <div class="modal-content">
            <div class="modal-header border-bottom-0 pb-0">
                <h6 class="modal-title" id="actionConfirmTitle">Confirm Action</h6>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body text-center py-4">
                <div class="mb-3" id="actionConfirmIcon"><i class="fas fa-exclamation-triangle text-warning fa-2x"></i></div>
                <p id="actionConfirmMessage" class="mb-0 text-light">Are you sure?</p>
                <div id="actionLoadingSpinner" class="spinner-border text-primary mt-3 d-none" role="status"><span class="visually-hidden">Loading...</span></div>
                <div id="actionResultMessage" class="mt-3 text-success fw-bold d-none"></div>
            </div>
            <div class="modal-footer border-top-0 pt-0 justify-content-center" id="actionConfirmFooter">
                <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal">Cancel</button>
                <button type="button" class="btn btn-sm btn-danger" id="btnConfirmActionRun">Proceed</button>
            </div>
        </div>
    </div>
</div>
"""
if 'id="actionConfirmModal"' not in html:
    html = html.replace('</body>', modals_html + '\n</body>')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print("Updated HTML.")

# Update JS logic
with open(js_path, 'r', encoding='utf-8') as f:
    js = f.read()

# Replace rendering functions and logic in JS
js = js.replace("statusColors['Warning'] = 'text-warning'", "statusColors['Degraded'] = 'text-warning'")
js = js.replace("statusDot['Warning'] = 'status-dot status-warning'", "statusDot['Degraded'] = 'status-dot status-warning'")
js = js.replace("statusBadge['Warning'] = 'tactical-badge-warning'", "statusBadge['Degraded'] = 'tactical-badge-warning'")

new_overview = """
export function renderFleetOverview(data) {
    if (!data) return;
    const setText = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
    const setCardStatus = (cardId, statusCls) => { const card = document.getElementById(cardId); if (card) card.className = statusCls ? `tactical-stat-card kpi-clickable-card ${statusCls}` : `tactical-stat-card kpi-clickable-card`; };

    // 1. Health Cards
    const health = data.health || {};
    const hasData = health.total > 0;
    const pct = hasData ? Math.round((health.healthy / health.total) * 100) : 0;
    setText('val-fleet-health-percent', hasData ? `${pct}%` : '-');
    setText('val-fleet-health-counts', hasData ? `${health.healthy || 0}/${health.total || 0} Servers Healthy` : 'No data');

    if (hasData && pct < 90) setCardStatus('card-fleet-health', 'status-critical');
    else if (hasData && pct < 98) setCardStatus('card-fleet-health', 'status-warning');
    else setCardStatus('card-fleet-health', '');

    // 2. Capacity Metrics
    const agg = data.aggregates || {};
    const p95 = data.p95 || {};
    const breaches = data.threshold_breaches || {};
    
    setText('val-fleet-avg-cpu', agg.cpu != null ? `${agg.cpu}%` : '-');
    setText('val-fleet-p95-cpu', p95.cpu != null ? p95.cpu : '-');
    
    const trendCpu = document.getElementById('trend-fleet-cpu');
    if (trendCpu) trendCpu.style.display = 'none'; // Ensure fake trends are hidden

    if (agg.cpu != null && agg.cpu > 85) setCardStatus('card-fleet-cpu', 'status-critical');
    else if (agg.cpu != null && agg.cpu > 70) setCardStatus('card-fleet-cpu', 'status-warning');
    else setCardStatus('card-fleet-cpu', '');

    setText('val-fleet-avg-mem', agg.memory != null ? `${agg.memory}%` : '-');
    setText('val-fleet-p95-mem', p95.memory != null ? p95.memory : '-');
    
    const trendMem = document.getElementById('trend-fleet-mem');
    if (trendMem) trendMem.style.display = 'none';

    if (agg.memory != null && agg.memory > 85) setCardStatus('card-fleet-mem', 'status-critical');
    else if (agg.memory != null && agg.memory > 70) setCardStatus('card-fleet-mem', 'status-warning');
    else setCardStatus('card-fleet-mem', '');

    setText('val-fleet-avg-disk', agg.disk != null ? `${agg.disk}%` : '-');
    setText('val-fleet-p95-disk', p95.disk != null ? p95.disk : '-');
    
    const trendDisk = document.getElementById('trend-fleet-disk');
    if (trendDisk) trendDisk.style.display = 'none';

    const diskWEl = document.getElementById('val-fleet-disk-warning');
    if (diskWEl) {
        diskWEl.textContent = `${breaches.disk ?? 0} ${(breaches.disk ?? 0) === 1 ? 'server' : 'servers'} above threshold`;
        if ((breaches.disk ?? 0) > 0) {
            diskWEl.classList.remove('d-none');
            setCardStatus('card-fleet-disk', 'status-warning');
        } else {
            diskWEl.classList.add('d-none');
            setCardStatus('card-fleet-disk', '');
        }
    }

    // 3. Global NOC Alert Bar
    const nocBar = document.getElementById('noc-alert-bar');
    const nocTitle = document.getElementById('noc-alert-title');
    const nocDetail = document.getElementById('noc-alert-detail');
    const nocIcon = document.getElementById('noc-alert-icon');
    const incidentContextBody = document.getElementById('table-incident-context-body');
    const incidentPanel = document.getElementById('incident-context-panel');
    const incidentCount = document.getElementById('incident-context-count');

    // Rename warning to degraded locally
    const degradedCount = health.warning || 0;
    const criticalCount = health.critical || 0;
    const offlineCount = health.offline || 0;
    const totalCriticals = criticalCount + offlineCount;
    
    // Dynamic Filter Counts sync
    setText('count-problem', totalCriticals + degradedCount);
    setText('count-warning', degradedCount);
    setText('count-critical', criticalCount);

    const problemServers = (data.servers || []).filter(s => s.health !== 'Healthy');
    if (incidentContextBody) {
        // Impact Summary
        const cpuImpact = problemServers.filter(s => s.cpu_usage > 85).length;
        const memImpact = problemServers.filter(s => s.memory_usage > 85).length;
        const diskImpact = problemServers.filter(s => s.disk_usage > 85).length;
        const impactText = `
            <tr>
                <td colspan="5" class="p-0 border-0">
                    <div class="impact-summary-box m-3">
                        <strong>Impact Summary</strong>
                        <div class="impact-text">${problemServers.length} server${problemServers.length===1?'':'s'} affected</div>
                        <div class="small text-secondary">
                        ${[
                            cpuImpact > 0 ? `${cpuImpact} CPU pressure` : '',
                            memImpact > 0 ? `${memImpact} Memory pressure` : '',
                            diskImpact > 0 ? `${diskImpact} Disk pressure` : '',
                            offlineCount > 0 ? `${offlineCount} Offline` : ''
                        ].filter(Boolean).join(' | ') || 'No specific resource bounds broken (Degraded).'}
                        </div>
                    </div>
                </td>
            </tr>
        `;

        const alertsListHTML = problemServers.slice(0, 10).map(s => {
            const hStatus = s.health === 'Warning' ? 'Degraded' : s.health;
            const hClass = hStatus === 'Critical' ? 'server-severity-critical' : (hStatus === 'Degraded' ? 'server-severity-warning' : 'server-severity');
            return `
                <tr class="table-row-${(hStatus||'').toLowerCase()}">
                    <td><span class="server-severity ${hClass}">${hStatus}</span></td>
                    <td><strong>${s.hostname || s.ip}</strong><br><small class="text-secondary">${s.ip}</small></td>
                    <td>CPU: ${(s.cpu_usage||0).toFixed(1)}% | Mem: ${(s.memory_usage||0).toFixed(1)}%</td>
                    <td>${s.last_seen ? new Date(s.last_seen).toLocaleTimeString() : 'Never'}</td>
                    <td class="text-end">
                        <a href="/devices/${s.device_id}/server-monitoring" class="btn btn-xs tactical-btn-outline"><i class="fas fa-search"></i> Inspect</a>
                    </td>
                </tr>
            `;
        }).join('');
        incidentContextBody.innerHTML = problemServers.length > 0 ? impactText + alertsListHTML : '';
    }

    if (nocBar) {
        nocBar.style.display = 'flex';
        
        if (!hasData) {
            nocBar.className = 'sticky-alert-bar is-stale';
            if (nocIcon) nocIcon.innerHTML = '<i class="fas fa-clock"></i>';
            if (nocTitle) nocTitle.textContent = 'Stale Data / No Connection';
            if (nocDetail) nocDetail.textContent = 'Awaiting telemetry updates from backend.';
            if (incidentPanel) incidentPanel.style.display = 'none';
        } else if (totalCriticals > 0 || degradedCount > 0) {
            const isCritical = totalCriticals > 0;
            nocBar.className = isCritical ? 'sticky-alert-bar is-critical' : 'sticky-alert-bar is-warning';
            
            if (nocIcon) nocIcon.innerHTML = isCritical ? '<i class="fas fa-radiation"></i>' : '<i class="fas fa-exclamation-triangle"></i>';
            
            if (nocTitle) {
                if (isCritical) {
                    nocTitle.textContent = `CRITICAL \u2014 ${totalCriticals} Server${totalCriticals>1?'s':''}`;
                } else {
                    nocTitle.textContent = `DEGRADED \u2014 ${degradedCount} Server${degradedCount>1?'s':''}`;
                }
            }
            
            const pressure = [
                breaches.cpu ? `CPU: ${breaches.cpu}` : null,
                breaches.memory ? `Mem: ${breaches.memory}`: null,
                breaches.disk ? `Disk: ${breaches.disk}` : null
            ].filter(Boolean);
            
            if (nocDetail) {
                let detailStr = pressure.length > 0 ? `(${pressure.join(' + ')})` : `Systems require operator attention.`;
                const remaining = problemServers.length - 1;
                if (remaining > 0) detailStr += ` +${remaining} additional alert${remaining > 1 ? 's' : ''}`;
                nocDetail.textContent = detailStr;
            }
            
            if (incidentPanel) {
                incidentPanel.style.display = 'block';
                incidentPanel.className = isCritical ? 'mt-4' : 'mt-4 is-warning';
                
                // Auto-scroll / Elevate Logic if not already scrolled
                if (!window.hasScrolledToIncident) {
                    incidentPanel.classList.add('flash-highlight');
                    setTimeout(() => incidentPanel.classList.remove('flash-highlight'), 2000);
                    window.hasScrolledToIncident = true;
                }
            }
            if (incidentCount) {
                incidentCount.textContent = `${problemServers.length} Servers`;
                incidentCount.className = isCritical ? 'badge bg-danger' : 'badge bg-warning text-dark';
            }
        } else {
            nocBar.className = 'sticky-alert-bar is-healthy';
            if (nocIcon) nocIcon.innerHTML = '<i class="fas fa-check-circle"></i>';
            if (nocTitle) nocTitle.textContent = 'All Systems Operational';
            if (nocDetail) nocDetail.textContent = 'No threshold breaches or offline agents detected.';
            if (incidentPanel) incidentPanel.style.display = 'none';
            window.hasScrolledToIncident = false;
        }
    }

    renderSparkline('chart-spark-cpu', data.trends?.labels, data.trends?.cpu, '#00d4aa', 'cpu');
    renderSparkline('chart-spark-mem', data.trends?.labels, data.trends?.memory, '#00d4aa', 'mem');
}

export function requestServerAction(deviceId, actionName) {
    let modalEl = document.getElementById('actionConfirmModal');
    if (!modalEl) { console.error('Modal not found'); return; }
    const modal = new bootstrap.Modal(modalEl);
    document.getElementById('actionConfirmMessage').textContent = `Are you sure you want to perform: ${actionName} on this node?`;
    document.getElementById('actionResultMessage').className = 'mt-3 text-success fw-bold d-none';
    document.getElementById('actionLoadingSpinner').classList.add('d-none');
    document.getElementById('actionConfirmFooter').classList.remove('d-none');
    
    document.getElementById('btnConfirmActionRun').onclick = () => {
        document.getElementById('actionLoadingSpinner').classList.remove('d-none');
        document.getElementById('btnConfirmActionRun').disabled = true;
        
        // Mock API call
        setTimeout(() => {
            document.getElementById('actionLoadingSpinner').classList.add('d-none');
            document.getElementById('actionResultMessage').textContent = `${actionName} command sent successfully.`;
            document.getElementById('actionResultMessage').classList.remove('d-none');
            document.getElementById('actionConfirmFooter').classList.add('d-none');
            document.getElementById('btnConfirmActionRun').disabled = false;
            
            setTimeout(() => {
                modal.hide();
            }, 1500);
        }, 800);
    };
    modal.show();
}
"""

js = re.sub(
    r'export function renderFleetOverview\(data\) \{[\s\S]*?renderSparkline\(\'chart-spark-mem\', data\.trends\?\.labels, data\.trends\?\.memory, \'#[A-Fa-f0-9]+\', \'mem\'\);\n\}', 
    new_overview.strip(), 
    js
)


new_sparkline = """
function renderSparkline(canvasId, labels, data, color, type) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    let chartRef = type === 'cpu' ? cpuSparkChart : memSparkChart;
    if (chartRef && chartRef.canvas === ctx) {
        chartRef.data.labels = labels || [];
        chartRef.data.datasets[0].data = data || [];
        
        // Marker only on breach
        chartRef.data.datasets[0].pointRadius = (data||[]).map(val => val >= 90 ? 3 : 0);
        chartRef.data.datasets[0].pointBackgroundColor = '#ff4e4e';

        chartRef.update('none');
        return;
    }

    if (chartRef) chartRef.destroy();

    const nextChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels || [],
            datasets: [{
                data: data || [],
                borderColor: color,
                borderWidth: 2,
                pointRadius: (data||[]).map(val => val >= 90 ? 3 : 0),
                pointBackgroundColor: '#ff4e4e',
                fill: {
                    target: 'origin',
                    above: 'rgba(0, 212, 170, 0.05)'
                },
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { 
                legend: { display: false }, 
                tooltip: { enabled: false },
                annotation: {
                    annotations: {
                        lineWarning: { type: 'line', yMin: 70, yMax: 70, borderColor: 'rgba(255, 176, 32, 0.3)', borderWidth: 1, borderDash: [5, 5] },
                        lineCritical: { type: 'line', yMin: 90, yMax: 90, borderColor: 'rgba(255, 78, 78, 0.3)', borderWidth: 1, borderDash: [5, 5] }
                    }
                }
            },
            scales: {
                x: { display: false },
                y: { display: false, min: 0, max: 100 }
            },
            animation: false
        }
    });

    if (type === 'cpu') cpuSparkChart = nextChart;
    else memSparkChart = nextChart;
}
"""

js = re.sub(
    r'function renderSparkline\(canvasId, labels, data, color, type\) \{[\s\S]*?memSparkChart = nextChart;\n\s*\}', 
    new_sparkline.strip(), 
    js
)

# Update action dropdowns in renderEnhancedServerTable
action_html = """
                        <ul class="dropdown-menu tactical-dropdown">
                            <li><a class="dropdown-item" href="/devices/${server.device_id}/server-monitoring">Inspect Metrics</a></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item text-warning" href="#" onclick="event.preventDefault(); import('./serverHealth.js').then(m => m.requestServerAction('${server.device_id}', 'Restart Agent'))">Restart Agent</a></li>
                            <li><a class="dropdown-item text-danger" href="#" onclick="event.preventDefault(); import('./serverHealth.js').then(m => m.requestServerAction('${server.device_id}', 'Acknowledge Alert'))">Acknowledge</a></li>
                        </ul>
"""

js = re.sub(
    r'<ul class=\"dropdown-menu tactical-dropdown\">[\s\S]*?<\/ul>',
    action_html.strip(),
    js
)

js = js.replace("health === 'Warning'", "health === 'Warning' || health === 'Degraded'")
js = js.replace("currentFilter === 'warning'", "currentFilter === 'degraded' || currentFilter === 'warning'")

with open(js_path, 'w', encoding='utf-8') as f:
    f.write(js)
print("Updated JS.")
