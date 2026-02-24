(function () {
    'use strict';

    let deviceToDelete = null;
    const STATUS_REFRESH_INTERVAL_MS = 15000;

    document.addEventListener('DOMContentLoaded', initTrackingDevicePage);

    function initTrackingDevicePage() {
        bindGlobalActions();
        bindStoredDeviceActions();
        bindModalActions();
        bindFilterActions();
        bindScanActions();
        applyDeviceFilters();
        startStoredStatusRefresh();
    }

    function bindGlobalActions() {
        document.querySelectorAll('.open-add-device').forEach((button) => {
            button.addEventListener('click', openAddDeviceModal);
        });

        const scanButton = document.getElementById('trackingScanBtn');
        if (scanButton) {
            scanButton.addEventListener('click', scanNetworkDevices);
        }

        const syncButton = document.getElementById('syncBtn');
        if (syncButton) {
            syncButton.addEventListener('click', syncTrackedDeviceIps);
        }

        const exportButton = document.getElementById('exportDevicesBtn');
        if (exportButton) {
            exportButton.addEventListener('click', exportDevicesList);
        }

        const bulkEditButton = document.getElementById('bulkEditBtn');
        if (bulkEditButton) {
            bulkEditButton.addEventListener('click', () => {
                showNotification('Bulk edit is planned for a later increment.', 'info');
            });
        }

        window.openAddDeviceModal = openAddDeviceModal;
    }

    function bindStoredDeviceActions() {
        const deviceTableBody = document.getElementById('deviceList');
        if (!deviceTableBody) {
            return;
        }

        deviceTableBody.addEventListener('click', (event) => {
            const editButton = event.target.closest('.edit-device');
            if (editButton) {
                let device = {};
                const rawDevice = editButton.getAttribute('data-device');
                if (rawDevice) {
                    try {
                        device = JSON.parse(rawDevice);
                    } catch (error) {
                        showNotification('Could not load device details for editing.', 'warning');
                        return;
                    }
                }
                populateEditForm(device);
                return;
            }

            const deleteButton = event.target.closest('.delete-device');
            if (deleteButton) {
                const macAddress = deleteButton.getAttribute('data-mac');
                const deviceName = deleteButton.getAttribute('data-device-name') || 'Unknown Device';
                showDeleteConfirmation(macAddress, deviceName);
            }
        });
    }

    function bindModalActions() {
        const saveButton = document.getElementById('saveDeviceBtn');
        if (saveButton) {
            saveButton.addEventListener('click', saveTrackedDevice);
        }

        const confirmDeleteButton = document.getElementById('confirmDeleteBtn');
        if (confirmDeleteButton) {
            confirmDeleteButton.addEventListener('click', async () => {
                if (deviceToDelete) {
                    await deleteTrackedDevice(deviceToDelete);
                }
            });
        }

        const addDeviceModal = document.getElementById('addDeviceModal');
        if (addDeviceModal) {
            addDeviceModal.addEventListener('hidden.bs.modal', clearDeviceForm);
        }
    }

    function bindFilterActions() {
        const searchInput = document.getElementById('deviceSearchInput');
        const statusFilter = document.getElementById('deviceStatusFilter');

        if (searchInput) {
            searchInput.addEventListener('input', applyDeviceFilters);
        }

        if (statusFilter) {
            statusFilter.addEventListener('change', applyDeviceFilters);
        }
    }

    function bindScanActions() {
        const scanResultsBody = document.getElementById('scanResultsBody');
        if (!scanResultsBody) {
            return;
        }

        scanResultsBody.addEventListener('click', async (event) => {
            const saveButton = event.target.closest('.save-scanned-device');
            if (!saveButton || saveButton.disabled) {
                return;
            }

            const device = {
                mac_address: saveButton.dataset.mac || '',
                ip: saveButton.dataset.ip || '',
                hostname: saveButton.dataset.hostname || '',
            };

            await saveScannedDevice(device, saveButton);
        });
    }

    function startStoredStatusRefresh() {
        const trackedRows = document.querySelectorAll('#deviceList tr[data-device-row="true"][data-mac]');
        if (!trackedRows.length) {
            return;
        }
        refreshStoredDeviceStatuses();
        window.setInterval(refreshStoredDeviceStatuses, STATUS_REFRESH_INTERVAL_MS);
    }

    async function refreshStoredDeviceStatuses() {
        try {
            const response = await requestJson('/api/tracking/live-summary');
            if (!response.success || !Array.isArray(response.devices)) {
                return;
            }

            const rows = document.querySelectorAll('#deviceList tr[data-device-row="true"][data-mac]');
            if (!rows.length) return;

            const rowMap = new Map();
            rows.forEach(row => {
                const mac = safeValue(row.getAttribute('data-mac'), '').toUpperCase();
                if (mac) {
                    rowMap.set(mac, row);
                }
            });

            response.devices.forEach((device) => {
                const macAddress = safeValue(device.mac_address, '').toUpperCase();
                if (!macAddress) return;

                const row = rowMap.get(macAddress);
                if (!row) return;

                applyStoredStatusToRow(row, device);
            });

            applyDeviceFilters();
        } catch (error) {
            console.debug('Stored status refresh failed:', error?.message || error);
        }
    }

    function applyStoredStatusToRow(row, device) {
        const availabilityRaw = safeValue(device.availability_status || device.status, 'offline').toLowerCase();
        const availabilityStatus = availabilityRaw === 'online'
            ? 'online'
            : availabilityRaw === 'degraded'
                ? 'degraded'
                : 'offline';

        row.dataset.deviceStatus = availabilityStatus;

        const statusCell = row.querySelector('.tracking-status-cell');
        let statusSpan = null;
        if (statusCell) {
            statusSpan = statusCell.querySelector('.status-badge');
            if (!statusSpan) {
                // Initial generation if needed
                statusSpan = document.createElement('span');
                statusSpan.className = 'tactical-badge status-badge';
                const statusMeta = statusCell.querySelector('.tracking-status-meta');
                if (statusMeta) {
                    statusCell.insertBefore(statusSpan, statusMeta.previousElementSibling); // Before the <br> ideally
                } else {
                    statusCell.prepend(statusSpan);
                }
            }

            if (row.dataset.lastBadgeStatus !== availabilityStatus) {
                row.dataset.lastBadgeStatus = availabilityStatus;

                statusSpan.className = 'tactical-badge status-badge'; // Reset class

                if (availabilityStatus === 'online') {
                    statusSpan.classList.add('tactical-badge-healthy');
                    statusSpan.textContent = 'ONLINE';
                } else if (availabilityStatus === 'degraded') {
                    statusSpan.classList.add('tactical-badge-warning');
                    statusSpan.textContent = 'DEGRADED';
                } else {
                    statusSpan.classList.add('tactical-badge-critical');
                    statusSpan.textContent = 'OFFLINE';
                }
            }
        }

        const reason = buildProbeReason(device);
        if (statusSpan && statusSpan.title !== reason) {
            statusSpan.title = reason;
        }

        const statusMeta = row.querySelector('.tracking-status-meta');
        const metaText = `${formatProbeTimestamp(device.last_probe_at)} | ${reason}`;
        if (statusMeta && statusMeta.textContent !== metaText) {
            statusMeta.textContent = metaText;
            statusMeta.title = reason;
        }
    }

    function formatProbeTimestamp(rawTimestamp) {
        if (!rawTimestamp) {
            return 'Last probe: n/a';
        }
        const parsed = new Date(rawTimestamp);
        if (Number.isNaN(parsed.getTime())) {
            return 'Last probe: n/a';
        }
        return `Last probe: ${parsed.toLocaleTimeString()}`;
    }

    function buildProbeReason(device) {
        const availability = safeValue(device.availability_status || device.status, 'offline').toLowerCase();
        const probeMethod = safeValue(device.probe_method, '').toLowerCase();
        const probeError = safeValue(device.probe_error_code, '');

        if (availability === 'online') {
            return probeMethod ? `Probe ok via ${probeMethod}` : 'Reachable';
        }
        if (availability === 'degraded') {
            if (probeMethod === 'health') {
                return probeError ? `Health-only (${probeError})` : 'Health-only reachability';
            }
            return probeError ? `Degraded (${probeError})` : 'Degraded reachability';
        }
        return probeError ? `Offline (${probeError})` : 'Agent unreachable';
    }

    function openAddDeviceModal() {
        clearDeviceForm();
        const modalElement = document.getElementById('addDeviceModal');
        if (!modalElement) {
            return;
        }
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
    }

    function showDeleteConfirmation(macAddress, deviceName) {
        deviceToDelete = macAddress;
        const deleteDeviceName = document.getElementById('deleteDeviceName');
        if (deleteDeviceName) {
            deleteDeviceName.textContent = deviceName;
        }

        const modalElement = document.getElementById('confirmDeleteModal');
        if (!modalElement) {
            return;
        }
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
    }

    async function saveTrackedDevice() {
        const saveButton = document.getElementById('saveDeviceBtn');
        const originalLabel = saveButton ? saveButton.innerHTML : 'Save Device';

        const originalMac = (document.getElementById('editMac')?.value || '').trim();
        const payload = {
            device_name: (document.getElementById('deviceName')?.value || '').trim(),
            employee_name: (document.getElementById('employeeName')?.value || '').trim(),
            mac_address: (document.getElementById('macAddress')?.value || '').trim(),
            ip_address: (document.getElementById('ipAddress')?.value || '').trim(),
            hostname: (document.getElementById('hostname')?.value || '').trim(),
            department: (document.getElementById('department')?.value || '').trim(),
            notes: (document.getElementById('notes')?.value || '').trim(),
        };

        if (originalMac) {
            payload.mac_address = originalMac;
        }

        if (!payload.mac_address && !payload.ip_address) {
            showNotification('Provide IP address or MAC address to register device.', 'warning');
            return;
        }

        const macPattern = /^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$/;
        if (payload.mac_address && !macPattern.test(payload.mac_address)) {
            showNotification('Invalid MAC address format. Use 00:1A:2B:3C:4D:5E.', 'warning');
            return;
        }

        if (!payload.device_name) {
            payload.device_name = payload.hostname || 'Auto-Discovered Device';
        }

        setButtonLoading(saveButton, '<i data-lucide="loader" class="fa-spin" style="width:1rem;height:1rem;vertical-align:middle;"></i> Saving...');

        try {
            const response = await requestJson('/api/tracking/save-device', {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            if (!response.success) {
                showNotification(response.error || 'Save failed.', 'danger');
                return;
            }

            showNotification('Device saved successfully.', 'success');
            const modalElement = document.getElementById('addDeviceModal');
            const modal = modalElement ? bootstrap.Modal.getOrCreateInstance(modalElement) : null;
            if (modal) {
                modal.hide();
            }
            setTimeout(() => window.location.reload(), 900);
        } catch (error) {
            showNotification(error.message || 'Save failed.', 'danger');
        } finally {
            resetButtonLoading(saveButton, originalLabel);
        }
    }

    async function deleteTrackedDevice(macAddress) {
        try {
            const response = await requestJson('/api/tracking/delete-device', {
                method: 'POST',
                body: JSON.stringify({ mac_address: macAddress }),
            });

            if (!response.success) {
                showNotification(response.error || 'Delete failed.', 'danger');
                return;
            }

            showNotification('Device deleted successfully.', 'success');
            const modalElement = document.getElementById('confirmDeleteModal');
            const modal = modalElement ? bootstrap.Modal.getOrCreateInstance(modalElement) : null;
            if (modal) {
                modal.hide();
            }
            setTimeout(() => window.location.reload(), 800);
        } catch (error) {
            showNotification(error.message || 'Delete failed.', 'danger');
        }
    }

    function populateEditForm(device) {
        const modalTitle = document.getElementById('modalTitle');
        if (modalTitle) {
            modalTitle.textContent = 'Edit Employee Device';
        }

        setInputValue('deviceName', device.device_name || '');
        setInputValue('employeeName', device.employee_name || '');
        setInputValue('macAddress', device.mac_address || '');
        setInputValue('ipAddress', device.ip_address || '');
        setInputValue('hostname', device.hostname || '');
        setInputValue('department', device.department || '');
        setInputValue('notes', device.notes || '');
        setInputValue('editMac', device.mac_address || '');

        const macField = document.getElementById('macAddress');
        if (macField) {
            macField.readOnly = true;
            macField.classList.add('bg-light');
        }

        const modalElement = document.getElementById('addDeviceModal');
        if (!modalElement) {
            return;
        }
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
    }

    function clearDeviceForm() {
        const form = document.getElementById('deviceForm');
        if (form) {
            form.reset();
        }

        const modalTitle = document.getElementById('modalTitle');
        if (modalTitle) {
            modalTitle.textContent = 'Add Employee Device';
        }

        setInputValue('editMac', '');

        const macField = document.getElementById('macAddress');
        if (macField) {
            macField.readOnly = false;
            macField.classList.remove('bg-light');
        }

        const saveButton = document.getElementById('saveDeviceBtn');
        if (saveButton) {
            saveButton.disabled = false;
            saveButton.innerHTML = 'Save Device';
        }
    }

    function applyDeviceFilters() {
        const searchTerm = (document.getElementById('deviceSearchInput')?.value || '').trim().toLowerCase();
        const statusFilter = document.getElementById('deviceStatusFilter')?.value || 'all';
        const rows = Array.from(document.querySelectorAll('#deviceList tr[data-device-row="true"]'));

        let visibleCount = 0;
        rows.forEach((row) => {
            const rowStatus = row.dataset.deviceStatus || 'offline';
            const rowSearch = row.dataset.searchIndex || '';

            const matchesSearch = !searchTerm || rowSearch.includes(searchTerm);
            const matchesStatus = statusFilter === 'all' || rowStatus === statusFilter;
            const shouldShow = matchesSearch && matchesStatus;

            const isCurrentlyHidden = row.classList.contains('d-none');
            const shouldBeHidden = !shouldShow;

            if (isCurrentlyHidden !== shouldBeHidden) {
                row.classList.toggle('d-none', shouldBeHidden);
            }

            if (shouldShow) {
                visibleCount += 1;
            }
        });

        const visibleCountElement = document.getElementById('deviceVisibleCount');
        if (visibleCountElement) {
            visibleCountElement.textContent = String(rows.length ? visibleCount : 0);
        }

        const filterEmptyState = document.getElementById('deviceFilterEmptyState');
        if (filterEmptyState) {
            const showFilteredEmptyState = rows.length > 0 && visibleCount === 0;
            filterEmptyState.classList.toggle('d-none', !showFilteredEmptyState);
        }
    }

    async function scanNetworkDevices(event) {
        const button = event.currentTarget;
        const originalLabel = button.innerHTML;
        setButtonLoading(button, '<i data-lucide="loader" class="fa-spin" style="width:1rem;height:1rem;vertical-align:middle;"></i> Scanning...');

        try {
            const response = await requestJson('/api/tracking/scan', {
                method: 'POST',
            });

            if (!response.success) {
                showNotification(response.error || 'Scan failed.', 'danger');
                return;
            }

            renderScanSummary(response);
            patchScanResults(response.devices_found || []);

            if (Array.isArray(response.updated_ips) && response.updated_ips.length > 0) {
                showNotification(`Updated IP addresses for ${response.updated_ips.length} device(s).`, 'success');
            }

            if (Array.isArray(response.auto_saved_devices) && response.auto_saved_devices.length > 0) {
                showNotification(`Auto-saved ${response.auto_saved_devices.length} new device(s) from scan.`, 'success');
            }
        } catch (error) {
            showNotification(error.message || 'Scan failed.', 'danger');
        } finally {
            resetButtonLoading(button, originalLabel);
        }
    }

    async function syncTrackedDeviceIps(event) {
        const button = event.currentTarget;
        const originalLabel = button.innerHTML;
        setButtonLoading(button, '<i data-lucide="loader" class="fa-spin" style="width:1rem;height:1rem;vertical-align:middle;"></i> Syncing...');

        try {
            const response = await requestJson('/api/tracking/sync-ips', {
                method: 'POST',
            });

            if (!response.success) {
                showNotification(response.error || 'Sync failed.', 'danger');
                return;
            }

            const updatedCount = Array.isArray(response.updated_devices) ? response.updated_devices.length : 0;
            const autoSavedCount = Array.isArray(response.auto_saved_devices) ? response.auto_saved_devices.length : 0;

            if (autoSavedCount > 0) {
                showNotification(`Auto-saved ${autoSavedCount} new device(s) during sync.`, 'success');
            }

            if (updatedCount > 0) {
                showNotification(`Updated IP addresses for ${updatedCount} device(s).`, 'success');
            }

            if (updatedCount === 0 && autoSavedCount === 0) {
                showNotification('All tracked devices are already up to date.', 'info');
                return;
            }

            setTimeout(() => window.location.reload(), 1400);
        } catch (error) {
            showNotification(error.message || 'Sync failed.', 'danger');
        } finally {
            resetButtonLoading(button, originalLabel);
        }
    }

    function renderScanSummary(response) {
        setElementText('scanTrackingActiveCount', response.tracking_active || 0);
        setElementText('scanPortOnlyCount', response.port_only || 0);
        setElementText('scanNewDevicesCount', response.new_devices || 0);

        const banner = document.getElementById('scanResultsBanner');
        if (banner) {
            const totalFound = response.total_found || 0;
            banner.textContent = `Found ${totalFound} device(s) with port 5002 open.`;
            banner.classList.toggle('d-none', totalFound === 0);
        }
    }

    function patchScanResults(devices) {
        const tableWrap = document.getElementById('scanResultsTableWrap');
        const emptyState = document.getElementById('scanResultsEmptyState');
        const body = document.getElementById('scanResultsBody');
        if (!tableWrap || !emptyState || !body) {
            return;
        }

        const nextKeys = new Set();

        devices.forEach((device) => {
            const rowKey = getScanRowKey(device);
            nextKeys.add(rowKey);

            let row = body.querySelector(`tr[data-row-key="${escapeSelectorValue(rowKey)}"]`);
            if (!row) {
                row = createScanRow(rowKey);
                body.appendChild(row);
            }
            updateScanRow(row, device);
        });

        body.querySelectorAll('tr[data-row-key]').forEach((row) => {
            if (!nextKeys.has(row.dataset.rowKey)) {
                row.remove();
            }
        });

        const hasRows = nextKeys.size > 0;
        tableWrap.hidden = !hasRows;
        emptyState.classList.toggle('d-none', hasRows);
        if (window.lucide && typeof lucide.createIcons === 'function') lucide.createIcons();
    }

    function createScanRow(rowKey) {
        const row = document.createElement('tr');
        row.dataset.rowKey = rowKey;

        row.innerHTML = [
            '<td class="scan-device-col"></td>',
            '<td class="scan-status-col"></td>',
            '<td class="scan-network-col"></td>',
            '<td class="scan-tracking-col"></td>',
            '<td class="scan-action-col text-end"></td>',
        ].join('');

        return row;
    }

    function updateScanRow(row, device) {
        const hostname = safeValue(device.hostname, 'Unknown');
        const system = safeValue(device.system, 'Unknown');
        const status = safeValue(device.status, 'unknown');
        const ip = safeValue(device.ip, 'N/A');
        const macAddress = safeValue(device.mac_address, 'N/A');
        const trackingText = device.tracking_data ? 'Active' : 'Inactive';

        let statusClass = 'tactical-badge tactical-badge-warning status-badge';
        let statusLabel = 'UNKNOWN';
        if (status === 'tracking_active') {
            statusClass = 'tactical-badge tactical-badge-healthy status-badge';
            statusLabel = 'TRACKING ACTIVE';
        } else if (status === 'port_open_no_service') {
            statusClass = 'tactical-badge tactical-badge-warning status-badge';
            statusLabel = 'PORT OPEN';
        }

        const isSaved = Boolean(device.is_saved);
        const actionHtml = isSaved
            ? '<button class="btn btn-outline-secondary border-secondary text-light btn-sm" type="button" disabled>Already Saved</button>'
            : `<button class="btn btn-outline-primary border-primary text-light btn-sm save-scanned-device" type="button" data-mac="${escapeHtml(macAddress)}" data-ip="${escapeHtml(ip)}" data-hostname="${escapeHtml(hostname)}"><i data-lucide="download" style="width: 14px; height: 14px; margin-right: 4px;"></i> Save</button>`;

        row.querySelector('.scan-device-col').innerHTML = `<strong>${escapeHtml(hostname)}</strong><div class="device-mac mt-1">${escapeHtml(system)}</div>`;
        row.querySelector('.scan-status-col').innerHTML = `<span class="${statusClass}">${statusLabel}</span>`;
        row.querySelector('.scan-network-col').innerHTML = `<strong style="color: var(--e-text-primary, #e6edf5); font-size: 0.73rem; font-family: var(--t-sans, 'IBM Plex Sans', sans-serif);">IP:</strong> <span class="text-success">${escapeHtml(ip)}</span><br><strong style="color: var(--e-text-primary, #e6edf5); font-size: 0.73rem; font-family: var(--t-sans, 'IBM Plex Sans', sans-serif);">MAC:</strong> ${escapeHtml(macAddress)}`;
        row.querySelector('.scan-tracking-col').innerHTML = `<strong style="color: var(--e-text-primary, #e6edf5); font-size: 0.73rem;">${trackingText}</strong>`;
        row.querySelector('.scan-action-col').innerHTML = actionHtml;
    }

    async function saveScannedDevice(device, button) {
        const payload = {
            device_name: device.hostname || 'Unknown Device',
            employee_name: '',
            mac_address: device.mac_address,
            ip_address: device.ip,
            hostname: device.hostname,
            department: '',
            notes: 'Added from network scan',
        };

        const originalLabel = button.innerHTML;
        setButtonLoading(button, '<i data-lucide="loader" class="fa-spin" style="width:1rem;height:1rem;vertical-align:middle;"></i> Saving...');

        try {
            const response = await requestJson('/api/tracking/save-device', {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            if (!response.success) {
                showNotification(response.error || 'Save failed.', 'danger');
                return;
            }

            showNotification('Scanned device saved successfully.', 'success');
            setTimeout(() => window.location.reload(), 900);
        } catch (error) {
            showNotification(error.message || 'Save failed.', 'danger');
        } finally {
            resetButtonLoading(button, originalLabel);
        }
    }

    async function exportDevicesList() {
        try {
            const response = await requestJson('/api/tracking/live-summary');
            if (!response.success) {
                showNotification(response.error || 'Failed to load device list for export.', 'danger');
                return;
            }

            const devices = Array.isArray(response.devices) ? response.devices : [];
            const csvLines = ['Device Name,Employee Name,Status,Last Seen'];

            devices.forEach((device) => {
                const lastSeen = device.timestamp ? new Date(device.timestamp).toLocaleDateString() : 'Never';
                csvLines.push([
                    csvEscape(device.device_name || ''),
                    csvEscape(device.employee_name || ''),
                    csvEscape(device.status || 'offline'),
                    csvEscape(lastSeen),
                ].join(','));
            });

            const blob = new Blob([csvLines.join('\n')], { type: 'text/csv' });
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `tracked-devices-${new Date().toISOString().slice(0, 10)}.csv`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);

            showNotification('Device list exported successfully.', 'success');
        } catch (error) {
            showNotification(error.message || 'Export failed.', 'danger');
        }
    }

    async function requestJson(url, options) {
        const requestOptions = {
            credentials: 'same-origin',
            ...options,
            headers: {
                ...(options && options.body ? { 'Content-Type': 'application/json' } : {}),
                ...(options && options.headers ? options.headers : {}),
            },
        };

        const response = await fetch(url, requestOptions);
        const contentType = (response.headers.get('content-type') || '').toLowerCase();
        let payload = null;

        if (contentType.includes('application/json')) {
            payload = await response.json().catch(() => null);
        } else {
            const rawText = await response.text();
            const snippet = (rawText || '').replace(/\s+/g, ' ').trim().slice(0, 220);
            throw createHttpError(
                response.status,
                `Expected JSON response but received non-JSON (${response.status} ${response.statusText}): ${snippet}`
            );
        }

        if (!response.ok) {
            const message = extractErrorMessage(payload) || httpStatusMessage(response.status);
            throw createHttpError(response.status, message);
        }

        return payload || {};
    }

    function extractErrorMessage(payload) {
        if (!payload) {
            return '';
        }
        if (typeof payload.error === 'string') {
            return payload.error;
        }
        if (payload.error && typeof payload.error.message === 'string') {
            return payload.error.message;
        }
        if (typeof payload.message === 'string') {
            return payload.message;
        }
        return '';
    }

    function httpStatusMessage(status) {
        if (status === 400) {
            return 'Request validation failed. Check input and try again.';
        }
        if (status === 401) {
            return 'Session expired or unauthorized. Please sign in again.';
        }
        if (status === 404) {
            return 'Requested device/resource was not found (it may have been deleted).';
        }
        if (status === 409) {
            return 'Conflict detected while saving. Refresh and retry.';
        }
        if (status >= 500) {
            return 'Server error while processing the request. Please retry.';
        }
        return `Request failed with status ${status}.`;
    }

    function createHttpError(status, message) {
        const error = new Error(message);
        error.status = status;
        return error;
    }

    function showNotification(message, type) {
        const host = document.getElementById('trackingNotificationHost') || document.body;
        const alertType = type || 'info';

        const notification = document.createElement('div');
        notification.className = `alert alert-${alertType} alert-dismissible fade show tracking-toast`;
        notification.setAttribute('role', 'alert');

        const messageText = document.createElement('span');
        messageText.textContent = message;
        notification.appendChild(messageText);

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'btn-close';
        closeButton.setAttribute('data-bs-dismiss', 'alert');
        closeButton.setAttribute('aria-label', 'Close');
        notification.appendChild(closeButton);

        host.appendChild(notification);

        window.setTimeout(() => {
            if (notification.parentNode) {
                notification.remove();
            }
        }, 5000);
    }

    function setButtonLoading(button, loadingHtml) {
        if (!button) {
            return;
        }
        button.disabled = true;
        button.innerHTML = loadingHtml;
    }

    function resetButtonLoading(button, originalHtml) {
        if (!button) {
            return;
        }
        button.disabled = false;
        button.innerHTML = originalHtml;
    }

    function setInputValue(id, value) {
        const input = document.getElementById(id);
        if (input) {
            input.value = value;
        }
    }

    function setElementText(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = String(value);
        }
    }

    function getScanRowKey(device) {
        if (device && device.mac_address && device.mac_address !== 'N/A') {
            return `mac:${device.mac_address}`;
        }
        return `ip:${device && device.ip ? device.ip : 'unknown'}`;
    }

    function safeValue(value, fallback) {
        if (value === undefined || value === null || String(value).trim() === '') {
            return fallback;
        }
        return String(value);
    }

    function escapeSelectorValue(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(value);
        }
        return String(value).replace(/"/g, '\\"');
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function csvEscape(value) {
        const normalized = String(value).replace(/"/g, '""');
        return `"${normalized}"`;
    }
}());
