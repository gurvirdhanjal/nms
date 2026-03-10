/**
 * Enterprise Server Dashboard with Optimized DOM Updates
 * Uses keyed patching for smooth, flicker-free updates
 */

import { patchKeyedTableRows, setTableMessageRow } from '../domPatch.js';
import { openServerModal } from '../modals/serverDetailModal.js';
import { timeAgo } from '../utils.js';

let currentFilter = 'all';
let serverData = [];
let isLoading = false;
let lastFetchTime = 0;

// Skeleton/Loading States
function showSkeletonKPIs() {
    const kpiIds = ['kpi-total-servers', 'kpi-healthy', 'kpi-warning', 'kpi-critical', 'kpi-offline', 'kpi-above-threshold'];
    kpiIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.innerHTML = '<div class="skeleton-text" style="width: 60px; height: 32px;"></div>';
        }
    });
}

function showSkeletonTable(tbody, colSpan = 9) {
    if (!tbody) return;
    
    const skeletonRows = Array.from({ length: 5 }, (_, i) => `
        <tr class="skeleton-row">
            <td><div class="skeleton-text" style="width: 150px;"></div></td>
            <td><div class="skeleton-text" style="width: 80px;"></div></td>
            <td><div class="skeleton-text" style="width: 50px;"></div></td>
            <td><div class="skeleton-text" style="width: 50px;"></div></td>
            <td><div class="skeleton-text" style="width: 50px;"></div></td>
            <td><div class="skeleton-text" style="width: 60px;"></div></td>
            <td><div class="skeleton-text" style="width: 50px;"></div></td>
            <td><div class="skeleton-text" style="width: 100px;"></div></td>
            <td><div class="skeleton-text" style="width: 80px;"></div></td>
        </tr>
    `).join('');
    
    tbody.innerHTML = skeletonRows;
}

// Optimized KPI Updates with Smooth Transitions
function updateKPIs(data) {
    const servers = data.servers || [];
    const total = servers.length;
    const healthy = servers.filter(s => s.health === 'Healthy').length;
    const warning = servers.filter(s => s.health === 'Warning').length;
    const critical = servers.filter(s => s.health === 'Critical').length;
    const offline = servers.filter(s => !s.last_seen || s.health === 'Offline').length;
    const aboveThreshold = servers.filter(s => 
        (s.cpu_usage || 0) > 80 || (s.memory_usage || 0) > 85 || (s.disk_usage || 0) > 90
    ).length;

    // Smooth number transitions
    animateValue('kpi-total-servers', total);
    animateValue('kpi-healthy', healthy);
    animateValue('kpi-warning', warning);
    animateValue('kpi-critical', critical);
    animateValue('kpi-offline', offline);
    animateValue('kpi-above-threshold', aboveThreshold);
    
    // Update percentages
    updateText('kpi-total-trend', `${servers.filter(s => s.is_monitored !== false).length} monitored`);
    updateText('kpi-healthy-pct', `${((healthy/total)*100 || 0).toFixed(1)}%`);
    updateText('kpi-warning-pct', `${((warning/total)*100 || 0).toFixed(1)}%`);
    updateText('kpi-critical-pct', `${((critical/total)*100 || 0).toFixed(1)}%`);
    updateText('kpi-offline-pct', `${((offline/total)*100 || 0).toFixed(1)}%`);
}

// Smooth number animation
function animateValue(elementId, endValue) {
    const el = document.getElementById(elementId);
    if (!el) return;
    
    const startValue = parseInt(el.textContent) || 0;
    if (startValue === endValue) return;
    
    const duration = 300;
    const startTime = performance.now();
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Easing function
        const easeOutQuad = progress * (2 - progress);
        const current = Math.round(startValue + (endValue - startValue) * easeOutQuad);
        
        el.textContent = current;
        
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = endValue;
        }
    }
    
    requestAnimationFrame(update);
}

// Update text only if changed (prevents flicker)
function updateText(elementId, newText) {
    const el = document.getElementById(elementId);
    if (el && el.textContent !== newText) {
        el.textContent = newText;
    }
}

