const monitoringSocket = io('/monitoring', { autoConnect: true });
    const detailsLiveState = {
        deviceId: null,
        handler: null
    };

    // Global State Model
    const deviceState = {
        devices: new Map(), // Key: String(id), Value: { id, name, ip, type, subnet, status, latency, element, ... }
        filters: {
            search: '',
            status: '',
            type: '',
            subnet: ''
        },
        // Cache DOM elements
        dom: {
            tableBody: null,
            countSpan: null,
            bulkDiv: null
        }
    };

    document.addEventListener('DOMContentLoaded', async function () {
        // Initialize DOM refs
        deviceState.dom.tableBody = document.querySelector("table tbody");
        deviceState.dom.countSpan = document.getElementById('selectedCount');
        deviceState.dom.bulkDiv = document.getElementById('bulkActions');

        // Hydrate State from Server-Rendered HTML
        initDeviceState();

        // Bulk Selection Logic
        const selectAll = document.getElementById('selectAllCheckbox');

        if (selectAll) {
            selectAll.addEventListener('change', function (e) {
                const checked = e.target.checked;
                const visibleCheckboxes = getVisibleDeviceCheckboxes();
                visibleCheckboxes.forEach(cb => {
                    cb.checked = checked;
                });
                updateBulkUI();
            });
        }

        getAllDeviceCheckboxes().forEach(cb => {
            cb.addEventListener('change', updateBulkUI);
        });

        // Initialize status polling (await first stable status render)
        await fetchAllStatuses();
        startStatusPolling();

        // Auto-classify (deferred)
        const unclassifiedCount = {{ unclassified_count|default (0, true) | int
    }};
    if (unclassifiedCount > 0) {
        const idleCallback = window.requestIdleCallback || (cb => setTimeout(cb, 2000));
        idleCallback(() => autoClassifyDevicesIfNeeded(unclassifiedCount), { timeout: 5000 });
    }

    bindStatusPollingToModal();

    // Check for edit params
    const urlParams = new URLSearchParams(window.location.search);
    const modal = document.getElementById('deviceModal');
    if (modal && urlParams.has('edit_id')) {
        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    }

    // Attach Filter Listeners
    attachFilterListeners();
    });

    // --- Core Architecture: State Management ---

    function initDeviceState() {
        if (!deviceState.dom.tableBody) return;
        const rows = deviceState.dom.tableBody.getElementsByTagName("tr");

        for (let i = 0; i < rows.length; i++) {
            const row = rows[i];
            const id = row.getAttribute('data-device-id');
            if (!id) continue;

            const name = (row.cells[1]?.textContent || "").toLowerCase().trim();
            const ip = (row.cells[2]?.textContent || "").toLowerCase().trim();

            // Extract Type value efficiently
            const select = row.querySelector('select');
            const type = select ? select.value.toLowerCase() : "unknown";

            // Extract initial status
            const statusIndicator = row.querySelector('.status-indicator');
            let status = 'unknown';
            if (statusIndicator) {
                if (statusIndicator.classList.contains('status-online')) status = 'online';
                else if (statusIndicator.classList.contains('status-offline')) status = 'offline';
                else if (statusIndicator.classList.contains('status-maintenance')) status = 'maintenance';
            }

            const subnet = row.getAttribute('data-subnet') || '';

            deviceState.devices.set(id, {
                id: id,
                name: name,
                ip: ip,
                type: type,
                subnet: subnet,
                status: status,
                latency: null,
                isVisible: true,
                // DOM References for fast patching
                dom: {
                    row: row,
                    statusIndicator: statusIndicator,
                    typeSelect: select
                }
            });
        }
    }

    function getAllDeviceCheckboxes() {
        return Array.from(document.querySelectorAll('.device-checkbox'));
    }

    function getVisibleDeviceCheckboxes() {
        return getAllDeviceCheckboxes().filter(cb => {
            const row = cb.closest('tr');
            return row && !row.classList.contains('d-none');
        });
    }

    // --- Core Architecture: Delta Patching ---

    function patchDeviceRow(id, newStatus, newLatency) {
        const device = deviceState.devices.get(String(id));
        if (!device) return;

        const normalizedStatus = (newStatus || 'unknown').toLowerCase();

        // 1. Update Model
        const statusChanged = device.status !== normalizedStatus;
        const latencyChanged = device.latency !== newLatency;

        device.status = normalizedStatus;
        device.latency = newLatency;

        // 2. Patch DOM only if changed
        if (statusChanged || latencyChanged) {
            const el = device.dom.statusIndicator;
            if (el) {
                // Determine class
                let statusClass = 'status-unknown';
                if (normalizedStatus === 'online') statusClass = 'status-online';
                else if (normalizedStatus === 'offline') statusClass = 'status-offline';
                else if (normalizedStatus === 'maintenance') statusClass = 'status-maintenance';

                // Class update
                if (!el.classList.contains(statusClass)) {
                    el.className = 'status-indicator ' + statusClass;
                }

                // Title update
                const latencyText = (newLatency != null && normalizedStatus === 'online')
                    ? ` (${Math.round(newLatency)}ms)` : '';
                const nextTitle = (normalizedStatus || 'Unknown') + latencyText;

                if (el.title !== nextTitle) {
                    el.title = nextTitle;
                }
            }
        }
    }

    // --- Core Architecture: In-Memory Filtering ---

    let filterRafId = null;

    function attachFilterListeners() {
        // Debounce typing only; select filters should react immediately.
        const debouncedFilter = debounce(applyFilters, 150);

        const searchInput = document.getElementById('deviceSearch');
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                updateFilterState();
                debouncedFilter();
            });
        }

        ['statusFilter', 'typeFilter', 'subnetFilter'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => {
                    updateFilterState();
                    applyFilters();
                });
            }
        });

        updateFilterState();
        applyFilters();
    }

    function updateFilterState() {
        const sInput = document.getElementById("deviceSearch");
        deviceState.filters.search = (sInput ? sInput.value : "").toLowerCase().trim();

        const stSelect = document.getElementById("statusFilter");
        deviceState.filters.status = stSelect ? stSelect.value.toLowerCase() : "";

        const tSelect = document.getElementById("typeFilter");
        deviceState.filters.type = tSelect ? tSelect.value.toLowerCase().trim() : "";

        const subSelect = document.getElementById("subnetFilter");
        deviceState.filters.subnet = subSelect ? subSelect.value : "";
    }

    function applyFilters() {
        const { search, status, type, subnet } = deviceState.filters;

        const normalizeType = (val) => {
            const v = val || "";
            if (v === "camera" || v === "camera/iot" || v === "camera_iot") return "camera";
            return v;
        };
        const targetType = normalizeType(type);

        const visibilityChanges = [];
        deviceState.devices.forEach(device => {
            const matchSearch = device.name.includes(search) || device.ip.includes(search);
            const matchStatus = status === "" || device.status === status;
            const matchType = type === "" || normalizeType(device.type) === targetType;
            const matchSubnet = subnet === "" || device.subnet === subnet;
            const isVisible = matchSearch && matchStatus && matchType && matchSubnet;

            if (device.isVisible !== isVisible) {
                device.isVisible = isVisible;
                visibilityChanges.push({ row: device.dom.row, hidden: !isVisible });
            }
        });

        if (visibilityChanges.length === 0) return;

        if (filterRafId) {
            cancelAnimationFrame(filterRafId);
        }

        // Batch DOM writes for only rows whose visibility changed.
        filterRafId = requestAnimationFrame(() => {
            for (let i = 0; i < visibilityChanges.length; i++) {
                const change = visibilityChanges[i];
                change.row.classList.toggle('d-none', change.hidden);
                if (change.hidden) {
                    const checkbox = change.row.querySelector('.device-checkbox');
                    if (checkbox && checkbox.checked) {
                        checkbox.checked = false;
                    }
                }
            }
            filterRafId = null;
            updateBulkUI();
        });
    }

    // --- Standard Logic ---

    function toggleMonitoringFields() {
        const mode = document.querySelector('input[name="monitoring_mode"]:checked').value;
        document.getElementById('snmpFields').style.display = mode === 'snmp' ? 'block' : 'none';
        document.getElementById('agentFields').style.display = mode === 'agent' ? 'block' : 'none';
        document.getElementById('wmiFields').style.display = mode === 'wmi' ? 'block' : 'none';
    }

    function toggleSnmpVersion() {
        const version = document.getElementById('snmp_version').value;
        const v2Fields = document.getElementById('snmp_v2_fields');
        const v3Fields = document.getElementById('snmp_v3_fields');

        if (version === 'v2c') {
            v2Fields.style.display = 'block';
            v3Fields.style.display = 'none';
        } else {
            v2Fields.style.display = 'none';
            v3Fields.style.display = 'block';
        }
    }

    function testConnectivity() {
        // ... (Existing logic, assuming unchanged functionality needed)
        const btn = document.getElementById('testConnectBtn');
        const resultDiv = document.getElementById('testResult');
        const originalText = btn.innerHTML;

        const ip = document.getElementById('device_ip').value;
        if (!ip) {
            resultDiv.innerHTML = '<div class="alert alert-danger py-1">IP Address is required</div>';
            return;
        }

        const mode = document.querySelector('input[name="monitoring_mode"]:checked').value;
        const formData = {
            ip: ip,
            mode: mode,
            snmp_community: document.getElementById('snmp_community').value,
            snmp_version: document.getElementById('snmp_version').value,
            snmp_port: document.getElementById('snmp_port').value,
        };

        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Testing...';
        resultDiv.innerHTML = '';

        fetch('/api/check_connectivity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        })
            .then(response => response.json())
            .then(data => {
                resultDiv.innerHTML = data.success
                    ? `<div class="alert alert-success py-1"><i class="fas fa-check-circle"></i> ${data.message}</div>`
                    : `<div class="alert alert-danger py-1"><i class="fas fa-times-circle"></i> ${data.message}</div>`;
            })
            .catch(err => {
                resultDiv.innerHTML = `<div class="alert alert-danger py-1">Error: ${err.message}</div>`;
            })
            .finally(() => {
                btn.disabled = false;
                btn.innerHTML = originalText;
            });
    }

    // Reset modal on open
    const deviceModalEl = document.getElementById('deviceModal');
    if (deviceModalEl) {
        deviceModalEl.addEventListener('show.bs.modal', function (event) {
            document.getElementById('deviceForm').reset();
            document.getElementById('device_id').value = '';
            const firstTab = document.querySelector('#deviceTabs button:first-child');
            if (firstTab) new bootstrap.Tab(firstTab).show();
            toggleMonitoringFields();
            toggleSnmpVersion();
            document.getElementById('testResult').innerHTML = '';
        });
    }

    function updateBulkUI() {
        const visibleCheckboxes = getVisibleDeviceCheckboxes();
        const selectedVisible = visibleCheckboxes.filter(cb => cb.checked);
        const count = selectedVisible.length;
        const selectAll = document.getElementById('selectAllCheckbox');

        if (selectAll) {
            if (visibleCheckboxes.length === 0) {
                selectAll.checked = false;
                selectAll.indeterminate = false;
            } else {
                selectAll.checked = count > 0 && count === visibleCheckboxes.length;
                selectAll.indeterminate = count > 0 && count < visibleCheckboxes.length;
            }
        }

        if (count > 0 && deviceState.dom.bulkDiv) {
            deviceState.dom.bulkDiv.style.display = 'flex';
            if (deviceState.dom.countSpan) deviceState.dom.countSpan.textContent = count;
        } else if (deviceState.dom.bulkDiv) {
            deviceState.dom.bulkDiv.style.display = 'none';
        }
    }

    function deleteSelectedDevices() {
        // ... (Existing logic)
        const selected = getVisibleDeviceCheckboxes().filter(cb => cb.checked);
        const ids = Array.from(selected).map(cb => parseInt(cb.value));
        if (ids.length === 0) return;

        if (!confirm(`Are you sure you want to delete ${ids.length} device(s)? This cannot be undone.`)) return;

        fetch('/api/devices/bulk_delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_ids: ids })
        }).then(res => res.json()).then(data => {
            if (data.success) {
                location.reload();
            } else {
                alert(`Error: ${data.error}`);
            }
        });
    }

    function updateDeviceType(deviceId, newType, selectIdx) {
        // Optimistic UI Update first? 
        // User requested: "Update in-memory model and re-render row only" for auto-classify.
        // For manual update, we can also be optimistic.

        fetch('/api/devices/' + deviceId + '/update_type', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_type: newType })
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Update State
                    const device = deviceState.devices.get(String(deviceId));
                    if (device) {
                        device.type = newType;
                        // Re-filter in case looking at specific type
                        applyFilters();
                    }

                    // Show Toast
                    showToast(`Device type updated to ${newType}`, 'success');
                } else {
                    alert('Error updating type: ' + data.error);
                }
            });
    }

    function showToast(msg, type = 'success') {
        const toast = document.createElement('div');
        toast.className = 'position-fixed bottom-0 end-0 p-3';
        toast.style.zIndex = '9999';
        toast.innerHTML = `
        <div class="toast show align-items-center text-white bg-${type} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">${msg}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function safeText(value, fallback = 'N/A') {
        if (value === null || value === undefined || value === '') return fallback;
        return escapeHtml(value);
    }

    function formatDateTime(value) {
        if (!value) return 'N/A';
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return safeText(value);
        return escapeHtml(dt.toLocaleString());
    }

    function formatSpeed(speedBps) {
        const speed = Number(speedBps);
        if (!Number.isFinite(speed) || speed <= 0) return 'N/A';
        if (speed >= 1000000000) {
            const gbps = speed / 1000000000;
            return `${Number.isInteger(gbps) ? gbps : gbps.toFixed(1)} Gbps`;
        }
        if (speed >= 1000000) {
            const mbps = speed / 1000000;
            return `${Number.isInteger(mbps) ? mbps : mbps.toFixed(1)} Mbps`;
        }
        const kbps = speed / 1000;
        return `${Number.isInteger(kbps) ? kbps : kbps.toFixed(1)} Kbps`;
    }

    function formatOctets(value) {
        if (value === null || value === undefined || value === '') return 'N/A';
        const num = Number(value);
        if (!Number.isFinite(num)) return 'N/A';
        return Math.round(num).toLocaleString();
    }

    function formatUptimeSeconds(secondsValue) {
        const seconds = Number(secondsValue);
        if (!Number.isFinite(seconds) || seconds < 0) return 'N/A';
        const total = Math.floor(seconds);
        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const mins = Math.floor((total % 3600) / 60);
        const secs = total % 60;
        if (days > 0) return `${days}d ${hours}h ${mins}m`;
        if (hours > 0) return `${hours}h ${mins}m ${secs}s`;
        if (mins > 0) return `${mins}m ${secs}s`;
        return `${secs}s`;
    }

    function getUsageClass(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return 'bg-secondary';
        if (n < 70) return 'bg-success';
        if (n < 90) return 'bg-warning';
        return 'bg-danger';
    }

    function setUsageBar(metricName, value) {
        const row = document.getElementById(`details-${metricName}-row`);
        const bar = document.getElementById(`details-${metricName}-bar`);
        const label = document.getElementById(`details-${metricName}-value`);
        if (!row || !bar || !label) return false;

        const n = Number(value);
        if (!Number.isFinite(n)) {
            row.classList.add('d-none');
            label.textContent = 'N/A';
            bar.style.width = '0%';
            bar.className = 'progress-bar bg-secondary';
            return false;
        }

        const bounded = Math.max(0, Math.min(100, n));
        row.classList.remove('d-none');
        label.textContent = `${bounded.toFixed(1)}%`;
        bar.style.width = `${bounded}%`;
        bar.setAttribute('aria-valuenow', String(bounded));
        bar.className = `progress-bar ${getUsageClass(bounded)}`;
        return true;
    }

    function buildInterfaceRows(interfaces, counters) {
        const ifaceList = Array.isArray(interfaces) ? interfaces : [];
        const counterList = Array.isArray(counters) ? counters : [];
        const countersByIndex = new Map();

        for (const counter of counterList) {
            if (!counter || counter.if_index === undefined || counter.if_index === null) continue;
            countersByIndex.set(String(counter.if_index), counter);
        }

        const rows = [];
        for (const iface of ifaceList) {
            if (!iface || iface.if_index === undefined || iface.if_index === null) continue;
            const ifIndexKey = String(iface.if_index);
            const counter = countersByIndex.get(ifIndexKey) || {};
            const oper = String(iface.oper_status || 'unknown').toLowerCase();
            const operBadge = oper === 'up'
                ? '<span class="badge bg-success">up</span>'
                : oper === 'down'
                    ? '<span class="badge bg-danger">down</span>'
                    : `<span class="badge bg-secondary">${safeText(oper, 'unknown')}</span>`;

            rows.push(`
                <tr>
                    <td>${safeText(iface.if_index)}</td>
                    <td>${safeText(iface.name, 'N/A')}</td>
                    <td>${safeText(iface.admin_status, 'unknown')}</td>
                    <td>${operBadge}</td>
                    <td>${escapeHtml(formatSpeed(iface.speed_bps))}</td>
                    <td>${escapeHtml(formatOctets(counter.in_octets))}</td>
                    <td>${escapeHtml(formatOctets(counter.out_octets))}</td>
                </tr>
            `);
        }

        return rows;
    }

    function updatePerformanceTab(health) {
        const noData = document.getElementById('details-performance-empty');
        const hasCpu = setUsageBar('cpu', health ? health.cpu_usage : null);
        const hasMem = setUsageBar('memory', health ? health.memory_usage : null);
        const hasDisk = setUsageBar('disk', health ? health.disk_usage : null);

        if (noData) {
            noData.classList.toggle('d-none', hasCpu || hasMem || hasDisk);
        }
    }

    function updateInterfacesTab(interfaces, counters) {
        const tbody = document.getElementById('details-interfaces-body');
        const empty = document.getElementById('details-interfaces-empty');
        if (!tbody || !empty) return;

        const rows = buildInterfaceRows(interfaces, counters);
        if (rows.length === 0) {
            tbody.innerHTML = '';
            empty.classList.remove('d-none');
            return;
        }

        empty.classList.add('d-none');
        tbody.innerHTML = rows.join('');
    }

    function teardownDeviceDetailsLive() {
        if (!monitoringSocket) {
            detailsLiveState.deviceId = null;
            detailsLiveState.handler = null;
            return;
        }

        if (detailsLiveState.deviceId !== null) {
            monitoringSocket.emit('unsubscribe_device', { device_id: detailsLiveState.deviceId });
        }
        if (detailsLiveState.handler) {
            monitoringSocket.off('snmp_metrics', detailsLiveState.handler);
        }
        detailsLiveState.deviceId = null;
        detailsLiveState.handler = null;
    }

    function renderDetailsModal(deviceData, liveMetrics = {}) {
        const health = (liveMetrics && typeof liveMetrics === 'object' && liveMetrics.health) ? liveMetrics.health : {};
        const system = (liveMetrics && typeof liveMetrics === 'object' && liveMetrics.system) ? liveMetrics.system : {};
        const interfaces = Array.isArray(liveMetrics && liveMetrics.interfaces) ? liveMetrics.interfaces : [];
        const counters = Array.isArray(liveMetrics && liveMetrics.counters) ? liveMetrics.counters : [];

        const stateDevice = deviceState.devices.get(String(deviceData.device_id));
        const statusText = stateDevice ? stateDevice.status : 'unknown';
        const statusBadge = statusText === 'online'
            ? '<span class="badge bg-success">Online</span>'
            : statusText === 'offline'
                ? '<span class="badge bg-danger">Offline</span>'
                : statusText === 'maintenance'
                    ? '<span class="badge bg-warning text-dark">Maintenance</span>'
                    : '<span class="badge bg-secondary">Unknown</span>';

        const isSnmpEnabled = Boolean(
            (deviceData && deviceData.snmp_enabled) ||
            (liveMetrics && liveMetrics.snmp_enabled)
        );
        const liveBadge = isSnmpEnabled
            ? '<span class="ms-2 badge border border-success text-success"><span class="spinner-grow spinner-grow-sm me-1" style="width:0.5rem;height:0.5rem;" aria-hidden="true"></span>Live</span>'
            : '';

        const snmpSystemHtml = Object.keys(system || {}).length > 0
            ? `
                <div class="col-md-6"><div class="detail-item"><div class="detail-label">SNMP Sys Name</div><div class="detail-value">${safeText(system.sys_name)}</div></div></div>
                <div class="col-md-6"><div class="detail-item"><div class="detail-label">SNMP Uptime</div><div class="detail-value">${escapeHtml(formatUptimeSeconds(system.sys_uptime_seconds))}</div></div></div>
                <div class="col-md-6"><div class="detail-item"><div class="detail-label">SNMP Location</div><div class="detail-value">${safeText(system.sys_location)}</div></div></div>
                <div class="col-md-6"><div class="detail-item"><div class="detail-label">SNMP Description</div><div class="detail-value">${safeText(system.sys_descr)}</div></div></div>
            `
            : '<div class="col-12"><div class="detail-item"><div class="detail-label">SNMP System</div><div class="detail-value">No SNMP system data available</div></div></div>';

        let modal = document.getElementById('deviceDetailsModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'deviceDetailsModal';
            modal.className = 'modal fade';
            modal.setAttribute('tabindex', '-1');
            modal.setAttribute('aria-hidden', 'true');
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
        <div class="modal-dialog modal-xl">
            <div class="modal-content bg-dark text-white border-secondary">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title">
                        ${safeText(deviceData.device_name)}
                        <small class="text-muted ms-2">(${safeText(deviceData.device_ip)})</small>
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <ul class="nav nav-tabs border-secondary mb-3" role="tablist">
                        <li class="nav-item" role="presentation">
                            <button class="nav-link active text-light" data-bs-toggle="tab" data-bs-target="#device-details-overview" type="button" role="tab">Overview</button>
                        </li>
                        <li class="nav-item" role="presentation">
                            <button class="nav-link text-light d-flex align-items-center" data-bs-toggle="tab" data-bs-target="#device-details-performance" type="button" role="tab">
                                <span>Performance</span>${liveBadge}
                            </button>
                        </li>
                        <li class="nav-item" role="presentation">
                            <button class="nav-link text-light" data-bs-toggle="tab" data-bs-target="#device-details-interfaces" type="button" role="tab">Interfaces</button>
                        </li>
                    </ul>

                    <div class="tab-content device-details-container">
                        <div class="tab-pane fade show active" id="device-details-overview" role="tabpanel">
                            <div class="row">
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Name</div><div class="detail-value">${safeText(deviceData.device_name)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">IP Address</div><div class="detail-value">${safeText(deviceData.device_ip)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Status</div><div class="detail-value">${statusBadge}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Type</div><div class="detail-value">${safeText(deviceData.device_type)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">MAC Address</div><div class="detail-value">${safeText(deviceData.macaddress)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Location</div><div class="detail-value">${safeText(deviceData.location)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Created</div><div class="detail-value">${formatDateTime(deviceData.created_at)}</div></div></div>
                                <div class="col-md-6"><div class="detail-item"><div class="detail-label">Updated</div><div class="detail-value">${formatDateTime(deviceData.updated_at || deviceData.last_seen)}</div></div></div>
                                <div class="col-12"><div class="detail-item"><div class="detail-label">Description</div><div class="detail-value">${safeText(deviceData.description)}</div></div></div>
                            </div>
                            <hr class="my-3">
                            <div class="row">
                                ${snmpSystemHtml}
                            </div>
                        </div>

                        <div class="tab-pane fade" id="device-details-performance" role="tabpanel">
                            <div id="details-performance-empty" class="alert alert-secondary py-2 d-none">No SNMP data available</div>

                            <div class="mb-3 d-none" id="details-cpu-row">
                                <div class="d-flex justify-content-between mb-1">
                                    <span class="small text-muted">CPU usage</span>
                                    <span class="small text-light" id="details-cpu-value">N/A</span>
                                </div>
                                <div class="progress bg-black border border-secondary" style="height: 12px;">
                                    <div id="details-cpu-bar" class="progress-bar bg-secondary" role="progressbar" style="width: 0%" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                            </div>

                            <div class="mb-3 d-none" id="details-memory-row">
                                <div class="d-flex justify-content-between mb-1">
                                    <span class="small text-muted">Memory usage</span>
                                    <span class="small text-light" id="details-memory-value">N/A</span>
                                </div>
                                <div class="progress bg-black border border-secondary" style="height: 12px;">
                                    <div id="details-memory-bar" class="progress-bar bg-secondary" role="progressbar" style="width: 0%" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                            </div>

                            <div class="mb-3 d-none" id="details-disk-row">
                                <div class="d-flex justify-content-between mb-1">
                                    <span class="small text-muted">Disk usage</span>
                                    <span class="small text-light" id="details-disk-value">N/A</span>
                                </div>
                                <div class="progress bg-black border border-secondary" style="height: 12px;">
                                    <div id="details-disk-bar" class="progress-bar bg-secondary" role="progressbar" style="width: 0%" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="device-details-interfaces" role="tabpanel">
                            <div id="details-interfaces-empty" class="alert alert-secondary py-2 d-none">No interfaces found</div>
                            <div class="table-responsive">
                                <table class="table table-sm table-dark table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th>Index</th>
                                            <th>Name</th>
                                            <th>Admin</th>
                                            <th>Oper</th>
                                            <th>Speed</th>
                                            <th>In Traffic</th>
                                            <th>Out Traffic</th>
                                        </tr>
                                    </thead>
                                    <tbody id="details-interfaces-body"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;

        updatePerformanceTab(health);
        updateInterfacesTab(interfaces, counters);
    }

    async function showDeviceDetails(deviceId) {
        try {
            const detailsResp = await fetch('/api/devices/' + deviceId);
            if (!detailsResp.ok) {
                throw new Error(`Device details request failed (${detailsResp.status})`);
            }
            const deviceData = await detailsResp.json();
            if (deviceData.error) {
                alert(deviceData.error);
                return;
            }

            let liveMetrics = {};
            try {
                const liveResp = await fetch(`/api/devices/${deviceId}/live_metrics`);
                if (liveResp.ok) {
                    liveMetrics = await liveResp.json();
                } else if (liveResp.status !== 404) {
                    console.warn('Live metrics endpoint returned:', liveResp.status);
                }
            } catch (liveErr) {
                console.warn('Live metrics fetch failed:', liveErr);
            }

            renderDetailsModal(deviceData, liveMetrics);

            const modalEl = document.getElementById('deviceDetailsModal');
            if (!modalEl) return;
            const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl);

            teardownDeviceDetailsLive();

            const onShown = () => {
                if (!monitoringSocket) return;
                const handler = (payload) => {
                    if (!payload || Number(payload.device_id) !== Number(deviceId)) return;
                    updatePerformanceTab(payload.health || {});
                    updateInterfacesTab(payload.interfaces || [], payload.counters || []);
                };
                detailsLiveState.deviceId = deviceId;
                detailsLiveState.handler = handler;
                monitoringSocket.on('snmp_metrics', handler);
                monitoringSocket.emit('subscribe_device', { device_id: deviceId });
            };

            const onHidden = () => {
                teardownDeviceDetailsLive();
                modalEl.removeEventListener('hidden.bs.modal', onHidden);
            };

            modalEl.addEventListener('shown.bs.modal', onShown, { once: true });
            modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });
            bsModal.show();
        } catch (err) {
            alert(`Failed to load device details: ${err.message}`);
        }
    }

    function deleteDevice(deviceId) {
        if (confirm('Are you sure you want to delete this device?')) {
            window.location.href = '/devices?delete_id=' + deviceId;
        }
    }

    // --- Optimized Polling ---

    let statusPollingInterval = null;
    let statusFetchInFlight = false;

    async function fetchAllStatuses() {
        if (window.cleanupBootstrapModal && !document.querySelector('.modal.show')) {
            window.cleanupBootstrapModal();
        }

        if (statusFetchInFlight) return;
        statusFetchInFlight = true;

        try {
            const response = await fetch('/api/monitoring/status?mode=latest&fallback=live');
            if (!response.ok) throw new Error('Network response not ok');
            const data = await response.json();
            const devices = Array.isArray(data.devices) ? data.devices : [];

            // Batch Updates
            requestAnimationFrame(() => {
                for (let i = 0; i < devices.length; i++) {
                    const d = devices[i];
                    patchDeviceRow(d.device_id, d.status, d.latency);
                }

                // Keep status-based filtered views consistent after live updates.
                if (deviceState.filters.status) {
                    applyFilters();
                }
            });

        } catch (error) {
            console.error('Error fetching statuses:', error);
        } finally {
            statusFetchInFlight = false;
        }
    }

    function startStatusPolling() {
        if (statusPollingInterval) return;
        statusPollingInterval = setInterval(fetchAllStatuses, 30000);
    }

    function stopStatusPolling() {
        if (!statusPollingInterval) return;
        clearInterval(statusPollingInterval);
        statusPollingInterval = null;
    }

    function bindStatusPollingToModal() {
        document.addEventListener('show.bs.modal', stopStatusPolling);
        document.addEventListener('hidden.bs.modal', () => {
            if (!document.querySelector('.modal.show')) {
                fetchAllStatuses();
                startStatusPolling();
            }
        });
    }

    let visibilityTimeout;
    document.addEventListener('visibilitychange', function () {
        clearTimeout(visibilityTimeout);
        if (document.hidden) {
            stopStatusPolling();
        } else {
            visibilityTimeout = setTimeout(() => {
                fetchAllStatuses();
                startStatusPolling();
            }, 500);
        }
    });

    function autoClassifyDevicesIfNeeded(count) {
        if (!count || count <= 0) return;
        const key = 'autoClassifyTs';
        const lastTs = parseInt(sessionStorage.getItem(key) || '0', 10);
        const now = Date.now();
        if (lastTs && (now - lastTs) < 10 * 60 * 1000) return;

        fetch('/api/devices/reclassify_all?auto=true')
            .then(res => res.json())
            .then(data => {
                if (data.success && data.updated_devices) {
                    // Update state and UI via delta patch
                    data.updated_devices.forEach(d => {
                        const device = deviceState.devices.get(String(d.device_id));
                        if (device) {
                            device.type = d.device_type;
                            if (device.dom.typeSelect) device.dom.typeSelect.value = d.device_type;
                        }
                    });

                    sessionStorage.setItem(key, String(Date.now()));
                    applyFilters(); // Re-evaluate filters
                }
            })
            .catch(console.warn);
    }

    function debounce(func, wait) {
        let timeout;
        return function (...args) {
            const context = this;
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(context, args), wait);
        };
    }