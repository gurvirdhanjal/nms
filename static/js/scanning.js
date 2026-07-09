// ============================================================================
// UTILITY: Shared UI compatibility layer
// ============================================================================
const scanningFlags = window.__UI_SURFACE_FLAGS__ || {};
const sharedLoadingApi = scanningFlags.sharedLoading !== false && window.UI?.Loading ? window.UI.Loading : null;

function showToast(message, type = 'info', timeout = 3000) {
    window.UI?.Toast?.show(String(message || ''), type, { durationMs: timeout, allowHtml: true });
}

function setButtonBusy(button, isBusy, config = {}) {
    if (!button) return;
    if (sharedLoadingApi) {
        sharedLoadingApi.setButtonBusy(button, {
            busy: isBusy,
            labelBusy: config.labelBusy || 'Working...',
            labelIdle: config.labelIdle || button.dataset.uiIdleLabel || button.innerHTML,
        });
        return;
    }

    if (!button.dataset.uiIdleLabel) {
        button.dataset.uiIdleLabel = config.labelIdle || button.innerHTML;
    }

    button.disabled = Boolean(isBusy);
    button.innerHTML = isBusy
        ? (config.labelBusy || 'Working...')
        : (config.labelIdle || button.dataset.uiIdleLabel || button.innerHTML);
}

function setRegionState(container, config = {}) {
    if (!container) return;
    if (sharedLoadingApi) {
        sharedLoadingApi.setRegionState(container, config);
        return;
    }
    container.innerHTML = `<div class="alert alert-secondary mb-0">${config.title || 'Loading...'}</div>`;
}

// ============================================================================
// UTILITY: Modal Cleanup (removes all backdrops and modal artifacts)
// ============================================================================
function cleanModalArtifacts() {
    // Remove ALL lingering backdrops
    document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
        backdrop.remove();
    });

    // Remove modal-open class from body
    document.body.classList.remove('modal-open');

    // Restore scrolling
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
}

