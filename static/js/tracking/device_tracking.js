(function () {
    'use strict';

    let deviceToDelete = null;
    const STATUS_REFRESH_INTERVAL_MS = 15000;
    let kpiBaseline = null;
    let lastRefreshAtMs = null;
    let refreshTicker = null;
    let activeQuickFilter = 'all';
    let statusRefreshController = null;
    let deleteInFlight = false;
    let actionBannerTimer = null;
    let listSyncFrame = null;
    let inventoryDevicesCache = [];
    let inventoryDevicesLoaded = false;
    const PAGINATION = { page: 1, perPage: 50 };
    const surfaceFlags = window.__UI_SURFACE_FLAGS__ || {};
    const toastApi = surfaceFlags.sharedToast !== false && window.UI?.Toast?.show
        ? window.UI.Toast
        : null;
    const loadingApi = surfaceFlags.sharedLoading !== false && window.UI?.Loading
        ? window.UI.Loading
        : null;
    const refreshApi = surfaceFlags.sharedRefresh !== false && window.UI?.Refresh?.createController
        ? window.UI.Refresh
        : null;

    document.addEventListener('DOMContentLoaded', initTrackingDevicePage);

    function initTrackingDevicePage() {
        inventoryDevicesCache = Array.isArray(window.__TRACKING_INVENTORY_CANDIDATES__)
            ? window.__TRACKING_INVENTORY_CANDIDATES__
            : [];
        inventoryDevicesLoaded = inventoryDevicesCache.length > 0;
        bindGlobalActions();
        bindStoredDeviceActions();
        bindModalActions();
        bindFilterActions();
        bindScanActions();
        renderInventoryDeviceList();
        maybeOpenAddDeviceModalFromQuery();
        maybeRunGoLiveFromQuery();
        scheduleStoredListSync();
        startStoredStatusRefresh();
        prewarmInventoryDevices();
        runPremiumEntrance();
    }

    function bindGlobalActions() {
        document.querySelectorAll('.open-add-device').forEach((button) => {
            button.addEventListener('click', openAddDeviceModal);
        });

        const scanButton = document.getElementById('trackingScanBtn');
        if (scanButton) {
            scanButton.addEventListener('click', scanNetworkDevices);
        }

        const scanResultsRescanButton = document.getElementById('scanResultsRescanBtn');
        if (scanResultsRescanButton) {
            scanResultsRescanButton.addEventListener('click', scanNetworkDevices);
        }

        const syncButton = document.getElementById('syncBtn');
        if (syncButton) {
            syncButton.addEventListener('click', syncTrackedDeviceIps);
        }

        const goLiveButton = document.getElementById('goLiveSyncBtn');
        if (goLiveButton) {
            goLiveButton.addEventListener('click', runGoLiveSync);
        }

        const manualRefreshButton = document.getElementById('trackingManualRefreshBtn');
        if (manualRefreshButton) {
            manualRefreshButton.addEventListener('click', refreshStoredDeviceStatuses);
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

        // KPI cards → click activates matching chip filter
        document.querySelectorAll('[data-filter-target]').forEach((card) => {
            card.addEventListener('click', () => {
                const target = card.dataset.filterTarget;
                const chipBtn = document.querySelector(`[data-chip-filter="${target}"]`);
                if (chipBtn) chipBtn.click();
            });
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    card.click();
                }
            });
        });
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
                const rawDeviceId = deleteButton.getAttribute('data-device-id');
                const parsedDeviceId = Number.parseInt(rawDeviceId || '', 10);
                const deviceName = deleteButton.getAttribute('data-device-name') || 'Unknown Device';
                showDeleteConfirmation({
                    deviceId: Number.isInteger(parsedDeviceId) ? parsedDeviceId : null,
                    macAddress: macAddress || '',
                }, deviceName);
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

        const confirmDeleteModal = document.getElementById('confirmDeleteModal');
        if (confirmDeleteModal) {
            confirmDeleteModal.addEventListener('hidden.bs.modal', () => {
                deviceToDelete = null;
                deleteInFlight = false;
            });
        }

        const actionBannerCloseButton = document.getElementById('trackingDeviceActionBannerClose');
        if (actionBannerCloseButton) {
            actionBannerCloseButton.addEventListener('click', hideActionBanner);
        }

        document.addEventListener('hidden.bs.modal', () => {
            if (!document.querySelector('.modal.show')) {
                statusRefreshController?.flushDeferred();
            }
        });

        const refreshInventoryButton = document.getElementById('refreshInventoryDevicesBtn');
        if (refreshInventoryButton) {
            refreshInventoryButton.addEventListener('click', () => ensureInventoryDevicesLoaded({ force: true }));
        }

        const inventorySearch = document.getElementById('inventoryDeviceSearch');
        if (inventorySearch) {
            inventorySearch.addEventListener('input', renderInventoryDeviceList);
        }
    }

    function checkTabOverflow() {
        const tabsCol = document.querySelector('.tracking-filter-tabs-col');
        const tabsWrap = document.querySelector('.ops-filter-tabs');
        if (tabsCol && tabsWrap) {
            tabsCol.classList.toggle('has-overflow', tabsWrap.scrollWidth > tabsWrap.clientWidth);
        }
    }

    function bindFilterActions() {
        const searchInput = document.getElementById('deviceSearchInput');
        const searchClear = document.getElementById('deviceSearchClear');
        const statusFilter = document.getElementById('deviceStatusFilter');
        const chipButtons = document.querySelectorAll('[data-chip-filter]');

        if (searchInput) {
            searchInput.addEventListener('input', () => {
                if (searchClear) searchClear.classList.toggle('visible', searchInput.value.length > 0);
                PAGINATION.page = 1;
                applyDeviceFilters();
            });
        }

        if (searchClear && searchInput) {
            searchClear.addEventListener('click', () => {
                searchInput.value = '';
                searchClear.classList.remove('visible');
                searchInput.focus();
                PAGINATION.page = 1;
                applyDeviceFilters();
            });
        }

        checkTabOverflow();
        window.addEventListener('resize', checkTabOverflow);

        if (statusFilter) {
            statusFilter.addEventListener('change', () => {
                if (statusFilter.value !== 'all') {
                    activeQuickFilter = 'all';
                    document.querySelectorAll('[data-chip-filter]').forEach((button) => {
                        button.classList.toggle('active', button.getAttribute('data-chip-filter') === 'all');
                    });
                }
                PAGINATION.page = 1;
                applyDeviceFilters();
            });
        }

        chipButtons.forEach((chipButton) => {
            chipButton.addEventListener('click', () => {
                activeQuickFilter = chipButton.getAttribute('data-chip-filter') || 'all';
                chipButtons.forEach((button) => button.classList.toggle('active', button === chipButton));
                const statusFilterSelect = document.getElementById('deviceStatusFilter');
                if (statusFilterSelect) {
                    statusFilterSelect.value = 'all';
                }
                PAGINATION.page = 1;
                applyDeviceFilters();
            });
        });
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
        updateRefreshTicker();
        if (refreshApi) {
            statusRefreshController = refreshApi.createController({
                shouldDefer: () => Boolean(document.querySelector('.modal.show')),
                resumeStaleMs: STATUS_REFRESH_INTERVAL_MS + 5000,
                fetcher: () => requestJson('/api/tracking/live-summary'),
                applyData: (response) => {
                    if (!response.success || !Array.isArray(response.devices)) {
                        return;
                    }
                    applyStoredDeviceSummary(response);
                    lastRefreshAtMs = Date.now();
                    updateRefreshTicker();
                    _setRefreshSpin(false);
                },
                onStateChange: (state) => {
                    updateRefreshTicker();
                    _setRefreshSpin(state === 'loading');
                },
                onError: (error) => {
                    console.debug('Stored status refresh failed:', error?.message || error);
                    const statusLabel = document.getElementById('trackingRefreshStatus');
                    if (statusLabel) {
                        statusLabel.textContent = 'Refresh issue';
                    }
                    if (toastApi) {
                        toastApi.show('Unable to refresh stored device status.', 'warning', {
                            durationMs: 2600
                        });
                    }
                }
            });
        }
        refreshStoredDeviceStatuses({ reason: 'initial' });
        window.setInterval(() => refreshStoredDeviceStatuses({ reason: 'interval' }), STATUS_REFRESH_INTERVAL_MS);
        if (!refreshTicker) {
            refreshTicker = window.setInterval(updateRefreshTicker, 1000);
        }
    }

    function refreshStoredDeviceStatuses(options = {}) {
        if (statusRefreshController) {
            return statusRefreshController.refresh(options);
        }
        return requestJson('/api/tracking/live-summary')
            .then((response) => {
                if (!response.success || !Array.isArray(response.devices)) {
                    return null;
                }
                applyStoredDeviceSummary(response);
                lastRefreshAtMs = Date.now();
                updateRefreshTicker();
                return response;
            })
            .catch((error) => {
                console.debug('Stored status refresh failed:', error?.message || error);
                return null;
            });
    }

    function applyStoredDeviceSummary(response) {
        updateKpiCards(response);

        const rows = document.querySelectorAll('#deviceList tr[data-device-row="true"][data-mac]');
        if (!rows.length) {
            return;
        }

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

        scheduleStoredListSync();
    }

    function scheduleStoredListSync() {
        if (listSyncFrame) {
            return;
        }
        listSyncFrame = window.requestAnimationFrame(() => {
            listSyncFrame = null;
            applyDeviceFilters();
        });
    }

    function getStoredDeviceRows() {
        const body = document.getElementById('deviceList');
        if (!body) {
            return [];
        }
        return Array.from(body.querySelectorAll('tr[data-device-row="true"]'));
    }

    function updateKpiCards(summaryResponse) {
        const total = Number(summaryResponse.total_devices || 0);
        const reachable = Number(
            summaryResponse.reachable_devices !== undefined
                ? summaryResponse.reachable_devices
                : Number(summaryResponse.online_devices || 0) + Number(summaryResponse.degraded_devices || 0)
        );
        const offline = Number(
            summaryResponse.offline_devices !== undefined
                ? summaryResponse.offline_devices
                : Math.max(total - reachable, 0)
        );

        setElementText('trackingKpiTotal', total);
        setElementText('trackingKpiReachable', reachable);
        setElementText('trackingKpiOffline', offline);
        const activeAgentCheckins = Number(summaryResponse.active_agent_checkins || 0);
        setElementText('trackingKpiActive24h', activeAgentCheckins);

        const isFirstLoad = !kpiBaseline;
        if (!kpiBaseline) {
            kpiBaseline = { total, reachable, offline, activeAgentCheckins };
        }

        // Suppress trend indicators on first load — there's no comparison period yet
        if (!isFirstLoad) {
            updateKpiTrend('trackingKpiTotalTrend', total - Number(kpiBaseline.total || 0), '');
            updateKpiTrend('trackingKpiReachableTrend', reachable - Number(kpiBaseline.reachable || 0), '');
            updateKpiTrend('trackingKpiOfflineTrend', offline - Number(kpiBaseline.offline || 0), '');
            const syncWindow = Number(summaryResponse.agent_sync_window_seconds || 180);
            updateKpiTrend(
                'trackingKpiActive24hTrend',
                activeAgentCheckins - Number(kpiBaseline.activeAgentCheckins || 0),
                `in ${Math.round(syncWindow / 60)}m window`
            );
        }

        const offlineCard = document.getElementById('trackingKpiOffline')?.closest('.ops-kpi-card');
        if (offlineCard) {
            offlineCard.classList.toggle('critical', offline > 0);
        }
    }

    function updateKpiTrend(elementId, delta, suffix) {
        const trendElement = document.getElementById(elementId);
        if (!trendElement) {
            return;
        }

        trendElement.classList.remove('up', 'down', 'stable');
        const detail = suffix ? ` ${suffix}` : '';
        if (delta > 0) {
            trendElement.classList.add('up');
            trendElement.textContent = `↑ ${delta}${detail}`;
            return;
        }
        if (delta < 0) {
            trendElement.classList.add('down');
            trendElement.textContent = `↓ ${Math.abs(delta)}${detail}`;
            return;
        }
        trendElement.classList.add('stable');
        trendElement.textContent = suffix ? suffix : 'No comparison data';
    }

    function applyStoredStatusToRow(row, device) {
        const availabilityRaw = safeValue(device.availability_status || device.status, 'offline').toLowerCase();
        const availabilityStatus = availabilityRaw === 'online'
            ? 'online'
            : availabilityRaw === 'degraded'
                ? 'degraded'
                : 'offline';
        const identitySource = safeValue(device.identity_source, row.dataset.identitySource || 'legacy_confirmed');

        row.dataset.deviceStatus = availabilityStatus;
        row.dataset.identitySource = identitySource;
        const ipAddress = safeValue(device.ip_address, '').trim();
        const hostName = safeValue(device.hostname, '').trim();
        row.dataset.needsSync = (!ipAddress || availabilityStatus === 'offline') ? '1' : '0';

        const ipValue = row.querySelector('.tracking-ip-value');
        if (ipValue) {
            const nextIpText = ipAddress || 'N/A';
            if (ipValue.textContent !== nextIpText) {
                ipValue.textContent = nextIpText;
            }
        }

        const hostValue = row.querySelector('.tracking-host-value');
        if (hostValue) {
            const nextHostText = hostName || 'N/A';
            if (hostValue.textContent !== nextHostText) {
                hostValue.textContent = nextHostText;
            }
        }

        const deviceNameText = safeValue(row.querySelector('.device-name-cell strong')?.textContent, '').trim().toLowerCase();
        const employeeText = safeValue(row.querySelector('.device-name-cell .device-mac')?.textContent, '').trim().toLowerCase();
        const scopeText = safeValue(row.querySelector('.tracking-scope-meta')?.textContent, '').trim().toLowerCase();
        const macText = safeValue(device.mac_address, row.getAttribute('data-mac') || '').trim().toLowerCase();
        row.dataset.searchIndex = `${deviceNameText} ${employeeText} ${hostName.toLowerCase()} ${ipAddress.toLowerCase()} ${macText} ${scopeText}`.trim();

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
        const syncHint = buildAgentSyncHint(device);
        const metaText = `${formatProbeTimestamp(device.last_probe_at)} | ${reason}${syncHint ? ` | ${syncHint}` : ''}`;
        if (statusMeta && statusMeta.textContent !== metaText) {
            statusMeta.textContent = metaText;
            statusMeta.title = reason;
        }

        applyIdentitySourceToRow(row, identitySource);
    }

    function getIdentitySourceLabel(source) {
        return source === 'scanner_inventory' ? 'Scanner Confirmed' : 'Manually Confirmed';
    }

    function getIdentityBadgeClass(source) {
        return source === 'scanner_inventory'
            ? 'tracking-identity-badge tracking-identity-badge-scanner'
            : 'tracking-identity-badge tracking-identity-badge-legacy';
    }

    function applyIdentitySourceToRow(row, source) {
        if (!row) {
            return;
        }
        const normalizedSource = safeValue(source, 'legacy_confirmed');
        row.dataset.identitySource = normalizedSource;

        const cell = row.querySelector('.device-name-cell');
        if (!cell) {
            return;
        }

        let meta = cell.querySelector('.tracking-identity-meta');
        if (!meta) {
            meta = document.createElement('div');
            meta.className = 'tracking-identity-meta';
            cell.appendChild(meta);
        }

        let badge = meta.querySelector('.tracking-identity-badge');
        if (!badge) {
            badge = document.createElement('span');
            meta.appendChild(badge);
        }

        badge.className = getIdentityBadgeClass(normalizedSource);
        badge.textContent = getIdentitySourceLabel(normalizedSource);
    }

    function maybeOpenAddDeviceModalFromQuery() {
        const params = new URLSearchParams(window.location.search);
        const shouldOpen = String(params.get('open_add_device') || '').trim().toLowerCase();
        if (!['1', 'true', 'yes'].includes(shouldOpen)) {
            return;
        }
        openAddDeviceModal();
        params.delete('open_add_device');
        const nextQuery = params.toString();
        const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}${window.location.hash || ''}`;
        window.history.replaceState({}, document.title, nextUrl);
    }

    function maybeRunGoLiveFromQuery() {
        const params = new URLSearchParams(window.location.search);
        const shouldSync = String(params.get('go_live') || '').trim().toLowerCase();
        if (!['1', 'true', 'yes'].includes(shouldSync)) {
            return;
        }

        params.delete('go_live');
        const nextQuery = params.toString();
        const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}${window.location.hash || ''}`;
        window.history.replaceState({}, document.title, nextUrl);
        window.setTimeout(() => {
            runGoLiveSync({ autoTriggered: true });
        }, 0);
    }

    function formatProbeTimestamp(rawTimestamp) {
        const parsed = parseUniversalDate(rawTimestamp);
        if (!parsed) {
            return 'Last probe: n/a';
        }
        return `Last probe: ${parsed.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}`;
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

    function buildAgentSyncHint(device) {
        if (!device) {
            return '';
        }
        if (device.agent_sync_recent) {
            const age = Number(device.agent_sync_age_seconds || 0);
            if (Number.isFinite(age)) {
                return `service.py check-in ${age}s ago`;
            }
            return 'service.py check-in active';
        }
        const lastSyncAt = safeValue(device.last_agent_sync_at, '');
        if (!lastSyncAt) {
            return 'no service.py check-in yet';
        }
        const parsed = parseUniversalDate(lastSyncAt);
        if (!parsed) {
            return 'service.py check-in stale';
        }
        return `last service.py check-in ${parsed.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}`;
    }

    function openAddDeviceModal() {
        clearDeviceForm();
        ensureInventoryDevicesLoaded();
        const modalElement = document.getElementById('addDeviceModal');
        if (!modalElement) {
            return;
        }
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
    }

    async function prewarmInventoryDevices(options = {}) {
        try {
            await requestJson(`/api/tracking/prewarm-eligible-inventory-devices${options.force ? '?force=1' : ''}`);
        } catch (error) {
            console.debug('Eligible inventory prewarm failed:', error?.message || error);
        }
    }

    async function ensureInventoryDevicesLoaded(options = {}) {
        if (inventoryDevicesLoaded && !options.force) {
            renderInventoryDeviceList();
            return inventoryDevicesCache;
        }

        const refreshButton = document.getElementById('refreshInventoryDevicesBtn');
        const originalLabel = refreshButton ? refreshButton.innerHTML : '';
        if (refreshButton) {
            setButtonLoading(refreshButton, 'Loading...');
        }

        try {
            const response = await requestJson('/api/tracking/eligible-inventory-devices');
            const devices = Array.isArray(response?.devices) ? response.devices : [];
            inventoryDevicesCache = devices
                .slice()
                .sort((left, right) => safeValue(left.device_name, '').localeCompare(safeValue(right.device_name, '')));
            inventoryDevicesLoaded = true;
            renderInventoryDeviceList();
            return inventoryDevicesCache;
        } catch (error) {
            showNotification(error.message || 'Failed to load devices from inventory.', 'danger');
            return [];
        } finally {
            if (refreshButton) {
                resetButtonLoading(refreshButton, originalLabel || 'Refresh List');
            }
        }
    }

    function renderInventoryDeviceList() {
        const list = document.getElementById('inventoryDeviceList');
        const emptyState = document.getElementById('inventoryDeviceEmptyState');
        if (!list) {
            return;
        }
        const query = safeValue(document.getElementById('inventoryDeviceSearch')?.value, '').trim().toLowerCase();
        const selectedId = safeValue(document.getElementById('inventoryDeviceId')?.value, '').trim();
        const filtered = inventoryDevicesCache.filter((device) => {
            if (!query) {
                return true;
            }
            const haystack = [
                safeValue(device.device_name, ''),
                safeValue(device.hostname, ''),
                safeValue(device.device_ip, ''),
                safeValue(device.macaddress, ''),
                safeValue(device.device_type, ''),
                safeValue(device.manufacturer, ''),
            ].join(' ').toLowerCase();
            return haystack.includes(query);
        });

        list.innerHTML = filtered.map((device) => {
            const deviceId = Number(device.device_id || 0);
            const isSelected = String(deviceId) === selectedId;
            const activityLabel = device.agent_recent ? 'service.py active' : 'agent configured';
            return `
                <button
                    type="button"
                    class="tracking-inventory-item${isSelected ? ' is-selected' : ''}"
                    data-inventory-device-id="${deviceId}"
                >
                    <span class="tracking-inventory-item-title">${escapeHtml(safeValue(device.device_name, `Device ${deviceId}`))}</span>
                    <span class="tracking-inventory-item-meta">${escapeHtml(safeValue(device.device_type, 'Unknown'))} | ${escapeHtml(activityLabel)}</span>
                    <span class="tracking-inventory-item-detail">${escapeHtml(safeValue(device.device_ip, 'No IP'))}</span>
                    <span class="tracking-inventory-item-detail">${escapeHtml(safeValue(device.macaddress, 'No MAC'))}</span>
                </button>
            `;
        }).join('');

        list.querySelectorAll('[data-inventory-device-id]').forEach((button) => {
            button.addEventListener('click', () => {
                const selectedIdFromButton = String(button.getAttribute('data-inventory-device-id') || '').trim();
                handleInventoryDeviceSelection(selectedIdFromButton);
            });
        });

        if (emptyState) {
            emptyState.classList.toggle('d-none', filtered.length > 0);
        }
    }

    function handleInventoryDeviceSelection(selectedId) {
        selectedId = String(selectedId || '').trim();
        const selectedDevice = inventoryDevicesCache.find((device) => String(device.device_id) === selectedId) || null;
        setInputValue('inventoryDeviceId', selectedDevice ? selectedId : '');
        setInventoryDeviceMeta(selectedDevice);
        renderInventoryDeviceList();
        if (!selectedDevice) {
            return;
        }

        setInputValue('deviceName', selectedDevice.device_name || '');
        setInputValue('macAddress', selectedDevice.macaddress || '');
        setInputValue('ipAddress', selectedDevice.device_ip || '');
        setInputValue('hostname', selectedDevice.hostname || '');
        setInputValue('department', '');
    }

    function setInventoryDeviceMeta(device) {
        const meta = document.getElementById('inventoryDeviceMeta');
        if (!meta) {
            return;
        }
        if (!device) {
            meta.textContent = '';
            meta.classList.add('d-none');
            return;
        }

        const parts = [
            safeValue(device.device_type, 'Unknown type'),
            safeValue(device.manufacturer, ''),
            safeValue(device.status, ''),
            safeValue(device.device_ip, ''),
            safeValue(device.macaddress, ''),
        ].filter(Boolean);
        meta.textContent = parts.join(' | ');
        meta.classList.remove('d-none');
    }

    function openScanResultsModal() {
        const modalElement = document.getElementById('scanResultsModal');
        if (!modalElement) {
            return null;
        }
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
        return modal;
    }

    function showDeleteConfirmation(deleteTarget, deviceName) {
        deviceToDelete = deleteTarget;
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
        const selectedInventoryId = (document.getElementById('inventoryDeviceId')?.value || '').trim();

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

        if (selectedInventoryId) {
            payload.inventory_device_id = Number.parseInt(selectedInventoryId, 10);
        }

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

        setButtonLoading(saveButton, 'Saving...');

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
            if (response.device) {
                upsertStoredDeviceRow(response.device);
            }
            const modalElement = document.getElementById('addDeviceModal');
            const modal = modalElement ? bootstrap.Modal.getOrCreateInstance(modalElement) : null;
            if (modal) {
                modal.hide();
            }
            inventoryDevicesLoaded = false;
            prewarmInventoryDevices({ force: true });
            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
        } catch (error) {
            showNotification(error.message || 'Save failed.', 'danger');
        } finally {
            resetButtonLoading(saveButton, originalLabel);
        }
    }

    async function deleteTrackedDevice(deleteTarget) {
        if (deleteInFlight) {
            return;
        }

        const payload = {};
        if (typeof deleteTarget === 'string') {
            payload.mac_address = deleteTarget;
        } else if (deleteTarget && typeof deleteTarget === 'object') {
            if (Number.isInteger(deleteTarget.deviceId) && deleteTarget.deviceId > 0) {
                payload.device_id = deleteTarget.deviceId;
            }
            if (deleteTarget.macAddress) {
                payload.mac_address = deleteTarget.macAddress;
            }
        }

        if (!payload.device_id && !payload.mac_address) {
            showNotification('Missing device identity for delete request.', 'warning');
            return;
        }

        deleteInFlight = true;
        const confirmDeleteButton = document.getElementById('confirmDeleteBtn');
        const originalDeleteLabel = confirmDeleteButton ? confirmDeleteButton.innerHTML : '';

        if (confirmDeleteButton) {
            setButtonLoading(confirmDeleteButton, 'Archiving...');
        }

        try {
            const response = await requestJson('/api/tracking/delete-device', {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            if (!response.success) {
                showNotification(response.error || 'Archive failed.', 'danger');
                return;
            }

            showActionBanner({
                title: response.already_deleted ? 'Already removed' : 'Device archived',
                message: response.message || 'Device archived successfully.',
                detail: response.already_deleted
                    ? 'The selected device was already removed from stored tracking inventory.'
                    : 'The selected device was removed from the active tracking list and its saved history was preserved.',
            });
            removeStoredDeviceRow(payload.device_id, payload.mac_address);
            const modalElement = document.getElementById('confirmDeleteModal');
            const modal = modalElement ? bootstrap.Modal.getOrCreateInstance(modalElement) : null;
            if (modal) {
                modal.hide();
            }
            deviceToDelete = null;
            inventoryDevicesLoaded = false;
            prewarmInventoryDevices({ force: true });
            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
        } catch (error) {
            showNotification(error.message || 'Archive failed.', 'danger');
        } finally {
            deleteInFlight = false;
            if (confirmDeleteButton) {
                resetButtonLoading(confirmDeleteButton, originalDeleteLabel || 'Archive Device');
            }
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
        setInputValue('inventoryDeviceId', '');
        setInventoryDeviceMeta(null);
        renderInventoryDeviceList();

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
        setInputValue('inventoryDeviceId', '');
        setInventoryDeviceMeta(null);
        const inventorySearch = document.getElementById('inventoryDeviceSearch');
        if (inventorySearch) {
            inventorySearch.value = '';
        }
        renderInventoryDeviceList();

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

    function removeStoredDeviceRow(deviceId, macAddress) {
        const body = document.getElementById('deviceList');
        if (!body) {
            return;
        }

        let row = null;
        if (Number.isInteger(deviceId) && deviceId > 0) {
            row = body.querySelector(`#device-row-${deviceId}`);
        }
        if (!row && macAddress) {
            row = body.querySelector(`tr[data-mac="${escapeSelectorValue(macAddress)}"]`);
        }
        if (row) {
            row.classList.add('is-removing');
            window.setTimeout(() => {
                if (row.parentNode) {
                    row.remove();
                }
                syncStoredDeviceEmptyState();
                scheduleStoredListSync();
            }, 170);
            return;
        }
        syncStoredDeviceEmptyState();
        scheduleStoredListSync();
    }

    function syncStoredDeviceEmptyState() {
        const body = document.getElementById('deviceList');
        if (!body) {
            return;
        }

        const existingRows = body.querySelectorAll('tr[data-device-row="true"]');
        let emptyRow = document.getElementById('deviceListEmptyRow');
        if (existingRows.length > 0) {
            if (emptyRow) {
                emptyRow.remove();
            }
            return;
        }

        if (emptyRow) {
            return;
        }

        emptyRow = document.createElement('tr');
        emptyRow.id = 'deviceListEmptyRow';
        emptyRow.innerHTML = `
            <td colspan="5" class="text-center py-4">
                <i data-lucide="laptop" class="text-muted mb-3 tracking-icon-lg"></i>
                <h5>No Devices Configured</h5>
                <p class="text-muted mb-2">Add your first employee endpoint, then use scan and sync to keep the list current.</p>
                <button class="tactical-btn-outline open-add-device" type="button">
                    <i data-lucide="plus"></i> Add Your First Device
                </button>
            </td>
        `;
        body.appendChild(emptyRow);
        body.querySelector('.open-add-device')?.addEventListener('click', openAddDeviceModal);
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    function upsertStoredDeviceRow(device) {
        const body = document.getElementById('deviceList');
        if (!body || !device) {
            return;
        }

        const normalizedMac = safeValue(device.mac_address, '').trim();
        const existingById = Number.isInteger(Number(device.id)) ? body.querySelector(`#device-row-${Number(device.id)}`) : null;
        const existingByMac = normalizedMac
            ? body.querySelector(`tr[data-device-row="true"][data-mac="${escapeSelectorValue(normalizedMac)}"]`)
            : null;
        const row = existingById || existingByMac || document.createElement('tr');
        const availability = 'offline';
        const identitySource = safeValue(device.identity_source, 'legacy_confirmed');

        row.id = `device-row-${Number(device.id || 0)}`;
        row.className = `ops-device-row status-${availability}`;
        row.dataset.deviceRow = 'true';
        row.dataset.deviceStatus = availability;
        row.dataset.mac = normalizedMac;
        row.dataset.identitySource = identitySource;
        row.dataset.unassigned = ((device.employee_name || '').trim() ? '0' : '1');
        row.dataset.needsSync = ((device.ip_address || '').trim() ? '0' : '1');
        row.dataset.searchIndex = `${safeValue(device.device_name, '')} ${safeValue(device.employee_name, '')} ${safeValue(device.hostname, '')} ${safeValue(device.ip_address, '')} ${normalizedMac} ${safeValue(device.site_id, '')} ${safeValue(device.department_id, '')}`.toLowerCase().trim();

        row.innerHTML = `
            <td class="ps-4 device-name-cell">
                <strong class="ops-hostname">${escapeHtml(device.device_name || device.hostname || 'Unknown Device')}</strong>
                <div class="ops-assignee device-mac">${escapeHtml(device.employee_name || 'Unassigned')}</div>
                <div class="tracking-identity-meta">
                    <span class="${getIdentityBadgeClass(identitySource)}">${escapeHtml(getIdentitySourceLabel(identitySource))}</span>
                    ${!device.last_agent_sync_at ? '<span class="mo-badge badge-never-seen ms-1">Never Seen</span>' : ''}
                </div>
            </td>
            <td class="tracking-status-cell">
                <span class="ops-status-badge status-badge ${availability}">${availability.toUpperCase()}</span>
                <div class="tracking-status-meta text-muted small">Awaiting next refresh</div>
            </td>
            <td class="tracking-network-cell">
                <div class="ops-network-line">IP <strong class="tracking-ip-value">${escapeHtml(device.ip_address || 'N/A')}</strong></div>
                <div class="ops-network-line">MAC <strong class="tracking-mac-value">${escapeHtml(normalizedMac || 'N/A')}</strong></div>
                <div class="ops-network-line tracking-scope-meta text-muted" style="font-size: 11px;">
                    <span class="tracking-host-value">${escapeHtml(device.hostname || '')}</span>
                </div>
            </td>
            <td>
                <div class="ops-time-main">${device.last_seen ? escapeHtml(String(device.last_seen)) : 'Never'}</div>
            </td>
            <td class="text-end pe-4">
                <div class="btn-group ops-actions justify-content-end align-items-center gap-2">
                    <a href="/tracking/devices/${Number(device.id || 0)}" class="ops-btn ops-btn-live text-decoration-none" title="Live Tracking">LIVE</a>
                    <button class="ops-btn ops-btn-icon edit-device" type="button" title="Edit Device">
                        <i data-lucide="edit" class="tracking-icon-sm"></i>
                    </button>
                    <button class="ops-btn ops-btn-icon delete-device tracking-delete-btn" data-device-id="${Number(device.id || 0)}" data-mac="${escapeHtml(normalizedMac)}" data-device-name="${escapeHtml(device.device_name || device.hostname || 'Unknown Device')}" type="button" title="Archive Device">
                        <i data-lucide="archive" class="tracking-icon-sm"></i>
                    </button>
                </div>
            </td>
        `;

        const editButton = row.querySelector('.edit-device');
        if (editButton) {
            editButton.setAttribute('data-device', JSON.stringify(device));
        }

        if (!existingById && !existingByMac) {
            const emptyRow = document.getElementById('deviceListEmptyRow');
            if (emptyRow) {
                emptyRow.remove();
            }
            row.classList.add('is-entering');
            body.prepend(row);
            window.requestAnimationFrame(() => {
                row.classList.remove('is-entering');
            });
        }

        scheduleStoredListSync();
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    function applyDeviceFilters(rows = getStoredDeviceRows()) {
        const searchTerm = (document.getElementById('deviceSearchInput')?.value || '').trim().toLowerCase();
        const statusFilter = document.getElementById('deviceStatusFilter')?.value || 'all';

        // Step 1: collect rows that pass all filters
        const matchedRows = [];
        rows.forEach((row) => {
            const rowStatus = row.dataset.deviceStatus || 'offline';
            const rowSearch = row.dataset.searchIndex || '';
            const isUnassigned = row.dataset.unassigned === '1';
            const needsSync = row.dataset.needsSync === '1';

            const matchesSearch = !searchTerm || rowSearch.includes(searchTerm);
            const matchesStatus = statusFilter === 'all' || rowStatus === statusFilter;
            const matchesChip = (
                activeQuickFilter === 'all' ||
                (activeQuickFilter === 'online' && rowStatus === 'online') ||
                (activeQuickFilter === 'offline' && rowStatus === 'offline') ||
                (activeQuickFilter === 'unassigned' && isUnassigned) ||
                (activeQuickFilter === 'needs_sync' && needsSync)
            );
            if (matchesSearch && matchesStatus && matchesChip) matchedRows.push(row);
        });

        // Step 2: apply pagination — show only current page slice, hide rest
        const total = matchedRows.length;
        const start = (PAGINATION.page - 1) * PAGINATION.perPage;
        const end = start + PAGINATION.perPage;
        rows.forEach((row) => row.classList.add('d-none'));
        matchedRows.forEach((row, i) => {
            if (i >= start && i < end) row.classList.remove('d-none');
        });

        const visibleCount = Math.min(end, total) - start;

        const visibleCountElement = document.getElementById('deviceVisibleCount');
        if (visibleCountElement) {
            visibleCountElement.textContent = String(rows.length ? total : 0);
        }

        const resultsCount = document.getElementById('trackingResultsCount');
        if (resultsCount) {
            resultsCount.textContent = total < rows.length ? `${total}/${rows.length}` : `${rows.length}`;
        }

        const filterEmptyState = document.getElementById('deviceFilterEmptyState');
        if (filterEmptyState) {
            filterEmptyState.classList.toggle('d-none', !(rows.length > 0 && total === 0));
        }

        _renderPaginationControls(total);
        updateTableHealthCounters(rows);
    }

    function _renderPaginationControls(total) {
        const container = document.getElementById('trackingPagination');
        if (!container) return;

        const totalPages = Math.ceil(total / PAGINATION.perPage);
        if (totalPages <= 1) {
            container.classList.add('d-none');
            return;
        }
        container.classList.remove('d-none');

        const p = PAGINATION.page;
        const start = (p - 1) * PAGINATION.perPage + 1;
        const end = Math.min(p * PAGINATION.perPage, total);

        // Build page number chips (max 5 shown)
        let pagesHtml = '';
        const maxChips = 5;
        let lo = Math.max(1, p - Math.floor(maxChips / 2));
        let hi = Math.min(totalPages, lo + maxChips - 1);
        if (hi - lo < maxChips - 1) lo = Math.max(1, hi - maxChips + 1);
        for (let i = lo; i <= hi; i++) {
            pagesHtml += `<button type="button" class="ops-btn ops-btn-ghost px-2 py-1${i === p ? ' ops-btn-primary' : ''}"
                style="min-width:32px;font-size:12px" data-page="${i}">${i}</button>`;
        }

        container.innerHTML = `
            <span class="text-muted" style="font-size:11px">${start}–${end} of ${total}</span>
            <div class="d-flex gap-1 align-items-center">
                <button type="button" class="ops-btn ops-btn-ghost px-2 py-1" style="font-size:12px"
                    data-page="${p - 1}" ${p <= 1 ? 'disabled' : ''}>← Prev</button>
                ${pagesHtml}
                <button type="button" class="ops-btn ops-btn-ghost px-2 py-1" style="font-size:12px"
                    data-page="${p + 1}" ${p >= totalPages ? 'disabled' : ''}>Next →</button>
            </div>`;

        container.querySelectorAll('button[data-page]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const newPage = parseInt(btn.dataset.page, 10);
                if (newPage >= 1 && newPage <= totalPages) {
                    PAGINATION.page = newPage;
                    applyDeviceFilters();
                    document.getElementById('deviceList')?.closest('.table-responsive')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });
    }

    function updateTableHealthCounters(rows = getStoredDeviceRows()) {
        if (!rows.length) {
            setElementText('managedCount', 0);
            setElementText('unassignedCount', 0);
            setElementText('needsAttentionCount', 0);
            return;
        }

        const managedCount = rows.length;
        const unassignedCount = rows.filter((row) => row.dataset.unassigned === '1').length;
        const needsAttention = rows.filter((row) => row.dataset.needsSync === '1').length;

        setElementText('managedCount', managedCount);
        setElementText('tabCountAll', managedCount);
        setElementText('unassignedCount', unassignedCount);
        setElementText('needsAttentionCount', needsAttention);

        const needsAttentionPill = document.getElementById('needsAttentionPill');
        if (needsAttentionPill) {
            needsAttentionPill.classList.toggle('attention', needsAttention > 0);
        }

        const unassignedPill = document.getElementById('unassignedCount')?.closest('.tracking-summary-pill');
        if (unassignedPill) {
            unassignedPill.classList.toggle('attention', unassignedCount > 0);
        }
    }

    function _setRefreshSpin(active) {
        const icon = document.getElementById('trackingRefreshIcon');
        if (icon) icon.classList.toggle('spinning', active);
    }

    function updateRefreshTicker() {
        const refreshStatus = document.getElementById('trackingRefreshStatus');
        const lastRefreshedText = document.getElementById('trackingLastRefreshedText');
        const statusDot = document.querySelector('.tracking-realtime-pill .status-dot');
        if (!refreshStatus || !lastRefreshedText) {
            return;
        }

        if (!lastRefreshAtMs) {
            refreshStatus.textContent = 'Auto-refresh every 15s';
            lastRefreshedText.textContent = 'Last refreshed: n/a';
            if (statusDot) {
                statusDot.classList.remove('healthy', 'offline');
                statusDot.classList.add('unknown');
            }
            return;
        }

        const ageSeconds = Math.max(0, Math.floor((Date.now() - lastRefreshAtMs) / 1000));
        refreshStatus.textContent = ageSeconds <= 20 ? 'Live polling active' : 'Refresh delayed';
        lastRefreshedText.textContent = `Last refreshed ${ageSeconds}s ago`;
        if (statusDot) {
            statusDot.classList.remove('healthy', 'offline', 'unknown');
            statusDot.classList.add(ageSeconds <= 20 ? 'healthy' : 'offline');
        }
    }

    async function scanNetworkDevices(event) {
        const button = event?.currentTarget || document.getElementById('trackingScanBtn');
        const originalLabel = button ? button.innerHTML : 'Scan Network';

        openScanResultsModal();
        showScanLoadingState();

        if (button) {
            setButtonLoading(button, 'Checking agents...');
        }

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
            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
        } catch (error) {
            showScanErrorState(error.message || 'Scan failed.');
            showNotification(error.message || 'Scan failed.', 'danger');
        } finally {
            if (button) {
                resetButtonLoading(button, originalLabel);
            }
        }
    }

    async function syncTrackedDeviceIps(event) {
        const button = event.currentTarget;
        const originalLabel = button.innerHTML;
        setButtonLoading(button, 'Syncing...');

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
                await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
                return;
            }

            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
        } catch (error) {
            showNotification(error.message || 'Sync failed.', 'danger');
        } finally {
            resetButtonLoading(button, originalLabel);
        }
    }

    async function runGoLiveSync(trigger = {}) {
        const button = trigger && trigger.currentTarget
            ? trigger.currentTarget
            : document.getElementById('goLiveSyncBtn');
        const originalLabel = button ? button.innerHTML : '';

        if (button) {
            setButtonLoading(button, 'Syncing live...');
        }

        try {
            const response = await requestJson('/api/tracking/live-sync', {
                method: 'POST',
            });

            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });

            const refreshedDevices = Number(response.refreshed_devices || 0);
            showActionBanner({
                title: 'Live sync complete',
                message: response.message || 'Live sync completed.',
                detail: refreshedDevices > 0
                    ? `Refreshed ${refreshedDevices} tracked device snapshot${refreshedDevices === 1 ? '' : 's'} and updated the live status view.`
                    : 'The live status view was refreshed using the latest available tracking snapshot cache.',
            });
        } catch (error) {
            showNotification(error.message || 'Live sync failed.', 'danger');
        } finally {
            if (button) {
                resetButtonLoading(button, originalLabel || 'Go Live');
            }
        }
    }

    function renderScanSummary(response) {
        const trackingActive = Number(response.tracking_active || 0);
        const portOnly = Number(response.port_only || 0);
        const readyToAdd = Number(response.new_devices || 0);
        const candidateHosts = Number(response.candidate_hosts || 0);
        const inventoryHosts = Number(response.inventory_hosts || 0);

        setElementText('scanTrackingActiveCount', trackingActive);
        setElementText('scanPortOnlyCount', portOnly);
        setElementText('scanNewDevicesCount', readyToAdd);
        setElementText('scanTrackingActiveMeta', trackingActive > 0 ? 'Last scan' : 'No active agents');
        setElementText('scanPortOnlyMeta', portOnly > 0 ? 'Port reachable only' : 'Agent verified');
        setElementText('scanNewDevicesMeta', readyToAdd > 0 ? 'Not yet monitored' : 'No new candidates');
        const newDeviceCard = document.getElementById('scanNewDevicesCount')?.closest('.ops-discovery-stat');
        if (newDeviceCard) {
            newDeviceCard.classList.toggle('warning', readyToAdd > 0);
        }

        const banner = document.getElementById('scanResultsBanner');
        const meta = document.getElementById('scanResultsMeta');
        if (banner) {
            if (candidateHosts === 0) {
                banner.textContent = 'No online inventory candidates are currently available to probe.';
            } else if (trackingActive > 0) {
                banner.textContent = trackingActive === 1
                    ? '1 active agent endpoint is ready for monitoring review.'
                    : `${trackingActive} active agent endpoints are ready for monitoring review.`;
            } else if (portOnly > 0) {
                banner.textContent = 'No agent-active endpoints responded, but the configured tracking service port is reachable on other hosts.';
            } else {
                banner.textContent = 'No active tracking agents were detected on this scan.';
            }
        }
        if (meta) {
            if (candidateHosts === 0) {
                meta.textContent = 'Tracking discovery now probes only known online inventory and already-tracked endpoints.';
            } else if (readyToAdd > 0) {
                meta.textContent = readyToAdd === 1
                    ? `Checked ${candidateHosts} candidate host${candidateHosts === 1 ? '' : 's'} from ${inventoryHosts} online inventory target${inventoryHosts === 1 ? '' : 's'}. 1 endpoint is not yet in monitored inventory.`
                    : `Checked ${candidateHosts} candidate hosts from ${inventoryHosts} online inventory targets. ${readyToAdd} endpoints are not yet in monitored inventory.`;
            } else if (trackingActive > 0) {
                meta.textContent = `Checked ${candidateHosts} candidate host${candidateHosts === 1 ? '' : 's'}. All detected agent-active endpoints are already present in monitored inventory.`;
            } else if (portOnly > 0) {
                meta.textContent = `${portOnly} host${portOnly === 1 ? '' : 's'} exposed a configured tracking service port but did not return full tracking identity after checking ${candidateHosts} known candidates.`;
            } else {
                meta.textContent = `No active agent responses were returned from ${candidateHosts} known candidates. Ensure service.py is running and reachable on one of the configured tracking agent ports, then scan again.`;
            }
        }
        if (candidateHosts === 0) {
            setScanEmptyState({
                title: 'No online inventory candidates',
                detail: 'The inventory does not currently show any online IPs to probe for the tracking agent.',
            });
        } else if (trackingActive === 0) {
            setScanEmptyState({
                title: portOnly > 0 ? 'No verified agent responses yet' : 'No active tracking agents found',
                detail: portOnly > 0
                    ? `${portOnly} host${portOnly === 1 ? '' : 's'} exposed a configured tracking agent port, but the agent did not return identity details.`
                    : 'Start service.py on the target endpoint, then run Scan Network again.',
            });
        }
    }

    function patchScanResults(devices) {
        const tableWrap = document.getElementById('scanResultsTableWrap');
        const emptyState = document.getElementById('scanResultsEmptyState');
        const body = document.getElementById('scanResultsBody');
        if (!tableWrap || !emptyState || !body) {
            return;
        }

        const agentReadyDevices = getAgentReadyScanDevices(devices);
        body.querySelectorAll('tr:not([data-row-key])').forEach((row) => row.remove());
        const nextKeys = new Set();

        agentReadyDevices.forEach((device) => {
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

    function showScanLoadingState() {
        const tableWrap = document.getElementById('scanResultsTableWrap');
        const emptyState = document.getElementById('scanResultsEmptyState');
        const body = document.getElementById('scanResultsBody');
        const banner = document.getElementById('scanResultsBanner');
        const meta = document.getElementById('scanResultsMeta');
        if (banner) {
            banner.textContent = 'Checking known online inventory for endpoints with an active tracking agent...';
        }
        if (meta) {
            meta.textContent = 'This probe is scoped to online inventory and already-tracked endpoints. The ICMP/classification engine is not changed.';
        }
        if (emptyState) {
            emptyState.classList.add('d-none');
        }
        if (tableWrap) {
            tableWrap.hidden = false;
        }
        if (body) {
            if (loadingApi?.setTableState) {
                loadingApi.setTableState(body, {
                    state: 'loading',
                    colspan: 5,
                    title: 'Checking known inventory candidates',
                    detail: 'Only devices with a live tracking agent response will appear in this list.',
                });
            } else {
                body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Checking known inventory candidates...</td></tr>';
            }
        }
    }

    function showScanErrorState(message) {
        const tableWrap = document.getElementById('scanResultsTableWrap');
        const emptyState = document.getElementById('scanResultsEmptyState');
        const body = document.getElementById('scanResultsBody');
        const banner = document.getElementById('scanResultsBanner');
        const meta = document.getElementById('scanResultsMeta');
        if (banner) {
            banner.textContent = 'Scan did not complete.';
        }
        if (meta) {
            meta.textContent = message || 'Unable to check the network for agent-active endpoints.';
        }
        if (emptyState) {
            emptyState.classList.add('d-none');
        }
        if (tableWrap) {
            tableWrap.hidden = false;
        }
        if (body) {
            if (loadingApi?.setTableState) {
                loadingApi.setTableState(body, {
                    state: 'error',
                    colspan: 5,
                    title: 'Unable to complete scan',
                    detail: message || 'Retry the scan in a few seconds.',
                });
            } else {
                body.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">${escapeHtml(message || 'Scan failed.')}</td></tr>`;
            }
        }
    }

    function setScanEmptyState(config = {}) {
        setElementText('scanResultsEmptyTitle', config.title || 'No active tracking agents found');
        setElementText('scanResultsEmptyDetail', config.detail || 'Run Scan Network again after the agent is started on the target endpoint.');
    }

    function getAgentReadyScanDevices(devices) {
        return (Array.isArray(devices) ? devices : []).filter((device) => {
            return safeValue(device?.status, '').toLowerCase() === 'tracking_active';
        });
    }

    function decrementScanReadyCount() {
        const current = Number(document.getElementById('scanNewDevicesCount')?.textContent || 0);
        if (!Number.isFinite(current) || current <= 0) {
            return;
        }
        const next = Math.max(0, current - 1);
        setElementText('scanNewDevicesCount', next);
        setElementText('scanNewDevicesMeta', next > 0 ? 'Not yet monitored' : 'All detected agents are already monitored');
        const newDeviceCard = document.getElementById('scanNewDevicesCount')?.closest('.ops-discovery-stat');
        if (newDeviceCard) {
            newDeviceCard.classList.toggle('warning', next > 0);
        }
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
        const macAddress = safeValue(device.authoritative_mac || device.mac_address, 'N/A');
        const reportedMac = safeValue(device.reported_agent_mac, '').trim();
        const trackingText = device.tracking_data ? 'Agent responding' : 'Identity verified';

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
            ? '<button class="btn btn-outline-secondary border-secondary text-light btn-sm" type="button" disabled>Already Monitored</button>'
            : `<button class="btn btn-outline-primary border-primary text-light btn-sm save-scanned-device" type="button" data-mac="${escapeHtml(macAddress)}" data-ip="${escapeHtml(ip)}" data-hostname="${escapeHtml(hostname)}"><i data-lucide="plus" class="tracking-icon-sm me-1"></i> Add to Monitoring</button>`;

        row.querySelector('.scan-device-col').innerHTML = `<strong>${escapeHtml(hostname)}</strong><div class="device-mac mt-1">${escapeHtml(system)}</div>`;
        row.querySelector('.scan-status-col').innerHTML = `<span class="${statusClass}">${statusLabel}</span>`;
        row.querySelector('.scan-network-col').innerHTML = `<strong class="tracking-scan-label">IP:</strong> <span class="text-success">${escapeHtml(ip)}</span><br><strong class="tracking-scan-label">MAC:</strong> ${escapeHtml(macAddress)}${reportedMac && reportedMac !== macAddress ? `<div class="tracking-scan-mac-note">Using scanner MAC for identity. Agent reported ${escapeHtml(reportedMac)}.</div>` : ''}`;
        row.querySelector('.scan-tracking-col').innerHTML = `<strong class="tracking-scan-state">${trackingText}</strong>`;
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
        let savedSuccessfully = false;
        setButtonLoading(button, 'Saving...');

        try {
            const response = await requestJson('/api/tracking/save-device', {
                method: 'POST',
                body: JSON.stringify(payload),
            });

            if (!response.success) {
                showNotification(response.error || 'Save failed.', 'danger');
                return;
            }

            showNotification('Device added to monitoring.', 'success');
            if (response.device) {
                upsertStoredDeviceRow(response.device);
            }
            savedSuccessfully = true;
            button.disabled = true;
            button.textContent = 'Already Monitored';
            decrementScanReadyCount();
            await refreshStoredDeviceStatuses({ reason: 'manual', manual: true });
        } catch (error) {
            showNotification(error.message || 'Save failed.', 'danger');
        } finally {
            if (!savedSuccessfully) {
                resetButtonLoading(button, originalLabel);
            }
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
                const parsedLastSeen = parseUniversalDate(device.timestamp);
                const lastSeen = parsedLastSeen ? parsedLastSeen.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' }) : 'Never';
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
            return 'Tracking reconciliation is running. Retry in a few seconds.';
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
        if (toastApi) {
            toastApi.show(String(message || ''), type || 'info', {
                durationMs: 5000,
                container: '#trackingNotificationHost'
            });
            return;
        }

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

    function showActionBanner(config = {}) {
        const banner = document.getElementById('trackingDeviceActionBanner');
        if (!banner) {
            return;
        }

        const titleNode = document.getElementById('trackingDeviceActionBannerTitle');
        const messageNode = document.getElementById('trackingDeviceActionBannerMessage');
        const detailNode = document.getElementById('trackingDeviceActionBannerDetail');

        if (titleNode) {
            titleNode.textContent = config.title || 'Update complete';
        }
        if (messageNode) {
            messageNode.textContent = config.message || '';
        }
        if (detailNode) {
            detailNode.textContent = config.detail || '';
            detailNode.classList.toggle('d-none', !config.detail);
        }

        banner.classList.remove('d-none');

        window.clearTimeout(actionBannerTimer);
        actionBannerTimer = window.setTimeout(hideActionBanner, 5200);
    }

    function hideActionBanner() {
        const banner = document.getElementById('trackingDeviceActionBanner');
        if (!banner) {
            return;
        }

        banner.classList.add('d-none');
        window.clearTimeout(actionBannerTimer);
        actionBannerTimer = null;
    }

    function runPremiumEntrance() {
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            return;
        }

        const targets = Array.from(document.querySelectorAll(
            '.tracking-page-header, #device-kpi-row .ops-kpi-card, .tracking-section-devices .ops-stored-card, .tracking-section-discovery .ops-stored-card'
        ));

        targets.forEach((node, index) => {
            if (!node || typeof node.animate !== 'function') {
                return;
            }

            node.animate(
                [
                    { opacity: 0, transform: 'translateY(8px)' },
                    { opacity: 1, transform: 'translateY(0)' },
                ],
                {
                    duration: 220,
                    delay: Math.min(index * 36, 180),
                    easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
                    fill: 'both',
                }
            );
        });
    }

    function setButtonLoading(button, loadingLabel) {
        if (!button) {
            return;
        }
        if (loadingApi) {
            loadingApi.setButtonBusy(button, {
                busy: true,
                labelBusy: loadingLabel || 'Working...',
                labelIdle: button.dataset.uiIdleLabel || button.innerHTML,
            });
            return;
        }
        if (!button.dataset.uiIdleLabel) {
            button.dataset.uiIdleLabel = button.innerHTML;
        }
        button.disabled = true;
        button.innerHTML = loadingLabel || 'Working...';
    }

    function resetButtonLoading(button, originalHtml) {
        if (!button) {
            return;
        }
        if (loadingApi) {
            loadingApi.setButtonBusy(button, {
                busy: false,
                labelIdle: originalHtml,
            });
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