// Resource Pressure with Smooth Gauge Animations
function updateResourcePressure(data) {
    const servers = data.servers || [];
    if (servers.length === 0) return;

    const avgCpu = servers.reduce((sum, s) => sum + (s.cpu_usage || 0), 0) / servers.length;
    const avgMem = servers.reduce((sum, s) => sum + (s.memory_usage || 0), 0) / servers.length;
    const avgDisk = servers.reduce((sum, s) => sum + (s.disk_usage || 0), 0) / servers.length;

    const cpuValues = servers.map(s => s.cpu_usage || 0).sort((a, b) => a - b);
    const memValues = servers.map(s => s.memory_usage || 0).sort((a, b) => a - b);
    const diskValues = servers.map(s => s.disk_usage || 0).sort((a, b) => a - b);

    const p95Index = Math.floor(servers.length * 0.95);
    const p95Cpu = cpuValues[p95Index] || 0;
    const p95Mem = memValues[p95Index] || 0;
    const p95Disk = diskValues[p95Index] || 0;

    updateText('avg-cpu', `${avgCpu.toFixed(1)}%`);
    updateText('p95-cpu', `${p95Cpu.toFixed(1)}%`);
    animateGauge('avg-cpu-gauge', avgCpu);

    updateText('avg-mem', `${avgMem.toFixed(1)}%`);
    updateText('p95-mem', `${p95Mem.toFixed(1)}%`);
    animateGauge('avg-mem-gauge', avgMem);

    updateText('avg-disk', `${avgDisk.toFixed(1)}%`);
    updateText('p95-disk', `${p95Disk.toFixed(1)}%`);
    animateGauge('avg-disk-gauge', avgDisk);

    // Calculate average uptime
    const uptimes = servers.filter(s => s.uptime).map(s => {
        if (typeof s.uptime === 'number') return s.uptime;
        if (typeof s.uptime === 'string') {
            const parsed = parseFloat(s.uptime);
            return isNaN(parsed) ? 0 : parsed;
        }
        return 0;
    }).filter(u => u > 0);
    
    if (uptimes.length > 0) {
        const avgUptime = uptimes.reduce((sum, u) => sum + u, 0) / uptimes.length;
        const days = Math.floor(avgUptime / 86400);
        const hours = Math.floor((avgUptime % 86400) / 3600);
        updateText('fleet-uptime', `${days}d ${hours}h`);
    } else {
        updateText('fleet-uptime', 'N/A');
    }
}

// Smooth gauge animation
function animateGauge(elementId, targetPercent) {
    const el = document.getElementById(elementId);
    if (!el) return;
    
    const currentPercent = parseFloat(el.style.width) || 0;
    if (Math.abs(currentPercent - targetPercent) < 0.1) return;
    
    el.style.transition = 'width 0.5s cubic-bezier(0.4, 0.0, 0.2, 1)';
    el.style.width = `${targetPercent}%`;
}

// Problem Servers with Keyed DOM Patching
function updateProblemServers(data) {
    const servers = data.servers || [];
    const problems = servers.filter(s => s.health !== 'Healthy').slice(0, 10);
    
    updateText('problem-count', problems.length);

    const tbody = document.getElementById('problem-servers-body');
    if (!tbody) return;

    patchKeyedTableRows(tbody, problems, {
        getKey: (server) => server.device_id,
        renderCells: (server) => {
            const healthBadge = server.health === 'Critical' ? 'bg-danger' : 'bg-warning';
            const cpuClass = (server.cpu_usage || 0) > 80 ? 'text-danger fw-bold' : '';
            const memClass = (server.memory_usage || 0) > 85 ? 'text-danger fw-bold' : '';
            const diskClass = (server.disk_usage || 0) > 90 ? 'text-danger fw-bold' : '';
            
            return `
                <td>
                    <div class="fw-bold">${server.hostname || server.device_name || server.ip}</div>
                    <div class="small text-secondary">${server.ip}</div>
                </td>
                <td><span class="badge ${healthBadge}">${server.health}</span></td>
                <td class="${cpuClass}">${server.cpu_usage ? server.cpu_usage.toFixed(1) : '0.0'}%</td>
                <td class="${memClass}">${server.memory_usage ? server.memory_usage.toFixed(1) : '0.0'}%</td>
                <td class="${diskClass}">${server.disk_usage ? server.disk_usage.toFixed(1) : '0.0'}%</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-sm btn-dark border-secondary px-2 server-modal-btn" data-device-id="${server.device_id}">
                            <i class="fas fa-chart-line"></i>
                        </button>
                        <a href="/devices/${server.device_id}/server-monitoring" class="btn btn-sm btn-dark border-secondary px-2">
                            <i class="fas fa-external-link-alt"></i>
                        </a>
                    </div>
                </td>
            `;
        },
        applyRow: (row, server) => {
            const modalBtn = row.querySelector('.server-modal-btn');
            if (modalBtn) {
                modalBtn.onclick = (e) => {
                    e.stopPropagation();
                    openServerModal(server.device_id);
                };
            }
        },
        emptyColSpan: 6,
        emptyMessage: '<i class="fas fa-check-circle text-success me-2"></i>No problematic servers',
        emptyClassName: 'text-center p-3'
    });
}


