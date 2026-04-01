(function () {
    'use strict';

    const config = window.TRACKING_LIVE_FLEET_CONFIG || {};
    const refreshMs = Math.max(3000, Number(config.refreshMs || 5000));

    const state = {
        auto: true,
        filter: 'all',
        search: '',
        lastSuccessTs: null,
        pollTimer: null,
    };

    const dom = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        dom.tableBody = document.getElementById('fleetDeviceTableBody');
        dom.emptyState = document.getElementById('fleetEmptyState');
        dom.errorBanner = document.getElementById('fleetErrorBanner');
        dom.refreshBtn = document.getElementById('fleetRefreshBtn');
        dom.autoBtn = document.getElementById('fleetAutoToggleBtn');
        dom.searchInput = document.getElementById('fleetSearchInput');
        dom.pollMeta = document.getElementById('fleetPollMeta');
        dom.pollDot = document.getElementById('fleetPollingDot');
        dom.lastUpdate = document.getElementById('fleetLastUpdate');
        dom.kpiTotal = document.getElementById('kpiTotal');
        dom.kpiOnline = document.getElementById('kpiOnline');
        dom.kpiDegraded = document.getElementById('kpiDegraded');
        dom.kpiOffline = document.getElementById('kpiOffline');
        dom.kpiCheckins = document.getElementById('kpiCheckins');
        dom.kpiCpu = document.getElementById('kpiCpu');
        dom.needsAttentionChip = document.getElementById('fleetNeedsAttentionChip');
        dom.visibleCount = document.getElementById('fleetVisibleCount');
        dom.chipCountAll = document.getElementById('chipCountAll');
        dom.chipCountOnline = document.getElementById('chipCountOnline');
        dom.chipCountDegraded = document.getElementById('chipCountDegraded');
        dom.chipCountOffline = document.getElementById('chipCountOffline');
        dom.chipCountAttention = document.getElementById('chipCountAttention');
        dom.chipCountHighCpu = document.getElementById('chipCountHighCpu');
        dom.chipCountHighRisk = document.getElementById('chipCountHighRisk');
        dom.chipCountIdle = document.getElementById('chipCountIdle');
        dom.filterChips = Array.from(document.querySelectorAll('.fleet-filter-chip, .filter-chips [data-filter]'));

        bindEvents();
        refreshFleet(true);
        state.pollTimer = window.setInterval(() => {
            if (state.auto) {
                refreshFleet(false);
            } else {
                updatePollingMeta();
            }
        }, refreshMs);
    }

    function bindEvents() {
        dom.refreshBtn?.addEventListener('click', () => refreshFleet(true));
        dom.autoBtn?.addEventListener('click', toggleAutoRefresh);
        dom.searchInput?.addEventListener('input', () => {
            state.search = String(dom.searchInput.value || '').trim().toLowerCase();
            applyFilters();
        });
        dom.filterChips.forEach((chip) => {
            chip.addEventListener('click', () => {
                state.filter = String(chip.dataset.filter || 'all').toLowerCase();
                dom.filterChips.forEach((btn) => btn.classList.toggle('active', btn === chip));
                applyFilters();
            });
        });
    }

    function toggleAutoRefresh() {
        state.auto = !state.auto;
        if (dom.autoBtn) {
            dom.autoBtn.dataset.auto = state.auto ? 'true' : 'false';
            dom.autoBtn.innerHTML = state.auto
                ? '<i class="fas fa-pause"></i> Pause'
                : '<i class="fas fa-play"></i> Resume';
        }
        updatePollingMeta();
    }

    async function refreshFleet(force) {
        clearError();
        const startedAt = Date.now();
        try {
            const endpoint = force ? '/api/tracking/live-summary?force=1' : '/api/tracking/live-summary';
            const { payload } = await requestJson(endpoint, {
                method: 'GET',
                headers: { 'Accept': 'application/json' },
            });
            renderPayload(payload);
            state.lastSuccessTs = Date.now();
            updateLastUpdate(state.lastSuccessTs);
            updatePollingMeta(Date.now() - startedAt, payload.devices?.length || 0);
        } catch (error) {
            if (error?.status === 401 || error?.status === 403) {
                state.auto = false;
                if (dom.autoBtn) {
                    dom.autoBtn.dataset.auto = 'false';
                    dom.autoBtn.innerHTML = '<i class="fas fa-play"></i> Resume';
                }
            }
            showError(error?.message || 'Failed to load fleet summary.');
            if (dom.pollDot) {
                dom.pollDot.classList.remove('healthy');
                dom.pollDot.classList.add('offline');
            }
            updatePollingMeta();
        }
    }

    function renderPayload(payload) {
        const devices = Array.isArray(payload.devices) ? payload.devices : [];
        const existingRows = new Map();
        Array.from(dom.tableBody?.querySelectorAll('tr.fleet-device-row') || []).forEach((row) => {
            existingRows.set(Number(row.dataset.deviceId || 0), row);
        });

        devices.forEach((device) => {
            const id = Number(device.id || 0);
            if (!id) {
                return;
            }
            let row = existingRows.get(id);
            if (!row) {
                row = createRowSkeleton(id);
                row.classList.add('row-entering');
                row.addEventListener('animationend', () => row.classList.remove('row-entering'), { once: true });
                dom.tableBody?.appendChild(row);
            }
            updateRow(row, device);
            existingRows.delete(id);
        });

        existingRows.forEach((row) => row.remove());
        applyFilters();
        renderKpis(payload, devices);
    }

    function createRowSkeleton(deviceId) {
        const row = document.createElement('tr');
        row.id = `fleet-device-${deviceId}`;
        row.className = 'fleet-device-row';
        row.dataset.deviceId = String(deviceId);
        row.dataset.cpu = '0';
        row.dataset.risk = 'unknown';
        row.dataset.activeViolationCount = '0';
        row.dataset.idleSeconds = '0';
        row.innerHTML = `
            <td>
                <div class="device-cell">
                    <div class="device-icon-wrap">
                        <i class="fas fa-desktop" aria-hidden="true"></i>
                    </div>
                    <div>
                        <div class="device-name-line">
                            <span class="device-name fleet-device-name">Unknown</span>
                            <span class="fleet-violation-icon d-none" title="Active policy violations">
                                <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
                            </span>
                        </div>
                        <div class="device-meta fleet-device-meta">Unassigned</div>
                        <div class="fleet-policy-note">No policy alerts</div>
                    </div>
                </div>
            </td>
            <td>
                <div class="status-cell">
                    <span class="status-badge offline">
                        <span class="status-dot"></span>
                        OFFLINE
                    </span>
                    <span class="fleet-status-reason">Awaiting telemetry</span>
                </div>
            </td>
            <td>
                <div class="metrics-cell">
                    <div class="metric-row">
                        <span class="metric-label">CPU</span>
                        <div class="metric-bar-track"><div class="metric-bar-fill fleet-cpu-bar" style="width:0%"></div></div>
                        <span class="metric-val fleet-cpu-val">0%</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">MEM</span>
                        <div class="metric-bar-track"><div class="metric-bar-fill fleet-mem-bar" style="width:0%"></div></div>
                        <span class="metric-val fleet-mem-val">0%</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">DSK</span>
                        <div class="metric-bar-track"><div class="metric-bar-fill fleet-disk-bar" style="width:0%"></div></div>
                        <span class="metric-val fleet-disk-val">0%</span>
                    </div>
                </div>
            </td>
            <td>
                <div class="network-cell">
                    <div class="net-line"><span class="net-label">IP</span><span class="fleet-ip">N/A</span></div>
                    <div class="net-line"><span class="net-label">Host</span><span class="fleet-host">N/A</span></div>
                    <div class="net-line"><span class="net-label">MAC</span><span class="fleet-mac">N/A</span></div>
                </div>
            </td>
            <td>
                <div class="time-cell">
                    <div class="time-main fleet-last-seen">Never</div>
                    <div class="time-ago fleet-sync-age">Sync: n/a</div>
                </div>
            </td>
            <td>
                <span class="risk-badge unknown fleet-risk-badge">Unknown</span>
                <div class="fleet-risk-context">CPU 0% · Idle 0m</div>
            </td>
            <td class="col-actions">
                <div class="actions-cell">
                    <a href="/tracking/devices/${deviceId}" class="action-btn primary" title="Open live view">
                        <i class="fas fa-expand" aria-hidden="true"></i> Live
                    </a>
                    <a href="/tracking/history/${deviceId}" class="action-btn" title="View history">
                        <i class="fas fa-history" aria-hidden="true"></i>
                    </a>
                </div>
            </td>
        `;
        return row;
    }

    function updateRow(row, device) {
        const status = normalizeStatus(device.availability_status || device.status);
        const tracking = ensureObject(device.tracking_data);
        const systemMetrics = ensureObject(tracking.system_metrics);
        const currentActivity = ensureObject(tracking.current_activity);
        const cpu = toNumber(systemMetrics.cpu_percent ?? systemMetrics.cpu_usage, 0);
        const memory = toNumber(systemMetrics.memory_percent ?? systemMetrics.memory_usage, 0);
        const disk = toNumber(systemMetrics.disk_percent ?? systemMetrics.disk_usage, 0);
        const idleSeconds = toNumber(currentActivity.idle_seconds, 0);
        const activeViolationCount = Math.max(0, Math.floor(toNumber(device.active_violation_count, 0)));
        const highestViolationSeverity = normalizeSeverity(device.highest_violation_severity);
        const hasActiveViolation = activeViolationCount > 0;
        const risk = computeRiskLevel(
            status,
            cpu,
            idleSeconds,
            String(device.probe_error_code || ''),
            Boolean(device.metrics_available),
            activeViolationCount,
            highestViolationSeverity
        );

        row.dataset.status = status;
        row.dataset.cpu = String(cpu);
        row.dataset.idleSeconds = String(idleSeconds);
        row.dataset.risk = risk.level;
        row.dataset.activeViolationCount = String(activeViolationCount);
        row.dataset.searchIndex = [
            device.device_name,
            device.employee_name,
            device.hostname,
            device.ip_address,
            device.mac_address,
        ].join(' ').toLowerCase();
        row.classList.toggle('fleet-has-violation', hasActiveViolation);

        const nameNode = row.querySelector('.fleet-device-name');
        const employeeNode = row.querySelector('.fleet-device-meta');
        const violationIconNode = row.querySelector('.fleet-violation-icon');
        const policyNoteNode = row.querySelector('.fleet-policy-note');
        const badgeNode = row.querySelector('.status-badge');
        const reasonNode = row.querySelector('.fleet-status-reason');
        const ipNode = row.querySelector('.fleet-ip');
        const hostNode = row.querySelector('.fleet-host');
        const macNode = row.querySelector('.fleet-mac');
        const lastSeenNode = row.querySelector('.fleet-last-seen');
        const syncNode = row.querySelector('.fleet-sync-age');
        const riskBadgeNode = row.querySelector('.fleet-risk-badge');
        const riskContextNode = row.querySelector('.fleet-risk-context');
        const detailAgentNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-agent`);
        const detailIdleNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-idle`);
        const detailViolationsNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-violations`);
        const detailCpuNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-cpu`);
        const detailMemNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-mem`);
        const detailDiskNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-disk`);
        const detailLastSeenNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-lastseen`);
        const detailRiskNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-risk`);
        const detailStatusNode = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"] .fleet-detail-status`);

        if (nameNode) nameNode.textContent = device.device_name || 'Unnamed Device';
        if (employeeNode) employeeNode.textContent = device.employee_name || 'Unassigned';
        if (violationIconNode) {
            violationIconNode.classList.toggle('d-none', !hasActiveViolation);
            violationIconNode.title = hasActiveViolation
                ? `${activeViolationCount} active restricted-site violation${activeViolationCount === 1 ? '' : 's'}`
                : 'No active policy violations';
        }
        if (policyNoteNode) {
            policyNoteNode.textContent = hasActiveViolation
                ? `${activeViolationCount} active policy alert${activeViolationCount === 1 ? '' : 's'}`
                : 'No policy alerts';
        }
        if (badgeNode) {
            badgeNode.textContent = status.toUpperCase();
            badgeNode.className = `status-badge ${status}`;
        }
        if (reasonNode) reasonNode.textContent = buildStatusReason(device);
        if (ipNode) ipNode.textContent = device.ip_address || 'N/A';
        if (hostNode) hostNode.textContent = device.hostname || 'N/A';
        if (macNode) macNode.textContent = device.mac_address || 'N/A';
        updateDeviceTelemetry(row.dataset.deviceId, cpu, memory, disk);
        if (lastSeenNode) {
            lastSeenNode.textContent = formatTimestamp(device.last_seen || device.last_probe_at || device.last_agent_sync_at);
        }
        if (syncNode) {
            syncNode.textContent = `Sync: ${formatAgeFromSeconds(device.agent_sync_age_seconds)}`;
        }
        if (riskBadgeNode) {
            riskBadgeNode.textContent = risk.label;
            riskBadgeNode.className = `tactical-badge fleet-risk-badge ${risk.badgeClass}`;
        }
        if (riskContextNode) {
            riskContextNode.textContent = hasActiveViolation
                ? `Policy alerts ${activeViolationCount} | CPU ${Math.round(cpu)}% | Idle ${Math.floor(idleSeconds / 60)}m`
                : `CPU ${Math.round(cpu)}% | Idle ${Math.floor(idleSeconds / 60)}m`;
        }
        if (detailAgentNode) detailAgentNode.textContent = device.agent_sync_recent ? 'Active' : 'Idle';
        if (detailIdleNode) detailIdleNode.textContent = `${Math.floor(idleSeconds / 60)}m`;
        if (detailViolationsNode) detailViolationsNode.textContent = String(activeViolationCount);
        if (detailCpuNode) detailCpuNode.textContent = formatTelemetryPercent(cpu);
        if (detailMemNode) detailMemNode.textContent = formatTelemetryPercent(memory);
        if (detailDiskNode) detailDiskNode.textContent = formatTelemetryPercent(disk);
        if (detailLastSeenNode) detailLastSeenNode.textContent = formatTimestamp(device.last_seen || device.last_probe_at || device.last_agent_sync_at);
        if (detailRiskNode) detailRiskNode.textContent = risk.label;
        if (detailStatusNode) detailStatusNode.textContent = status.toUpperCase();
    }

    function renderKpis(payload, devices) {
        const online = toNumber(payload.online_devices, devices.filter((d) => normalizeStatus(d.availability_status || d.status) === 'online').length);
        const degraded = toNumber(payload.degraded_devices, devices.filter((d) => normalizeStatus(d.availability_status || d.status) === 'degraded').length);
        const offline = toNumber(payload.offline_devices, devices.filter((d) => normalizeStatus(d.availability_status || d.status) === 'offline').length);
        const total = toNumber(payload.total_devices, devices.length);
        const checkins = toNumber(payload.active_agent_checkins, 0);
        const needsAttention = devices.filter((device) => toNumber(device.active_violation_count, 0) > 0).length;

        const cpuValues = devices
            .filter((d) => ['online', 'degraded'].includes(normalizeStatus(d.availability_status || d.status)))
            .map((d) => {
                const tracking = ensureObject(d.tracking_data);
                const systemMetrics = ensureObject(tracking.system_metrics);
                return toNumber(systemMetrics.cpu_percent ?? systemMetrics.cpu_usage, NaN);
            })
            .filter((v) => Number.isFinite(v));

        const avgCpu = cpuValues.length ? (cpuValues.reduce((sum, value) => sum + value, 0) / cpuValues.length) : 0;

        setText(dom.kpiTotal, String(total));
        setText(dom.kpiOnline, String(online));
        setText(dom.kpiDegraded, String(degraded));
        setText(dom.kpiOffline, String(offline));
        setText(dom.kpiCheckins, String(checkins));
        setText(dom.kpiCpu, `${Math.round(avgCpu)}%`);
        setText(dom.chipCountAttention, String(needsAttention));
    }

    function applyFilters() {
        const rows = Array.from(dom.tableBody?.querySelectorAll('tr.fleet-device-row') || []);
        const counts = {
            all: rows.length,
            online: 0,
            degraded: 0,
            offline: 0,
            needs_attention: 0,
            high_cpu: 0,
            high_risk: 0,
            idle_20m: 0,
        };
        let visibleCount = 0;
        rows.forEach((row) => {
            const status = String(row.dataset.status || 'offline');
            const cpu = toNumber(row.dataset.cpu, 0);
            const idleSeconds = toNumber(row.dataset.idleSeconds, 0);
            const risk = String(row.dataset.risk || 'unknown');
            const activeViolationCount = toNumber(row.dataset.activeViolationCount, 0);
            if (status === 'online') counts.online += 1;
            if (status === 'degraded') counts.degraded += 1;
            if (status === 'offline') counts.offline += 1;
            if (activeViolationCount > 0) counts.needs_attention += 1;
            if (cpu >= 85) counts.high_cpu += 1;
            if (risk === 'high') counts.high_risk += 1;
            if (idleSeconds >= 1200) counts.idle_20m += 1;
            const matches = matchesSearch(row) && matchesChipFilter(row);
            row.classList.toggle('d-none', !matches);
            const detailRow = document.querySelector(`tr[data-detail-for="${row.dataset.deviceId}"]`);
            if (detailRow) {
                detailRow.classList.toggle('d-none', !matches);
            }
            if (matches) {
                visibleCount += 1;
            }
        });
        if (dom.emptyState) {
            dom.emptyState.classList.toggle('d-none', visibleCount > 0);
        }
        setText(dom.visibleCount, String(visibleCount));
        setText(dom.chipCountAll, String(counts.all));
        setText(dom.chipCountOnline, String(counts.online));
        setText(dom.chipCountDegraded, String(counts.degraded));
        setText(dom.chipCountOffline, String(counts.offline));
        setText(dom.chipCountAttention, String(counts.needs_attention));
        setText(dom.chipCountHighCpu, String(counts.high_cpu));
        setText(dom.chipCountHighRisk, String(counts.high_risk));
        setText(dom.chipCountIdle, String(counts.idle_20m));
    }

    function matchesSearch(row) {
        if (!state.search) {
            return true;
        }
        return String(row.dataset.searchIndex || '').includes(state.search);
    }

    function matchesChipFilter(row) {
        const status = String(row.dataset.status || 'offline');
        const cpu = toNumber(row.dataset.cpu, 0);
        const idleSeconds = toNumber(row.dataset.idleSeconds, 0);
        const risk = String(row.dataset.risk || 'unknown');
        const activeViolationCount = toNumber(row.dataset.activeViolationCount, 0);
        switch (state.filter) {
            case 'online':
                return status === 'online';
            case 'degraded':
                return status === 'degraded';
            case 'offline':
                return status === 'offline';
            case 'needs_attention':
                return activeViolationCount > 0;
            case 'high_cpu':
                return cpu >= 85;
            case 'high_risk':
                return risk === 'high';
            case 'idle_20m':
                return idleSeconds >= 1200;
            default:
                return true;
        }
    }

    function computeRiskLevel(status, cpu, idleSeconds, probeError, metricsAvailable, activeViolationCount, highestViolationSeverity) {
        const violationCount = Math.max(0, Math.floor(toNumber(activeViolationCount, 0)));
        const violationSeverity = normalizeSeverity(highestViolationSeverity);
        if (violationCount > 0) {
            if (violationSeverity === 'HIGH') {
                return { level: 'high', label: 'HIGH', badgeClass: 'fleet-risk-high' };
            }
            return { level: 'medium', label: 'MEDIUM', badgeClass: 'fleet-risk-medium' };
        }
        if (status === 'offline' || probeError.startsWith('INTEGRITY_')) {
            return { level: 'high', label: 'HIGH', badgeClass: 'fleet-risk-high' };
        }
        if (status === 'degraded' || cpu >= 85 || !metricsAvailable) {
            return { level: 'medium', label: 'MEDIUM', badgeClass: 'fleet-risk-medium' };
        }
        if (idleSeconds >= 1200 || cpu >= 65) {
            return { level: 'medium', label: 'MEDIUM', badgeClass: 'fleet-risk-medium' };
        }
        return { level: 'low', label: 'LOW', badgeClass: 'fleet-risk-low' };
    }

    function normalizeSeverity(value) {
        const normalized = String(value || '').trim().toUpperCase();
        if (normalized === 'HIGH' || normalized === 'CRITICAL') {
            return 'HIGH';
        }
        if (normalized === 'MEDIUM' || normalized === 'WARNING' || normalized === 'DEGRADED') {
            return 'MEDIUM';
        }
        if (normalized === 'LOW' || normalized === 'INFO') {
            return 'LOW';
        }
        return 'LOW';
    }

    function buildStatusReason(device) {
        if (device.probe_error_code) {
            return String(device.probe_error_code).replace(/_/g, ' ');
        }
        if (device.metrics_available === false && normalizeStatus(device.availability_status || device.status) !== 'offline') {
            return 'Telemetry partial';
        }
        return 'Telemetry healthy';
    }

    function normalizeStatus(value) {
        const status = String(value || 'offline').toLowerCase();
        if (status === 'online' || status === 'degraded' || status === 'offline') {
            return status;
        }
        return 'offline';
    }

    function statusBadgeClass(status) {
        if (status === 'online') return 'tactical-badge-success';
        if (status === 'degraded') return 'tactical-badge-warning';
        return 'tactical-badge-secondary';
    }

    function updateDeviceTelemetry(deviceId, cpu, mem, disk) {
        const row = document.getElementById(`fleet-device-${deviceId}`);
        if (!row) {
            return;
        }

        const metrics = [
            { bar: '.fleet-cpu-bar', value: '.fleet-cpu-val', percent: cpu },
            { bar: '.fleet-mem-bar', value: '.fleet-mem-val', percent: mem },
            { bar: '.fleet-disk-bar', value: '.fleet-disk-val', percent: disk },
        ];

        metrics.forEach(({ bar, value, percent }) => {
            const clamped = clampPercent(percent);
            const tone = getTelemetryTone(clamped);
            const barNode = row.querySelector(bar);
            const valueNode = row.querySelector(value);

            if (barNode) {
                barNode.style.transform = `scaleX(${clamped / 100})`;
                barNode.style.background = tone;
            }
            if (valueNode) {
                valueNode.textContent = formatTelemetryPercent(clamped);
                valueNode.style.color = tone;
            }
        });

        row.dataset.cpu = String(clampPercent(cpu));
    }

    function getTelemetryTone(percent) {
        if (percent >= 80) return 'var(--s-critical)';
        if (percent >= 60) return 'var(--s-warning)';
        return 'var(--s-healthy)';
    }

    function clampPercent(value) {
        const percent = toNumber(value, 0);
        return Math.max(0, Math.min(100, percent));
    }

    function formatTelemetryPercent(value) {
        return `${Math.round(clampPercent(value))}%`;
    }

    function showError(message) {
        if (!dom.errorBanner) return;
        dom.errorBanner.textContent = message;
        dom.errorBanner.classList.remove('d-none');
    }

    function clearError() {
        if (!dom.errorBanner) return;
        dom.errorBanner.textContent = '';
        dom.errorBanner.classList.add('d-none');
    }

    function updatePollingMeta(lastPollDurationMs, deviceCount) {
        if (dom.pollDot) {
            dom.pollDot.classList.remove('offline');
            dom.pollDot.classList.add(state.auto ? 'healthy' : 'offline');
        }
        if (!dom.pollMeta) {
            return;
        }
        if (!state.lastSuccessTs) {
            dom.pollMeta.textContent = state.auto
                ? `Polling every ${Math.round(refreshMs / 1000)}s - waiting for first success`
                : 'Polling paused';
            return;
        }
        const ageSeconds = Math.max(0, Math.floor((Date.now() - state.lastSuccessTs) / 1000));
        const durationText = Number.isFinite(lastPollDurationMs) ? `${Math.round(lastPollDurationMs)}ms` : '--';
        const deviceText = Number.isFinite(deviceCount) ? `${deviceCount} devices` : '--';
        dom.pollMeta.textContent = state.auto
            ? `Polling every ${Math.round(refreshMs / 1000)}s - last ${durationText} - ${deviceText} - updated ${ageSeconds}s ago`
            : `Polling paused - last success ${ageSeconds}s ago`;
    }

    function updateLastUpdate(timestampMs) {
        if (!dom.lastUpdate) {
            return;
        }
        if (!timestampMs) {
            dom.lastUpdate.textContent = 'Never updated';
            return;
        }
        dom.lastUpdate.textContent = `Updated ${new Date(timestampMs).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}`;
    }

    function formatTimestamp(isoValue) {
        const parsed = parseUniversalDate(isoValue);
        if (!parsed) return 'Never';
        return parsed.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });
    }

    async function requestJson(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...(options || {}),
        });
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        let payload = null;

        if (contentType.includes('application/json')) {
            payload = await response.json().catch(() => null);
        } else {
            const bodyText = await response.text().catch(() => '');
            if (
                response.status === 401 ||
                response.status === 403 ||
                response.redirected ||
                /<form[^>]*login|name=["']username["']/i.test(bodyText)
            ) {
                const authError = new Error('Session expired. Please sign in again.');
                authError.status = 401;
                throw authError;
            }
            const nonJsonError = new Error(`Unexpected response format (${response.status}).`);
            nonJsonError.status = response.status;
            throw nonJsonError;
        }

        const errorMessage = payload?.error || payload?.message || `Request failed (${response.status}).`;
        if (response.status === 401) {
            const authError = new Error('Session expired. Please sign in again.');
            authError.status = 401;
            throw authError;
        }
        if (response.status === 403) {
            const forbiddenError = new Error('Access denied for this view.');
            forbiddenError.status = 403;
            throw forbiddenError;
        }
        if (!response.ok || payload?.success === false) {
            const requestError = new Error(errorMessage);
            requestError.status = response.status;
            throw requestError;
        }
        return { response, payload: payload || {} };
    }

    function parseUniversalDate(value) {
        if (value instanceof Date) {
            return Number.isNaN(value.getTime()) ? null : value;
        }
        if (typeof value === 'number') {
            const parsedFromNumber = new Date(value);
            return Number.isNaN(parsedFromNumber.getTime()) ? null : parsedFromNumber;
        }
        const raw = String(value || '').trim();
        if (!raw) {
            return null;
        }
        if (/^\d+$/.test(raw)) {
            const numeric = Number(raw);
            if (Number.isFinite(numeric)) {
                const ts = raw.length <= 10 ? numeric * 1000 : numeric;
                const parsedFromEpoch = new Date(ts);
                if (!Number.isNaN(parsedFromEpoch.getTime())) {
                    return parsedFromEpoch;
                }
            }
        }
        let normalized = raw;
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$/.test(raw)) {
            normalized = raw.replace(' ', 'T');
        }
        if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(normalized)) {
            normalized = `${normalized}Z`;
        }
        const parsed = new Date(normalized);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function formatAgeFromSeconds(seconds) {
        if (!Number.isFinite(Number(seconds))) {
            return 'n/a';
        }
        const value = Math.max(0, Math.floor(Number(seconds)));
        if (value < 60) return `${value}s ago`;
        if (value < 3600) return `${Math.floor(value / 60)}m ago`;
        return `${Math.floor(value / 3600)}h ago`;
    }

    function setText(node, value) {
        if (node) {
            if (node.textContent !== value) {
                node.textContent = value;
                node.classList.remove('kpi-updated');
                void node.offsetWidth; // force reflow to restart animation
                node.classList.add('kpi-updated');
            }
        }
    }

    function ensureObject(value) {
        return value && typeof value === 'object' ? value : {};
    }

    function toNumber(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }
})();