// ============================================================================
// NETWORK SCANNING MAIN CODE
// ============================================================================
document.addEventListener('DOMContentLoaded', function () {
    // Prevent double init
    if (window.scanningInitialized) return;
    window.scanningInitialized = true;

    // ============================================================================
    // NETWORK SCANNING MAIN CODE
    // ============================================================================
    const getLocalRangeBtn = document.getElementById('getLocalRange');
    const startScanBtn = document.getElementById('startScan');
    const ipRangeInput = document.getElementById('ipRange');
    const scanProgress = document.getElementById('scanProgress');
    const scanResults = document.getElementById('scanResults');
    const progressBar = document.querySelector('.progress-bar');
    const bulkActions = document.getElementById('bulkActions');
    const selectedCountSpan = document.getElementById('selectedCount');
    const bulkAddBtn = document.getElementById('bulkAddBtn');

    let currentScanId = null;
    let progressInterval = null;
    let isScanning = false;
    let totalDevicesFound = 0;
    let activePings = new Set(); // Track active ping requests
    let isAddingDevice = false;
    let isAutoAdd = false; // Flag for Scan & Add to DB functionality

    // Store all discovered devices: ip -> deviceObject
    let discoveredDevices = new Map();
    // Cache DOM rows: ip -> tr element
    let deviceRows = new Map();
    let selectedIPs = new Set();
    let lastUIUpdate = 0;

    // ========================================================================
    // Get Local IP Range
    // ========================================================================
    if (getLocalRangeBtn) {
        getLocalRangeBtn.addEventListener('click', function () {
            fetch('/api/get_local_ip_range')
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        throw new Error(data.error);
                    }
                    if (data.ip_range) {
                        ipRangeInput.value = data.ip_range;
                        showToast('Local IP range loaded successfully', 'success', 2000);
                    } else {
                        throw new Error('No IP range returned from server');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    showToast(`Error getting local IP range: ${error.message}`, 'danger', 3000);
                });
        });
    }


    // ========================================================================
    // Start Network Scan
    // ========================================================================
    if (startScanBtn) {
        startScanBtn.addEventListener('click', function () {
            if (isScanning) {
                stopCurrentScan();
                return;
            }

            isAutoAdd = false; // Normal manual scan

            const ipRange = ipRangeInput.value;
            // Allow empty IP range to trigger backend auto-detection
            if (!ipRange) {
                showToast('Auto-detecting network range...', 'info', 2000);
            }

            // Reset state
            discoveredDevices.clear();
            deviceRows.clear();
            selectedIPs.clear();
            updateBulkUI();

            startNetworkScan(ipRange);
        });
    }

    const scanAndAddBtn = document.getElementById('scanAndAddBtn');
    if (scanAndAddBtn) {
        scanAndAddBtn.addEventListener('click', function () {
            if (isScanning) {
                if (isAutoAdd) {
                    stopCurrentScan();
                } else {
                    showToast('A manual scan is already in progress.', 'warning', 3000);
                }
                return;
            }

            isAutoAdd = true; // Enable automatic DB addition

            const ipRange = ipRangeInput.value;
            // Allow empty IP range to trigger backend auto-detection
            if (!ipRange) {
                showToast('Auto-detecting network range...', 'info', 2000);
            }

            // Reset state
            discoveredDevices.clear();
            deviceRows.clear();
            selectedIPs.clear();
            updateBulkUI();

            startNetworkScan(ipRange);
        });
    }

    if (bulkAddBtn) {
        bulkAddBtn.addEventListener('click', executeBulkAdd);
    }

    // Event Delegation for Select All (Robust for dynamic content)
    if (scanResults) {
        scanResults.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'selectAllCheckbox') {
                toggleSelectAll(e);
            }
        });
    }

    function startNetworkScan(ipRange) {
        totalDevicesFound = 0;

        scanProgress.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = 'Starting scan...';
        progressBar.classList.remove('progress-bar-animated');

        if (isAutoAdd && scanAndAddBtn) {
            scanAndAddBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Scan & Add';
            scanAndAddBtn.classList.remove('btn-success');
            scanAndAddBtn.classList.add('btn-danger');
            startScanBtn.disabled = true;
        } else {
            startScanBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Scan';
            startScanBtn.classList.remove('btn-primary');
            startScanBtn.classList.add('btn-danger');
            if (scanAndAddBtn) scanAndAddBtn.disabled = true;
        }
        isScanning = true;

        scanResults.innerHTML = `
            <div class="ui-region-state">
                <div class="ui-region-state-title">Initializing network scan</div>
                <div class="ui-region-state-detail">Preparing address sweep and device enrichment.</div>
                <div class="progress mt-2" style="height: 10px;">
                    <div class="progress-bar bg-primary" id="detailedProgress" style="width: 0%"></div>
                </div>
                <div class="ui-refresh-meta" id="progressText">Preparing to scan...</div>
            </div>
        `;

        const scanMode = 'heavy';
        applyScanModeUI();

        fetch('/api/scan_network', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                ip_range: ipRange,
                scan_mode: 'heavy'
            })
        })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }

                currentScanId = data.scan_id;
                startProgressPolling();
            })
            .catch(error => {
                console.error('Error:', error);
                scanResults.innerHTML = `<div class="alert alert-danger"><i class="fas fa-exclamation-circle"></i> Scan failed to start: ${error.message}</div>`;
                resetScanButton();
                showToast('Failed to start scan', 'danger', 3000);
            });
    }

    function startProgressPolling() {
        progressInterval = setInterval(() => {
            checkScanProgress();
        }, 1500);
    }

    function checkScanProgress() {
        if (!currentScanId) return;

        fetch(`/api/scan_progress/${currentScanId}`)
            .then(response => response.json())
            .then(data => {
                updateScanProgress(data);

                if (data.status === 'completed' || data.status === 'error' || data.status === 'stopped') {
                    // Prevent double-firing
                    if (!isScanning) return;

                    stopProgressPolling();
                    if (data.status === 'error') {
                        showError(data.error);
                    } else if (data.status === 'completed') {
                        showCompletionMessage(data);

                        if (isAutoAdd) {
                            console.log("Auto-add enabled. Adding all discovered devices...");
                            if (discoveredDevices.size === 0) {
                                showToast("No devices discovered to add.", "warning");
                            } else {
                                // Add all discovered to selected pool
                                selectedIPs.clear();
                                for (const ip of discoveredDevices.keys()) {
                                    selectedIPs.add(ip);
                                }
                                // Execute DB injection
                                executeBulkAdd();
                            }
                            isAutoAdd = false; // reset after use
                        }
                    }
                    resetScanButton();
                }
            })
            .catch(error => {
                console.error('Error checking progress:', error);
            });
    }

    function updateScanProgress(data) {
        // Always process new devices immediately (data integrity)
        if (data.new_devices && data.new_devices.length > 0) {
            // Add new devices to store
            data.new_devices.forEach(d => {
                if (!discoveredDevices.has(d.ip)) {
                    discoveredDevices.set(d.ip, d);
                }
            });
            displayNewDevicesBatch(data.new_devices, data.total_found);
        }

        // Throttled UI updates (progress bar, text) to avoid layout thrashing
        const now = Date.now();
        if (now - lastUIUpdate < 800) return;
        lastUIUpdate = now;

        const progressText = document.getElementById('progressText');
        const detailedProgress = document.getElementById('detailedProgress');

        progressBar.style.width = data.progress + '%';
        progressBar.textContent = `${Math.round(data.progress)}%`;

        if (detailedProgress) {
            detailedProgress.style.width = data.progress + '%';
        }

        if (progressText) {
            let statusText = `Scanning... ${data.scanned_hosts || 0}/${data.total_hosts || 0} hosts`;
            if (data.total_found > 0) {
                statusText += ` - Found ${data.total_found} online devices`;
                totalDevicesFound = data.total_found;
            }
            progressText.textContent = statusText;
        }

        if (data.total_found > 0 && !document.querySelector('table')) {
            initializeResultsTable();
        }
    }

    function applyScanModeUI() {
        if (!bulkActions || !bulkAddBtn) return;
        bulkAddBtn.disabled = false;
    }


    function initializeResultsTable() {
        const checkboxHeader = `
                            <th class="select-column" style="width: 40px;">
                                <div class="form-check">
                                    <input class="form-check-input" type="checkbox" id="selectAllCheckbox">
                                </div>
                            </th>`;

        scanResults.innerHTML = `
            <div class="scan-status-bar">
                <div class="scan-spinner"></div>
                <span>Scan in progress — <strong><span id="liveCount">0</span> devices</strong> found so far</span>
            </div>
            <div class="table-responsive border border-secondary rounded overflow-auto devices-table-shell" style="max-height: 50vh;">
                <table class="table table-hover table-sm devices-table align-middle">
                    <thead class="table-dark">
                        <tr>
                            ${checkboxHeader}
                            <th>IP Address</th>
                            <th>Hostname</th>
                            <th>MAC Address</th>
                            <th>Manufacturer</th>
                            <th>Type</th>
                            <th>Status</th>
                            <th>Latency</th>
                            <th class="text-center">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="devicesTableBody">
                    </tbody>
                </table>
            </div>
            <div class="mt-2" style="font-size: 0.75rem;">
                <strong>Total devices found: <span id="totalCount">0</span></strong>
                <div class="progress mt-1" style="height: 8px;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated bg-primary" 
                         id="detailedProgress" style="width: 0%"></div>
                </div>
            </div>
        `;

        // Event Delegation for Table Actions
        const tableBody = document.getElementById('devicesTableBody');
        // Attach to parent scanResults or existing container if tableBody is transient
        // But since we just created it inside scanResults, we can attach to scanResults (delegation root)

        // Ensure we don't duplicate listeners on scanResults (it's persistent)
        // Actually, scanResults is cleared on start.
        // Let's attach to scanResults, as it contains the table.
        scanResults.removeEventListener('click', handleTableClick);
        scanResults.addEventListener('click', handleTableClick);

        // Render lucide icons for injected HTML
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }

    function handleTableClick(e) {
        // Minimal action icons
        const addIcon = e.target.closest('.action-add');
        if (addIcon) {
            e.stopPropagation();
            addDeviceToInventory(addIcon.dataset.ip);
            return;
        }

        const scanIcon = e.target.closest('.action-scan');
        if (scanIcon) {
            e.stopPropagation();
            scanPorts(scanIcon.dataset.ip);
            return;
        }
    }

    function toggleSelectAll(e) {
        const isChecked = e.target.checked;
        const checkboxes = document.querySelectorAll('.device-checkbox');

        checkboxes.forEach(cb => {
            if (cb.disabled) return;
            cb.checked = isChecked;
            const ip = cb.closest('tr').getAttribute('data-ip-row');
            if (isChecked) {
                selectedIPs.add(ip);
            } else {
                selectedIPs.delete(ip);
            }
        });

        updateBulkUI();
    }

    function findDeviceRow(ip) {
        // Check cache first
        if (deviceRows.has(ip)) {
            return deviceRows.get(ip);
        }
        // Fallback to DOM query
        return document.querySelector(`tr[data-ip-row="${ip}"]`);
    }

    function createDeviceRow(device) {
        const tr = document.createElement('tr');
        tr.setAttribute('data-ip-row', device.ip);

        // Status badge logic
        let statusBadge = '<span class="badge bg-secondary border border-secondary text-secondary">Unknown</span>';
        if (device.status === 'Online') {
            statusBadge = '<span class="badge bg-dark border border-success text-success">Online</span>';
        } else if (device.status === 'Offline') {
            statusBadge = '<span class="badge bg-dark border border-danger text-danger">Offline</span>';
        }

        // Latency
        const latency = device.latency ? `${Math.round(device.latency)} ms` : '-';

        // Device type label
        const rawType = (device.device_type || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || '-';

        // File server badge
        const fsTag = device.file_server
            ? '<span class="badge ms-1" style="font-size:0.6rem;background:rgba(99,102,241,0.18);border:1px solid rgba(99,102,241,0.35);color:#a5b4fc;">File Server</span>'
            : '';

        const actionIcons = `
            <div class="d-flex justify-content-center gap-3 device-actions">
                <span class="action-icon action-add" title="Add to Inventory" data-ip="${device.ip}" style="cursor: pointer; color: var(--e-text-secondary);">
                    <i class="fas fa-plus"></i>
                </span>
                <span class="action-icon action-scan" title="Port Scan" data-ip="${device.ip}" style="cursor: pointer; color: var(--e-text-secondary);">
                    <i class="fas fa-search"></i>
                </span>
            </div>
            `;

        const checkboxCell = `
            <td class="select-column">
                <div class="form-check">
                    <input class="form-check-input device-checkbox" type="checkbox" value="${device.ip}">
                </div>
            </td>`;

        tr.innerHTML = `
            ${checkboxCell}
            <td class="device-name-cell"><strong>${device.ip}</strong></td>
            <td>${device.hostname || 'Unknown'}${fsTag}</td>
            <td><div class="device-mac">${device.mac || 'N/A'}</div></td>
            <td>${device.manufacturer || 'Unknown'}</td>
            <td style="font-size:0.72rem;color:#94a3b8;">${rawType}</td>
            <td>${statusBadge}</td>
            <td>${latency}</td>
            <td class="text-center">
                ${actionIcons}
            </td>
        `;

        return tr;
    }

    function displayNewDevicesBatch(devices, totalFound) {
        let tableBody = document.getElementById('devicesTableBody');
        let totalCount = document.getElementById('totalCount');
        let liveCount = document.getElementById('liveCount');

        if (!tableBody) {
            initializeResultsTable();
            tableBody = document.getElementById('devicesTableBody');
            totalCount = document.getElementById('totalCount');
            liveCount = document.getElementById('liveCount');
        }

        const fragment = document.createDocumentFragment();
        let addedCount = 0;

        devices.forEach(device => {
            if (!findDeviceRow(device.ip)) {
                const row = createDeviceRow(device);
                fragment.appendChild(row);
                // Cache the row
                deviceRows.set(device.ip, row);
                addedCount++;
            }
        });

        if (addedCount > 0) {
            tableBody.appendChild(fragment);

            // Animate only the new rows (optional, but keep it performant)
            // We can skip specific row animation loop for massive performance
        }

        if (totalCount) totalCount.textContent = totalFound;
        if (liveCount) liveCount.textContent = totalFound;

        // attachEventListeners(); -> REMOVED (Replaced by Delegation)

        // Show bulk actions if we have results
        if (totalFound > 0) {
            bulkActions.style.display = 'flex';
        }

        if (devices.length >= 5) {
            showBatchNotification(devices.length, totalFound);
        }
    }

    let lastBatchToastAt = 0;
    function showBatchNotification(batchSize, totalFound) {
        const now = Date.now();
        if (now - lastBatchToastAt < 2000) return; // throttle to avoid flood
        lastBatchToastAt = now;

        showToast(
            `+${batchSize} new devices — ${totalFound} total found`,
            'success',
            2200
        );
    }

    function updateBulkUI() {
        if (selectedCountSpan) {
            selectedCountSpan.textContent = selectedIPs.size;
        }
        if (bulkAddBtn) {
            bulkAddBtn.disabled = selectedIPs.size === 0;
            if (selectedIPs.size > 0) {
                bulkAddBtn.textContent = `Add ${selectedIPs.size} Selected`;
            } else {
                bulkAddBtn.textContent = `Add to Inventory`;
            }
        }

        // Update select all checkbox state
        const selectAll = document.getElementById('selectAllCheckbox');
        if (selectAll) {
            const allCheckboxes = document.querySelectorAll('.device-checkbox');
            if (allCheckboxes.length > 0 && selectedIPs.size === allCheckboxes.length) {
                selectAll.indeterminate = false;
                selectAll.checked = true;
            } else if (selectedIPs.size > 0) {
                selectAll.indeterminate = true;
                selectAll.checked = false;
            } else {
                selectAll.indeterminate = false;
                selectAll.checked = false;
            }
        }
    }

    function stopCurrentScan() {
        if (currentScanId) {
            fetch(`/api/stop_scan/${currentScanId}`, {
                method: 'POST'
            })
                .then(response => response.json())
                .then(data => {
                    console.log('Scan stopped:', data);
                })
                .catch(error => {
                    console.error('Error stopping scan:', error);
                });
        }

        stopProgressPolling();
        resetScanButton();

        const existingResults = document.getElementById('devicesTableBody').innerHTML;
        if (existingResults) {
            // Keep the table if it exists
            const alertBox = scanResults.querySelector('.alert-info');
            if (alertBox) {
                alertBox.className = 'alert alert-warning py-2 mb-2';
                alertBox.innerHTML = `<i class="fas fa-hand-paper"></i> Scan stopped by user. Found ${totalDevicesFound} devices.`;
            }
        } else {
            scanResults.innerHTML = `<div class="alert alert-warning"><i class="fas fa-hand-paper"></i> Scan stopped by user. Found ${totalDevicesFound} devices.</div>`;
        }
    }

    function stopProgressPolling() {
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
        isScanning = false;
    }

    function resetScanButton() {
        startScanBtn.innerHTML = '<i class="fas fa-play"></i> Start Network Scan';
        startScanBtn.classList.remove('btn-danger');
        startScanBtn.classList.add('btn-primary');
        startScanBtn.disabled = false;

        if (scanAndAddBtn) {
            scanAndAddBtn.innerHTML = '<i class="fas fa-database"></i> Scan & Add to DB';
            scanAndAddBtn.classList.remove('btn-danger');
            scanAndAddBtn.classList.add('btn-success');
            scanAndAddBtn.disabled = false;
        }

        isScanning = false;

        progressBar.classList.remove('progress-bar-animated');
        scanProgress.style.display = 'none';

        // Show bulk actions if devices found
        if (totalDevicesFound > 0) {
            bulkActions.style.display = 'flex';
        }
    }

    function showCompletionMessage(data) {
        const scanDuration = data.scan_duration ? data.scan_duration.toFixed(2) : 'unknown';
        const alertBox = scanResults.querySelector('.alert-info');

        const msg = `
            <i class="fas fa-check-circle"></i> 
            Scan completed in ${scanDuration} seconds. Found ${data.total_found} devices.
        `;

        if (alertBox) {
            alertBox.className = 'alert alert-success py-2 mb-2';
            alertBox.innerHTML = msg;
        } else {
            // Just prepend if not found
            const div = document.createElement('div');
            div.className = 'alert alert-success py-2 mb-2';
            div.innerHTML = msg;
            scanResults.insertBefore(div, scanResults.firstChild);
        }

        showToast(`Scan complete! Found ${data.total_found} devices`, 'success', 3000);
    }

    function showError(message) {
        scanResults.innerHTML = `<div class="alert alert-danger"><i class="fas fa-exclamation-circle"></i> Scan error: ${message}</div>`;
        resetScanButton();
        showToast('Scan error: ' + message, 'danger', 4000);
    }

    function initializePage() {
        // 1. Auto-populate IP range on page load
        fetch('/api/get_local_ip_range')
            .then(response => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then(data => {
                if (data.ip_range && ipRangeInput) {
                    ipRangeInput.value = data.ip_range;
                    ipRangeInput.placeholder = `e.g., ${data.ip_range}`;
                }
            })
            .catch(error => console.warn('Error getting local IP range:', error));

        // 2. Check for ACTIVE SCAN (Persistence)
        fetch('/api/active_scan')
            .then(res => res.json())
            .then(data => {
                if (data.scan_id) {
                    console.log('Resuming active scan:', data.scan_id);
                    currentScanId = data.scan_id;

                    // Restore UI State
                    scanProgress.style.display = 'block';
                    startScanBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Scan';
                    startScanBtn.classList.remove('btn-primary');
                    startScanBtn.classList.add('btn-danger');
                    isScanning = true;

                    // Initialize table with existing results
                    initializeResultsTable();

                    if (data.devices && data.devices.length > 0) {
                        data.devices.forEach(d => {
                            if (!discoveredDevices.has(d.ip)) {
                                discoveredDevices.set(d.ip, d);
                            }
                        });
                        // Display all at once
                        displayNewDevicesBatch(data.devices, data.total_found || data.devices.length);
                    }

                    // Resume Polling
                    startProgressPolling();
                    showToast('Resumed active network scan', 'info', 3000);
                }
            })
            .catch(err => console.error('Error checking active scan:', err));
    }


    initializePage();

    // ========================================================================
    // BULK ACTIONS
    // ========================================================================

    function executeBulkAdd() {
        if (selectedIPs.size === 0) return;

        const devicesToAdd = [];
        selectedIPs.forEach(ip => {
            const device = discoveredDevices.get(ip);
            if (device) {
                devicesToAdd.push({
                    ip: device.ip,
                    hostname: device.hostname,
                    mac: device.mac,
                    manufacturer: device.manufacturer,
                    device_type: device.device_type || device.type,
                    confidence_score: device.confidence_score,
                    classification_confidence: device.classification_confidence,
                    classification_details: device.classification_details,
                    snmp_working: Boolean(device.snmp_working || device.snmp_ok),
                    snmp_community: device.snmp_community || device.community || '',
                    snmp_version: device.snmp_version || device.version || '',
                    snmp_port: device.snmp_port || 161
                });
            }
        });

        if (devicesToAdd.length === 0) return;

        setButtonBusy(bulkAddBtn, true, {
            labelBusy: 'Adding...',
            labelIdle: '<i class="fas fa-plus-circle"></i> Add Selected to Inventory'
        });

        fetch('/api/devices/bulk_add', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(devicesToAdd)
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const parts = [];
                    if (data.added > 0) parts.push(`${data.added} new`);
                    if (data.updated > 0) parts.push(`${data.updated} updated`);
                    if (data.skipped > 0) parts.push(`${data.skipped} already up to date`);
                    const summary = parts.length ? parts.join(', ') : 'no changes';
                    showToast(`Inventory: ${summary}.`, 'success', 5000);

                    // Updated visual state for added devices
                    devicesToAdd.forEach(d => {
                        const row = findDeviceRow(d.ip);
                        if (row) {
                            const checkbox = row.querySelector('.device-checkbox');
                            if (checkbox) {
                                checkbox.checked = false;
                                checkbox.disabled = true;
                            }
                            row.classList.remove('table-success'); // Remove if exists
                            row.classList.add('device-added');
                        }
                    });

                    selectedIPs.clear();
                    updateBulkUI();

                } else {
                    const errorMsg = data.message || data.error || 'Unknown error';
                    showToast(`Error: ${errorMsg}`, 'danger', 5000);
                }
            })
            .catch(err => {
                showToast(`Error adding devices: ${err.message}`, 'danger', 5000);
            })
            .finally(() => {
                updateBulkUI(); // Reset button state
            });
    }

    // ========================================================================
    // PORT SCAN MODAL (with proper cleanup)
    // ========================================================================
    let currentModalInstance = null;

    window.scanPorts = function (ipAddress) {
        const modalTitle = document.getElementById('portScanModalLabel');
        const modalBody = document.getElementById('portScanResults');
        const modalElement = document.getElementById('portScanModal');

        if (!modalElement) {
            showToast('Port scan modal not found', 'danger', 3000);
            return;
        }

        modalTitle.textContent = `Port Scan: ${ipAddress}`;
        setRegionState(modalBody, {
            state: 'loading',
            title: 'Scanning ports',
            detail: `Inspecting exposed services on ${ipAddress}.`,
            preserveHeight: 120
        });

        // Dispose existing modal instance
        if (currentModalInstance) {
            currentModalInstance.dispose();
            currentModalInstance = null;
        }

        // Clean up any lingering artifacts
        cleanModalArtifacts();

        // Create new modal instance
        currentModalInstance = new bootstrap.Modal(modalElement, {
            backdrop: 'static',
            keyboard: true
        });
        currentModalInstance.show();

        fetch('/api/scan_ports', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ip_address: ipAddress })
        })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    modalBody.innerHTML = `<div class="alert alert-danger">${data.error}</div>`;
                    return;
                }

                let portsHtml = '';
                if (data.open_ports && data.open_ports.length > 0) {
                    data.open_ports.forEach(port => {
                        let connectBtn = '';
                        if (port.port === 80) {
                            connectBtn = `<button class="btn btn-sm tactical-btn-outline" onclick="window.openHTTP('${data.ip_address}')"><i class="fas fa-globe me-1"></i> Open</button>`;
                        } else if (port.port === 443) {
                            connectBtn = `<button class="btn btn-sm tactical-btn-outline" onclick="window.openHTTPS('${data.ip_address}')"><i class="fas fa-lock me-1"></i> Open</button>`;
                        } else if (port.port === 3389) {
                            connectBtn = `<button class="btn btn-sm tactical-btn-outline" onclick="window.openRDP('${data.ip_address}')"><i class="fas fa-desktop me-1"></i> Connect</button>`;
                        } else if (port.port === 22) {
                            connectBtn = `<button class="btn btn-sm tactical-btn-outline" onclick="window.openSSH('${data.ip_address}')"><i class="fas fa-terminal me-1"></i> SSH</button>`;
                        } else {
                            connectBtn = `<button class="btn btn-sm tactical-btn-outline" onclick="window.openCustomPort('${data.ip_address}', [${port.port}])"><i class="fas fa-cog me-1"></i> Connect</button>`;
                        }

                        portsHtml += `
                        <tr>
                            <td><strong>${port.port}</strong></td>
                            <td>${port.service || 'Unknown'}</td>
                            <td>${port.status || 'Open'}</td>
                            <td>${connectBtn}</td>
                        </tr>
                    `;
                    });
                } else {
                    portsHtml = '<tr><td colspan="4" class="text-center text-muted">No open ports detected</td></tr>';
                }

                modalBody.innerHTML = `
                <div class="table-responsive">
                    <table class="tactical-table table-hover mb-0">
                        <thead>
                            <tr>
                                <th>Port</th>
                                <th>Service</th>
                                <th>State</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${portsHtml}
                        </tbody>
                    </table>
                </div>
            `;
            })
            .catch(error => {
                console.error('Error:', error);
                modalBody.innerHTML = `<div class="alert alert-danger">Error scanning ports: ${error.message}</div>`;
            });
    };

    // Modal cleanup event
    const modalElement = document.getElementById('portScanModal');
    if (modalElement) {
        modalElement.addEventListener('hidden.bs.modal', function () {
            if (currentModalInstance) {
                currentModalInstance.dispose();
                currentModalInstance = null;
            }
            cleanModalArtifacts();
        });
    }

    // ========================================================================
    // CONNECTION FUNCTIONS (openHTTP, openHTTPS, etc.)
    // ========================================================================
    window.openHTTP = function (ip) {
        const url = `http://${ip}`;
        window.open(url, '_blank');
    };

    window.openHTTPS = function (ip) {
        const url = `https://${ip}`;
        window.open(url, '_blank');
    };

    window.openRDP = function (ip) {
        const rdpUrl = `rdp://full%20address=s:${ip}`;
        window.location.href = rdpUrl;

        setTimeout(() => {
            const userChoice = confirm(`Opening Remote Desktop Connection to ${ip}\n\nIf Remote Desktop didn't open automatically, click OK to download an RDP file.`);
            if (userChoice) {
                downloadRDPFile(ip);
            }
        }, 1000);
    };

    function downloadRDPFile(ip) {
        const rdpContent = `screen mode id:i:2
use multimon:i:0
desktopwidth:i:1920
desktopheight:i:1080
session bpp:i:32
winposstr:s:0,3,0,0,800,600
compression:i:1
keyboardhook:i:2
audiocapturemode:i:0
videoplaybackmode:i:1
connection type:i:7
networkautodetect:i:1
bandwidthautodetect:i:1
displayconnectionbar:i:1
enableworkspacereconnect:i:0
disable wallpaper:i:0
allow font smoothing:i:0
allow desktop composition:i:0
disable full window drag:i:1
disable menu anims:i:1
disable themes:i:0
disable cursor setting:i:0
bitmapcachepersistenable:i:1
full address:s:${ip}
audiomode:i:0
redirectprinters:i:1
redirectcomports:i:0
redirectsmartcards:i:1
redirectclipboard:i:1
redirectposdevices:i:0
autoreconnection enabled:i:1
authentication level:i:2
prompt for credentials:i:1
negotiate security layer:i:1
remoteapplicationmode:i:0
alternate shell:s:
shell working directory:s:
gatewayhostname:s:
gatewayusagemethod:i:4
gatewaycredentialssource:i:4
gatewayprofileusagemethod:i:0
promptcredentialonce:i:0
gatewaybrokeringtype:i:0
use redirection server name:i:0
rdgiskdcproxy:i:0
kdcproxyname:s:`;

        const blob = new Blob([rdpContent], { type: 'application/x-rdp' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `RemoteDesktop_${ip}.rdp`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast(`RDP file downloaded: RemoteDesktop_${ip}.rdp<br>Double-click to connect.`, 'success', 4000);
    }

    window.openSSH = function (ip) {
        const sshCommand = `ssh user@${ip}`;

        if (navigator.clipboard) {
            navigator.clipboard.writeText(sshCommand).then(() => {
                showToast(`SSH command copied to clipboard:<br><code>${sshCommand}</code><br>Paste in terminal to connect.`, 'info', 4000);
            }).catch(() => {
                showToast(`SSH Command: ${sshCommand}<br>Use in your terminal.`, 'info', 4000);
            });
        } else {
            showToast(`SSH Command: ${sshCommand}<br>Use in your terminal.`, 'info', 4000);
        }
    };

    window.openCustomPort = function (ip, openPorts) {
        let portList = '';
        if (openPorts && openPorts.length > 0) {
            portList = `\n\nDetected open ports: ${openPorts.join(', ')}`;
        }

        const port = prompt(`Enter port number to connect to ${ip}:${portList}`, '');

        if (port && !isNaN(port) && port > 0 && port < 65536) {
            const protocol = confirm('Use HTTPS? (OK for HTTPS, Cancel for HTTP)') ? 'https' : 'http';
            const url = `${protocol}://${ip}:${port}`;
            window.open(url, '_blank');
            showToast(`Opening ${url}`, 'info', 2000);
        } else if (port) {
            showToast('Invalid port number', 'warning', 2000);
        }
    };

    // ========================================================================
    // PING DEVICE (with proper toast notifications)
    // ========================================================================
    window.pingDevice = function (ip, button) {
        // Prevent multiple simultaneous pings to same IP
        if (activePings.has(ip)) {
            showToast(`Already pinging ${ip}...`, 'warning', 2000);
            return;
        }

        const originalHTML = button.innerHTML;
        setButtonBusy(button, true, {
            labelBusy: 'Pinging...',
            labelIdle: originalHTML
        });
        activePings.add(ip);

        fetch('/api/ping_device', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ip_address: ip })
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    showToast(`✓ Ping to ${ip} successful!<br>Latency: ${data.latency} ms | TTL: ${data.ttl}`, 'success', 3000);
                } else {
                    showToast(`✗ Ping to ${ip} failed.<br>${data.message || 'No response'}`, 'warning', 3000);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showToast(`Error pinging ${ip}: ${error.message}`, 'danger', 3000);
            })
            .finally(() => {
                setButtonBusy(button, false, {
                    labelIdle: originalHTML
                });
                activePings.delete(ip);
            });
    };

    // ========================================================================
    // ADD DEVICE TO INVENTORY
    // ========================================================================
    window.addDeviceToInventory = function (ip, hostname, mac) {
        if (isAddingDevice) {
            showToast('Device operation already in progress...', 'warning', 2000);
            return;
        }

        isAddingDevice = true;
        addDevice(ip, hostname, mac);
    };

    function addDevice(ip, hostname, mac) {
        const device = discoveredDevices.get(ip) || {};
        const payload = {
            ip_address: ip,
            hostname: device.hostname || hostname || 'Unknown',
            mac_address: device.mac || mac || 'N/A',
            manufacturer: device.manufacturer,
            device_type: device.device_type || device.type,
            confidence_score: device.confidence_score,
            classification_confidence: device.classification_confidence,
            classification_details: device.classification_details,
            snmp_working: Boolean(device.snmp_working || device.snmp_ok),
            snmp_community: device.snmp_community || device.community || '',
            snmp_version: device.snmp_version || device.version || '',
            snmp_port: device.snmp_port || 161
        };

        console.log('Adding device:', payload);

        fetch('/api/add_to_inventory', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        })
            .then(response => response.json())
            .then(data => {
                console.log('Response:', data);
                if (data.success) {
                    showToast(`✓ Device ${ip} added successfully!<br>Refresh to see it in Device Management.`, 'success', 3500);

                    // Mark as added in UI
                    const row = findDeviceRow(ip);
                    if (row) {
                        row.classList.add('device-added');
                        // Disable checkbox
                        const checkbox = row.querySelector('.device-checkbox');
                        if (checkbox) {
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            selectedIPs.delete(ip);
                            updateBulkUI();
                        }
                    }

                } else {
                    showToast(`✗ Error: ${data.message || 'Failed to add device'}`, 'danger', 3500);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showToast(`Network error: ${error.message}`, 'danger', 3500);
            })
            .finally(() => {
                isAddingDevice = false;
            });
    }
});
