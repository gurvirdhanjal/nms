// ============================================================================
// UTILITY: Toast Notification System (replaces alert())
// ============================================================================
const MAX_TOASTS = 4;

function showToast(message, type = 'info', timeout = 3000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.position = 'fixed';
        container.style.top = '20px';
        container.style.right = '20px';
        container.style.zIndex = '1060';
        container.style.maxWidth = '400px';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';
        container.style.gap = '10px';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `alert alert-${type} alert-dismissible fade show`;
    toast.role = 'alert';
    toast.style.minWidth = '280px';
    toast.dataset.toastKey = `${type}|${message}`;
    toast.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    container.appendChild(toast);

    // Enforce max visible toasts
    while (container.children.length > MAX_TOASTS) {
        container.removeChild(container.firstChild);
    }

    // Auto-dismiss after timeout
    if (timeout > 0) {
        toast._dismissTimer = setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => {
                if (toast.parentNode) toast.remove();
            }, 150);
        }, timeout);
    }
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

    // Store all discovered devices: ip -> deviceObject
    let discoveredDevices = new Map();
    let selectedIPs = new Set();

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

            const ipRange = ipRangeInput.value;
            // Allow empty IP range to trigger backend auto-detection
            if (!ipRange) {
                showToast('Auto-detecting network range...', 'info', 2000);
            }

            // Reset state
            discoveredDevices.clear();
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
        progressBar.classList.add('progress-bar-animated');
        startScanBtn.innerHTML = '<i class="fas fa-stop"></i> Stop Scan';
        startScanBtn.classList.remove('btn-primary');
        startScanBtn.classList.add('btn-danger');
        isScanning = true;

        scanResults.innerHTML = `
            <div class="text-center">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-2">Initializing network scan...</p>
                <div class="progress mt-2" style="height: 10px;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated" 
                         id="detailedProgress" style="width: 0%"></div>
                </div>
                <small class="text-muted" id="progressText">Preparing to scan...</small>
            </div>
        `;

        fetch('/api/scan_network', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                ip_range: ipRange
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
                    stopProgressPolling();
                    if (data.status === 'error') {
                        showError(data.error);
                    } else if (data.status === 'completed') {
                        showCompletionMessage(data);
                    }
                    resetScanButton();
                }
            })
            .catch(error => {
                console.error('Error checking progress:', error);
            });
    }

    function updateScanProgress(data) {
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

        if (data.new_devices && data.new_devices.length > 0) {
            // Add new devices to store
            data.new_devices.forEach(d => {
                if (!discoveredDevices.has(d.ip)) {
                    discoveredDevices.set(d.ip, d);
                }
            });
            displayNewDevicesBatch(data.new_devices, data.total_found);
        }

        if (data.total_found > 0 && !document.querySelector('table')) {
            initializeResultsTable();
        }
    }

    function initializeResultsTable() {
        scanResults.innerHTML = `
            <div class="alert alert-info py-2 mb-2">
                <i class="fas fa-sync fa-spin"></i> 
                Network scan in progress... Found <span id="liveCount">0</span> devices so far...
            </div>
            <div class="table-responsive">
                <table class="table table-striped table-hover align-middle">
                    <thead class="table-dark">
                        <tr>
                            <th style="width: 40px;">
                                <div class="form-check">
                                    <input class="form-check-input" type="checkbox" id="selectAllCheckbox">
                                </div>
                            </th>
                            <th>IP Address</th>
                            <th>Hostname</th>
                            <th>MAC Address</th>
                            <th>Manufacturer</th>
                            <th>Status</th>
                            <th>Latency</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="devicesTableBody">
                    </tbody>
                </table>
            </div>
            <div class="mt-2">
                <strong>Total devices found: <span id="totalCount">0</span></strong>
                <div class="progress mt-1" style="height: 8px;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated" 
                         id="detailedProgress" style="width: 0%"></div>
                </div>
            </div>
        `;

        // Event listener handled by delegation
    }

    function toggleSelectAll(e) {
        const isChecked = e.target.checked;
        const checkboxes = document.querySelectorAll('.device-checkbox');

        checkboxes.forEach(cb => {
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

        devices.forEach(device => {
            const existingRow = findDeviceRow(device.ip);
            if (!existingRow) {
                const row = createDeviceRow(device);
                tableBody.appendChild(row);

                row.style.opacity = '0';
                setTimeout(() => {
                    row.style.transition = 'opacity 0.5s ease-in';
                    row.style.opacity = '1';
                }, 10);
            }
        });

        if (totalCount) totalCount.textContent = totalFound;
        if (liveCount) liveCount.textContent = totalFound;

        attachEventListeners();

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
            `<strong><i class="fas fa-bolt"></i> Batch Update!</strong><br>
             Found ${batchSize} new devices<br>
             <small>Total: ${totalFound} devices</small>`,
            'success',
            2500
        );
    }

    function findDeviceRow(ip) {
        return document.querySelector(`tr[data-ip-row="${ip}"]`);
    }

    function createDeviceRow(device) {
        const isAgent = device.is_agent || device.type === 'Tactical Agent';
        const statusClass = device.status === 'Online' ? 'success' : 'secondary';
        const statusIcon = device.status === 'Online' ? 'fa-wifi' : 'fa-times-circle';
        const latencyText = device.latency ? `${device.latency} ms` : 'N/A';

        const row = document.createElement('tr');
        row.className = 'device-row';
        if (isAgent) {
            row.classList.add('tactical-agent-row');
            row.style.boxShadow = "inset 4px 0 0 0 #00ffc8"; // Neon left border
            row.style.background = "rgba(0, 255, 200, 0.05)";
        }
        row.setAttribute('data-ip-row', device.ip);

        // Auto-check agents if they are new
        const autoCheck = isAgent ? 'checked' : '';
        if (isAgent && !selectedIPs.has(device.ip)) {
            // We can auto-select, but let's just make it visually distinct for now
            // to avoid accidental bulk adds. Or maybe we SHOULD auto-select?
            // User said "detect then i can go", implying manual action but easier.
            // We'll leave it unchecked by default but highly visible.
        }

        const agentBadge = isAgent ? '<span class="badge bg-info text-dark ms-2"><i class="fas fa-robot"></i> AGENT</span>' : '';

        row.innerHTML = `
            <td>
                <div class="form-check">
                    <input class="form-check-input device-checkbox" type="checkbox" value="${device.ip}" ${autoCheck}>
                </div>
            </td>
            <td>
                <code class="text-primary fw-bold" style="font-size: 0.95rem;">${device.ip}</code>
                ${agentBadge}
            </td>
            <td class="text-light">${device.hostname || 'Unknown'}</td>
            <td><small class="text-secondary font-monospace">${device.mac || 'N/A'}</small></td>
            <td class="text-light">${device.manufacturer || 'Unknown'}</td>
            <td>
                <span class="badge bg-${statusClass}">
                    <i class="fas ${statusIcon}"></i> ${device.status}
                </span>
            </td>
            <td class="text-light"><small>${latencyText}</small></td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <button class="btn btn-outline-primary add-to-inventory-btn" 
                            data-ip="${device.ip}" 
                            data-hostname="${device.hostname || 'Unknown'}" 
                            data-mac="${device.mac || 'N/A'}" 
                            title="Add to Inventory">
                        <i class="fas fa-plus"></i>
                    </button>
                    <button class="btn btn-outline-info scan-ports-btn" data-ip="${device.ip}" 
                            title="Scan Ports" ${device.status !== 'Online' ? 'disabled' : ''}>
                        <i class="fas fa-search"></i>
                    </button>
                    <button class="btn btn-outline-success ping-device-btn" data-ip="${device.ip}" 
                            title="Ping Device">
                        <i class="fas fa-network-wired"></i>
                    </button>
                </div>
            </td>
        `;

        // If auto-checked, add to selected set immediately
        if (autoCheck) {
            selectedIPs.add(device.ip);
            // Defer UI update slightly to allow DOM to settle
            setTimeout(updateBulkUI, 100);
        }

        return row;
    }

    function attachEventListeners() {
        document.querySelectorAll('.scan-ports-btn').forEach(button => {
            button.removeEventListener('click', scanPortsListener);
            button.addEventListener('click', scanPortsListener);
        });

        document.querySelectorAll('.ping-device-btn').forEach(button => {
            button.removeEventListener('click', pingDeviceListener);
            button.addEventListener('click', pingDeviceListener);
        });

        document.querySelectorAll('.add-to-inventory-btn').forEach(button => {
            button.removeEventListener('click', addToInventoryListener);
            button.addEventListener('click', addToInventoryListener);
        });

        document.querySelectorAll('.device-checkbox').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const ip = e.target.value;
                if (e.target.checked) selectedIPs.add(ip);
                else selectedIPs.delete(ip);
                updateBulkUI();
            });
        });
    }

    function updateBulkUI() {
        if (selectedCountSpan) {
            selectedCountSpan.textContent = selectedIPs.size;
        }
        if (bulkAddBtn) {
            bulkAddBtn.disabled = selectedIPs.size === 0;
            if (selectedIPs.size > 0) {
                bulkAddBtn.innerHTML = `<i class="fas fa-plus-circle"></i> Add ${selectedIPs.size} Selected Device(s)`;
            } else {
                bulkAddBtn.innerHTML = `<i class="fas fa-plus-circle"></i> Add Selected to Inventory`;
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

    function scanPortsListener(e) {
        // Find closest button in case click target is icon
        const btn = e.target.closest('button');
        if (!btn) return;
        const ip = btn.getAttribute('data-ip');
        scanPorts(ip);
    }

    function pingDeviceListener(e) {
        const btn = e.target.closest('button');
        if (!btn) return;
        const ip = btn.getAttribute('data-ip');
        pingDevice(ip, btn);
    }

    function addToInventoryListener(e) {
        const btn = e.target.closest('button');
        if (!btn) return;
        const ip = btn.getAttribute('data-ip');
        const hostname = btn.getAttribute('data-hostname');
        const mac = btn.getAttribute('data-mac');
        addDeviceToInventory(ip, hostname, mac);
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

    function buildConnectionButtons(device) {
        if (device.status !== 'Online') {
            return '<span class="text-muted"><small>Device Offline</small></span>';
        }

        let buttons = '<div class="connection-buttons-group" style="display: flex; gap: 5px; flex-wrap: wrap;">';
        const openPortNumbers = device.open_ports ? device.open_ports.map(p => p.port) : [];

        if (openPortNumbers.includes(80)) {
            buttons += `
                <button class="btn btn-sm btn-outline-primary" onclick="window.openHTTP('${device.ip}')" title="Open HTTP">
                    <i class="fas fa-globe"></i>
                </button>
            `;
        }

        if (openPortNumbers.includes(443)) {
            buttons += `
                <button class="btn btn-sm btn-outline-success" onclick="window.openHTTPS('${device.ip}')" title="Open HTTPS">
                    <i class="fas fa-lock"></i>
                </button>
            `;
        }

        if (openPortNumbers.includes(3389)) {
            buttons += `
                <button class="btn btn-sm btn-outline-warning" onclick="window.openRDP('${device.ip}')" title="Connect via RDP">
                    <i class="fas fa-desktop"></i>
                </button>
            `;
        }

        if (openPortNumbers.includes(22)) {
            buttons += `
                <button class="btn btn-sm btn-outline-info" onclick="window.openSSH('${device.ip}')" title="SSH Info">
                    <i class="fas fa-terminal"></i>
                </button>
            `;
        }

        buttons += `
            <button class="btn btn-sm btn-outline-secondary" onclick="window.openCustomPort('${device.ip}', ${JSON.stringify(openPortNumbers).replace(/"/g, '&quot;')})" title="Custom Port">
                <i class="fas fa-cog"></i>
            </button>
        `;

        buttons += '</div>';
        return buttons;
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
                    manufacturer: device.manufacturer
                });
            }
        });

        if (devicesToAdd.length === 0) return;

        bulkAddBtn.disabled = true;
        bulkAddBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';

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
                    showToast(`Added ${data.added} devices. Skipped ${data.skipped}.`, 'success', 5000);

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
                    showToast(`Error: ${data.message || 'Unknown error'}`, 'danger', 5000);
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
        modalBody.innerHTML = '<div class="text-center"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div><p class="mt-2">Scanning ports...</p></div>';

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
                            connectBtn = `<button class="btn btn-sm btn-primary" onclick="window.openHTTP('${data.ip_address}')"><i class="fas fa-globe"></i> Open</button>`;
                        } else if (port.port === 443) {
                            connectBtn = `<button class="btn btn-sm btn-success" onclick="window.openHTTPS('${data.ip_address}')"><i class="fas fa-lock"></i> Open</button>`;
                        } else if (port.port === 3389) {
                            connectBtn = `<button class="btn btn-sm btn-warning" onclick="window.openRDP('${data.ip_address}')"><i class="fas fa-desktop"></i> Connect</button>`;
                        } else if (port.port === 22) {
                            connectBtn = `<button class="btn btn-sm btn-info" onclick="window.openSSH('${data.ip_address}')"><i class="fas fa-terminal"></i> SSH</button>`;
                        } else {
                            connectBtn = `<button class="btn btn-sm btn-secondary" onclick="window.openCustomPort('${data.ip_address}', [${port.port}])"><i class="fas fa-cog"></i> Connect</button>`;
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
                    <table class="table table-striped table-sm">
                        <thead class="table-dark">
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
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
        button.disabled = true;
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
                button.innerHTML = originalHTML;
                button.disabled = false;
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
        const payload = {
            ip_address: ip,
            hostname: hostname || 'Unknown',
            mac_address: mac || 'N/A'
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