// Fleet Table with Keyed DOM Patching
function updateFleetTable(data) {
    const servers = data.servers || [];
    let filtered = servers;

    if (currentFilter !== 'all') {
        if (currentFilter === 'problem') {
            filtered = servers.filter(s => s.health !== 'Healthy');
        } else {
            filtered = servers.filter(s => s.health.toLowerCase() === currentFilter);
        }
    }

    const tbody = document.getElementById('server-fleet-body');
    if (!tbody) return;

    patchKeyedTableRows(tbody, filtered, {
        getKey: (server) => server.device_id,
        renderCells: (server) => {
            const lastSeen = server.last_seen ? timeAgo(server.last_seen) : 'Never';
            const healthBadge = 
                server.health === 'Healthy' ? 'bg-success' : 
                server.health === 'Critical' ? 'bg-danger' : 'bg-warning';
            const cpuClass = (server.cpu_usage || 0) > 80 ? 'text-danger fw-bold' : '';
            const memClass = (server.memory_usage || 0) > 85 ? 'text-danger fw-bold' : '';
            const diskClass = (server.disk_usage || 0) > 90 ? 'text-danger fw-bold' : '';
            const lossClass = (server.packet_loss || 0) > 0 ? 'text-warning' : '';
            
            return `
                <td>
                    <div class="fw-bold">${server.hostname || server.device_name || server.ip}</div>
                    <div class="small text-secondary">${server.ip}</div>
                </td>
                <td><span class="badge ${healthBadge}">${server.health}</span></td>
                <td class="${cpuClass}">${server.cpu_usage ? server.cpu_usage.toFixed(1) : '0.0'}%</td>
                <td class="${memClass}">${server.memory_usage ? server.memory_usage.toFixed(1) : '0.0'}%</td>
                <td class="${diskClass}">${server.disk_usage ? server.disk_usage.toFixed(1) : '0.0'}%</td>
                <td>${server.latency ? server.latency.toFixed(1) + ' ms' : '-'}</td>
                <td class="${lossClass}">${server.packet_loss ? server.packet_loss.toFixed(1) + '%' : '-'}</td>
                <td>${lastSeen}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-sm btn-dark border-secondary px-2 server-modal-btn" data-device-id="${server.device_id}">
                            <i class="fas fa-chart-line"></i>
                        </button>
                        <a href="/devices/${server.device_id}/server-monitoring" class="btn btn-sm btn-dark border-secondary px-2">
                            <i class="fas fa-external-link-alt"></i>
                        </a>
                    </div>
                </td>
            `;
        },
        applyRow: (row, server) => {
            const modalBtn = row.querySelector('.server-modal-btn');
            if (modalBtn) {
                modalBtn.onclick = (e) => {
                    e.stopPropagation();
                    openServerModal(server.device_id);
                };
            }
        },
        emptyColSpan: 9,
        emptyMessage: 'No servers match filter',
        emptyClassName: 'text-center text-secondary p-3'
    });
}

// Server Alerts (placeholder for now)
function updateServerAlerts(data) {
    const tbody = document.getElementById('server-alerts-body');
    if (!tbody) return;
    
    setTableMessageRow(tbody, 4, 'No recent alerts', 'text-center text-secondary p-3');
    updateText('alert-count', '0');
}

// Main Update Function
function updateDashboard(data) {
    updateKPIs(data);
    updateResourcePressure(data);
    updateProblemServers(data);
    updateServerAlerts(data);
    updateFleetTable(data);
}

// Fetch with Debouncing and Error Handling
async function fetchServerData() {
    if (isLoading) return;
    
    const now = Date.now();
    if (now - lastFetchTime < 1000) return; // Debounce: min 1 second between fetches
    
    isLoading = true;
    lastFetchTime = now;
    
    try {
        const response = await fetch('/api/server/health');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        serverData = data.servers || [];
        updateDashboard(data);
    } catch (error) {
        console.error('[ServerDashboard] Failed to fetch server data:', error);
        // Show error state but don't clear existing data
        const tbody = document.getElementById('server-fleet-body');
        if (tbody && tbody.children.length === 0) {
            setTableMessageRow(tbody, 9, 
                '<i class="fas fa-exclamation-triangle text-warning me-2"></i>Failed to load server data. Retrying...', 
                'text-center text-warning p-3'
            );
        }
    } finally {
        isLoading = false;
    }
}

// Filter Management
function setFilter(filter) {
    currentFilter = filter;
    updateFleetTable({ servers: serverData });
}

// Initialize Dashboard
export function initServerDashboard() {
    // Show skeleton states
    showSkeletonKPIs();
    const fleetBody = document.getElementById('server-fleet-body');
    if (fleetBody) showSkeletonTable(fleetBody);
    
    // Setup filter buttons
    document.querySelectorAll('[data-filter]').forEach(btn => {
        btn.addEventListener('click', () => {
            const filter = btn.dataset.filter;
            setFilter(filter);
            
            // Update button states
            document.querySelectorAll('[data-filter]').forEach(b => {
                b.classList.toggle('active', b === btn);
                b.classList.toggle('tactical-btn-primary', b === btn);
                b.classList.toggle('tactical-btn-ghost', b !== btn);
            });
        });
    });

    // Setup refresh button
    const refreshBtn = document.getElementById('btnRefreshDashboard');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<i class="fas fa-sync-alt fa-spin me-1"></i>Refreshing...';
            
            await fetchServerData();
            
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<i class="fas fa-sync-alt me-1"></i>Refresh';
        });
    }

    // Initial load
    fetchServerData();

    // Auto-refresh every 30 seconds
    setInterval(fetchServerData, 30000);
}

// Export for use in template
export { fetchServerData, setFilter };
