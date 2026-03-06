(function () {
    'use strict';

    const config = window.TRACKING_DEVICE_LIVE_CONFIG || {};
    const deviceId = Number(config.deviceId || 0);
    const macAddress = String(config.macAddress || '').trim();
    const pollMs = Math.max(3000, Number(config.pollMs || 5000));

    if (!deviceId || !macAddress) {
        return;
    }

    const stateMachineApi = window.DeviceConsoleStateMachine || {};
    const riskApi = window.DeviceConsoleRisk || {};
    const telemetryStateApi = window.DeviceConsoleTelemetryState || {};
    const cacheStoreApi = window.DeviceConsoleCacheStore || {};
    const mutationLocksApi = window.DeviceConsoleMutationLocks || {};
    const eventFeedApi = window.DeviceConsoleEventFeedStore || {};
    const ackFallbackApi = window.DeviceConsoleAckFallbackStore || {};
    const apiNormalizer = window.DeviceConsoleApiNormalizer || {};

    const responseCache = typeof cacheStoreApi.createCacheStore === 'function'
        ? cacheStoreApi.createCacheStore(10000)
        : null;
    const mutationLocks = typeof mutationLocksApi.createMutationLocks === 'function'
        ? mutationLocksApi.createMutationLocks()
        : null;
    const eventFeedStore = typeof eventFeedApi.createEventFeedStore === 'function'
        ? eventFeedApi.createEventFeedStore({ key: `device_console_events:${deviceId}`, maxItems: 80 })
        : null;
    const ackFallbackStore = typeof ackFallbackApi.createAckFallbackStore === 'function'
        ? ackFallbackApi.createAckFallbackStore({ key: `device_console_ack:${deviceId}` })
        : null;

    const state = {
        pollTimer: null,
        lastPollTs: null,
        lastPollDurationMs: null,
        activeTab: 'overview',
        historyLoaded: false,
        lastKnownStatus: normalizeStatus(config.initialStatus || 'offline'),
        baseRiskLevel: 'UNKNOWN',
        cameraStreaming: false,
        micStreaming: false,
        modals: {},
        lazyLoaded: {
            history: false,
            websitePolicy: false,
            alerts: false,
        },
        tabCounters: {
            processes: 0,
            alerts: 0,
            websitePolicy: 0,
        },
        policy: {
            activeViolationCount: 0,
            highestViolationSeverity: 'LOW',
            latestViolationTimestamp: null,
            alerts: [],
            hasHydratedAlerts: false,
            lastFetchedAt: 0,
        },
        websitePolicy: {
            loading: false,
            loadedAt: null,
            mode: 'Inactive',
            restrictedSites: [],
            restrictedMeta: [],
            violationsToday: 0,
            recentViolations: [],
            source: 'unavailable',
            rawPolicy: null,
            selectedDomains: [],
        },
        deviceState: {
            connectivity: 'offline',
            telemetry: 'stale',
            policy: 'compliant',
            risk: 'low',
            risk_score: 0,
        },
        lastToast: {
            message: '',
            at: 0,
        },
        series: {
            cpu: [],
            ram: [],
            network: [],
        },
    };

    const dom = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheDom();
        bindEvents();
        initModalHandles();
        applyInitialIdentityBadges();
        stopCameraStream(true, { forceRemote: true });
        switchTab('overview', { skipLazyLoad: true });
        refreshLiveData(false, { preferCache: true });
        state.pollTimer = window.setInterval(() => refreshLiveData(false), pollMs);
    }

    function cacheDom() {
        dom.errorBanner = document.getElementById('deviceLiveErrorBanner');
        dom.statusBadge = document.getElementById('deviceHeaderStatus');
        dom.riskBadge = document.getElementById('headerRiskBadge');
        dom.policyBadge = document.getElementById('headerPolicyBadge');
        dom.pollMeta = document.getElementById('devicePollMeta');
        dom.pollDot = document.getElementById('devicePollDot');
        dom.forcePollBtn = document.getElementById('deviceForcePollBtn');
        dom.isolateBtn = document.getElementById('deviceIsolateBtn');
        dom.restartBtn = document.getElementById('deviceRestartBtn');
        dom.messageBtn = document.getElementById('deviceMessageBtn');
        dom.remoteViewBtn = document.getElementById('deviceRemoteViewBtn');
        dom.confirmIsolateBtn = document.getElementById('deviceConfirmIsolateBtn');
        dom.messageText = document.getElementById('deviceMessageText');
        dom.messageSendBtn = document.getElementById('deviceMessageSendBtn');
        dom.toastRoot = document.getElementById('deviceConsoleToastRoot');
        dom.remoteViewImage = document.getElementById('remoteViewImage');
        dom.remoteViewRefreshBtn = document.getElementById('remoteViewRefreshBtn');
        dom.remoteViewFullscreenBtn = document.getElementById('remoteViewFullscreenBtn');
        dom.policyModeValue = document.getElementById('policyModeValue');
        dom.policyModeDot = document.getElementById('policyModeDot');
        dom.policyRestrictedCount = document.getElementById('policyRestrictedCount');
        dom.policyViolationsToday = document.getElementById('policyViolationsToday');
        dom.policyRestrictedSitesList = document.getElementById('policyRestrictedSitesList');
        dom.policyRecentViolationsList = document.getElementById('policyRecentViolationsList');
        dom.policyAddSiteBtn = document.getElementById('policyAddSiteBtn');
        dom.policyRemoveSiteBtn = document.getElementById('policyRemoveSiteBtn');
        dom.policyAddSiteInput = document.getElementById('policyAddSiteInput');
        dom.policyAddSiteCategory = document.getElementById('policyAddSiteCategory');
        dom.policyAddSiteReason = document.getElementById('policyAddSiteReason');
        dom.policyAddSiteConfirmBtn = document.getElementById('policyAddSiteConfirmBtn');
        dom.policyRemoveSiteList = document.getElementById('policyRemoveSiteList');
        dom.policyRemoveSiteConfirmBtn = document.getElementById('policyRemoveSiteConfirmBtn');
        dom.tabCountProcesses = document.getElementById('tabCountProcesses');
        dom.tabCountPolicy = document.getElementById('tabCountPolicy');
        dom.tabCountAlerts = document.getElementById('tabCountAlerts');
        dom.agentHealthAwaiting = document.getElementById('agentHealthAwaiting');
        dom.telemetryBanner = document.getElementById('telemetryWaitingBanner');
        dom.telemetryHeartbeat = document.getElementById('telemetryBannerHeartbeat');
        dom.telemetryPoll = document.getElementById('telemetryBannerPoll');
        dom.telemetryIndicator = document.querySelector('.telemetry-status-indicator');
        dom.policyViolationList = document.getElementById('policyViolationList');
        dom.alertsRiskBar = document.getElementById('alertsRiskBar');
        dom.surveillanceStateBadge = document.getElementById('survTabStateBadge');
        dom.surveillanceStateText = document.getElementById('survTabStateText');
        dom.cameraCapabilityBadge = document.getElementById('survCameraCapabilityBadge');
        dom.cameraStatusBadge = document.getElementById('survCameraStatusBadge');
        dom.micCapabilityBadge = document.getElementById('survMicCapabilityBadge');
        dom.micStatusBadge = document.getElementById('survMicStatusBadge');
        dom.cameraStartBtn = document.getElementById('survCameraStartBtn');
        dom.cameraStopBtn = document.getElementById('survCameraStopBtn');
        dom.cameraCaptureBtn = document.getElementById('survCameraCaptureBtn');
        dom.micStartBtn = document.getElementById('survMicStartBtn');
        dom.micStopBtn = document.getElementById('survMicStopBtn');
        dom.cameraFullscreenBtn = document.getElementById('survCameraFullscreenBtn');
        dom.cameraRecordingIndicator = document.getElementById('survCameraRecordingIndicator');
        dom.cameraLog = document.getElementById('survCameraLog');
        dom.micLog = document.getElementById('survMicLog');
        dom.cameraPreviewWrap = document.getElementById('survCameraPreviewWrap');
        dom.cameraPreview = document.getElementById('survCameraPreview');
        dom.cameraFallback = document.getElementById('survCameraFallback');
        dom.cameraFallbackText = document.getElementById('survCameraFallbackText');
        dom.captureCanvas = document.getElementById('survCaptureCanvas');
        dom.captureDownload = document.getElementById('survCaptureDownload');
        dom.micAudioShell = document.getElementById('survMicAudioShell');
        dom.micAudio = document.getElementById('survMicAudio');
        dom.micFallback = document.getElementById('survMicFallback');
        dom.micFallbackText = document.getElementById('survMicFallbackText');
        dom.micLevelMeter = document.querySelector('#survMicAudioShell .surveillance-level-meter');
        dom.micPlaybackState = document.getElementById('survMicPlaybackState');
        dom.micVolume = document.getElementById('survMicVolume');
    }

    function bindEvents() {
        Array.from(document.querySelectorAll('.device-tab-btn')).forEach((button) => {
            button.addEventListener('click', () => switchTab(button.dataset.tab));
        });
        Array.from(document.querySelectorAll('[data-open-tab]')).forEach((button) => {
            button.addEventListener('click', () => switchTab(button.dataset.openTab));
        });

        dom.forcePollBtn?.addEventListener('click', () => refreshLiveData(true));
        dom.isolateBtn?.addEventListener('click', handleIsolateAction);
        dom.restartBtn?.addEventListener('click', handleRestartAction);
        dom.messageBtn?.addEventListener('click', handleMessageAction);
        dom.confirmIsolateBtn?.addEventListener('click', confirmIsolateAction);
        dom.messageSendBtn?.addEventListener('click', submitMessageAction);
        dom.remoteViewBtn?.addEventListener('click', openRemoteViewModal);
        dom.remoteViewRefreshBtn?.addEventListener('click', refreshRemoteViewSnapshot);
        dom.remoteViewFullscreenBtn?.addEventListener('click', openRemoteViewFullscreen);
        dom.policyAddSiteBtn?.addEventListener('click', () => showModal('policyAddSite'));
        dom.policyRemoveSiteBtn?.addEventListener('click', submitPolicyRemoveSite);
        dom.policyAddSiteConfirmBtn?.addEventListener('click', submitPolicyAddSite);
        dom.policyRemoveSiteConfirmBtn?.addEventListener('click', submitPolicyRemoveSite);
        dom.policyRestrictedSitesList?.addEventListener('click', handlePolicyDomainListClick);
        dom.policyRestrictedSitesList?.addEventListener('change', handlePolicyDomainCheckboxToggle);
        dom.policyRecentViolationsList?.addEventListener('click', handleRetryActions);
        dom.policyViolationList?.addEventListener('click', handleAlertActionClick);
        dom.policyViolationList?.addEventListener('click', handleRetryActions);
        dom.errorBanner?.addEventListener('click', handleRetryActions);

        dom.cameraStartBtn?.addEventListener('click', () => startCameraStream());
        dom.cameraStopBtn?.addEventListener('click', () => stopCameraStream(false));
        dom.cameraCaptureBtn?.addEventListener('click', captureCameraSnapshot);
        dom.cameraFullscreenBtn?.addEventListener('click', toggleCameraFullscreen);
        dom.micStartBtn?.addEventListener('click', () => startMicMonitor());
        dom.micStopBtn?.addEventListener('click', () => stopMicMonitor(false));
        dom.micVolume?.addEventListener('input', applyMicVolumeSetting);
        dom.micAudio?.addEventListener('playing', () => {
            setText('survMicPlaybackState', 'Live audio connected');
        });
        dom.micAudio?.addEventListener('waiting', () => {
            setText('survMicPlaybackState', 'Buffering audio stream...');
        });
        dom.micAudio?.addEventListener('stalled', () => {
            setText('survMicPlaybackState', 'Stream stalled - retrying');
        });
        dom.micAudio?.addEventListener('error', () => {
            setText('survMicPlaybackState', 'Audio stream unavailable');
        });

        window.addEventListener('beforeunload', () => {
            if (state.pollTimer) {
                clearInterval(state.pollTimer);
            }
            stopCameraStream(true, { forceRemote: true });
            stopMicMonitor(true);
        });
    }

    function applyInitialIdentityBadges() {
        const deviceType = String(config.deviceType || 'Workstation');
        const initialPolicy = String(config.policyStatus || 'compliant').toLowerCase();
        const policyDomain = String(config.policyDomain || '').trim();
        const initialSyncIso = String(config.initialAgentSyncAt || '').trim() || null;
        const initialSyncAge = ageSecondsFromIso(initialSyncIso);
        const initialAgentState = resolveAgentState(state.lastKnownStatus, initialSyncAge, initialSyncIso);
        const initialStatusReason = deriveStatusReason(state.lastKnownStatus, '', false, initialSyncIso);
        const initialAgentHealth = resolveAgentHealthLabel(state.lastKnownStatus, false, initialSyncAge, initialSyncIso);

        setText('headerDeviceTypeBadge', deviceType);
        setText('identityDeviceType', deviceType);
        setPolicyBadge(initialPolicy, policyDomain, 0);
        setBadgeStatus(state.lastKnownStatus);
        updateSurveillanceReadiness(state.lastKnownStatus);
        setAgentStateBadge(initialAgentState);
        setText('metaAgentHealth', initialAgentHealth);
        setText('metaUptime', 'N/A');
        setText('metaTotalUptime', 'N/A');
        setText('metaDowntime', 'N/A');
        setStatusReason(initialStatusReason);
        setCameraStates('Unknown', 'Disabled', { isActive: false, fallbackText: 'Webcam not available or agent not connected' });
        setMicStates('Unknown', 'Disabled', { isActive: false, fallbackText: 'Microphone monitoring not available or agent not connected' });
        applyDailyUptimeSnapshot(config.initialDailyUptime);
        setLastSeenDisplay(config.initialLastSeenUtc || null);
        if (config.initialDisplayIp) {
            setText('metaIp', config.initialDisplayIp);
        }
        if (dom.telemetryPoll) {
            dom.telemetryPoll.textContent = `Polling every ${Math.round(pollMs / 1000)}s`;
        }
        applyMicVolumeSetting();
        setAgentAwaitingVisibility(!initialSyncIso);
        showTelemetryBanner(!initialSyncIso, initialSyncIso);
        renderPolicyViolations([]);
        renderAlertFeedTimeline([]);
        setTabCounter('processes', 0);
        setTabCounter('alerts', 0);
        setTabCounter('websitePolicy', 0);
        renderWebsitePolicyPanel({
            mode: 'Awaiting',
            restrictedSites: [],
            violationsToday: 0,
            recentViolations: [],
            source: 'pending',
        });
    }

    function switchTab(tabKey, options) {
        const opts = options || {};
        const normalized = String(tabKey || '').trim().toLowerCase();
        if (!normalized) {
            return;
        }
        const currentActive = document.querySelector('.device-tab-panel.active')?.dataset.panel || '';
        if (currentActive === 'surveillance' && normalized !== 'surveillance') {
            if (state.cameraStreaming) {
                stopCameraStream(true);
            }
            if (state.micStreaming) {
                stopMicMonitor(true);
            }
        }
        Array.from(document.querySelectorAll('.device-tab-btn')).forEach((button) => {
            button.classList.toggle('active', button.dataset.tab === normalized);
        });
        Array.from(document.querySelectorAll('.device-tab-panel')).forEach((panel) => {
            panel.classList.toggle('active', panel.dataset.panel === normalized);
        });
        state.activeTab = normalized;

        if (!opts.skipLazyLoad) {
            void loadTabDataIfNeeded(normalized);
        }
    }

    function handleIsolateAction() {
        if (!showModal('isolateConfirm')) {
            showInfo('Isolation endpoint is not configured yet for tracked agents.');
        }
    }

    async function handleRestartAction() {
        await refreshLiveData(true);
        showInfo('Restart endpoint is unavailable. Forced sync completed instead.');
    }

    function handleMessageAction() {
        if (!showModal('message')) {
            showInfo('Messaging endpoint is not configured yet.');
        }
    }

    async function toggleCameraFullscreen() {
        const container = dom.cameraPreviewWrap;
        if (!container) return;
        try {
            if (document.fullscreenElement) {
                await document.exitFullscreen();
                return;
            }
            if (container.requestFullscreen) {
                await container.requestFullscreen();
            }
        } catch (error) {
            // Non-blocking: fullscreen is best-effort.
        }
    }

    async function refreshLiveData(force, options) {
        clearError();
        const started = Date.now();
        const opts = options || {};

        try {
            let payloadEnvelope;
            try {
                payloadEnvelope = await fetchLiveTelemetryEnvelope(force, opts);
            } catch (firstError) {
                if (isTransientLiveFetchError(firstError) && !opts.retryAttempted) {
                    await waitMs(220);
                    payloadEnvelope = await fetchLiveTelemetryEnvelope(false, { preferCache: true, retryAttempted: true });
                } else {
                    throw firstError;
                }
            }
            const { payload } = payloadEnvelope;
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid live telemetry payload.');
            }

            const trackingData = ensureObject(payload.tracking_data);
            const deviceInfo = ensureObject(payload.device_info);
            const availabilityStatus = normalizeStatus(
                payload.availability_status || deviceInfo.status || (payload.success ? 'online' : 'offline')
            );

            state.lastPollTs = Date.now();
            state.lastPollDurationMs = state.lastPollTs - started;

            renderSnapshot({
                status: availabilityStatus,
                trackingData,
                deviceInfo,
                probeErrorCode: payload.error_code || payload.probe?.error_code || '',
                timestampIso: payload.timestamp || new Date().toISOString(),
                metricsAvailable: Boolean(payload.metrics_available),
            });
        } catch (error) {
            if (error?.status === 401 || error?.status === 403) {
                if (state.pollTimer) {
                    clearInterval(state.pollTimer);
                    state.pollTimer = null;
                }
            }
            showError(error?.message || 'Failed to fetch live telemetry.');
            setBadgeStatus(state.lastKnownStatus || 'offline');
            updateSurveillanceReadiness(state.lastKnownStatus || 'offline');
            if (dom.telemetryIndicator) {
                dom.telemetryIndicator.classList.remove('state-healthy', 'state-degraded', 'state-critical');
                dom.telemetryIndicator.classList.add('state-offline');
            }
            setText('telemetryStatusTitle', 'OFFLINE');
            reconcileGlobalDeviceState({
                connectivity: normalizeStatus(state.lastKnownStatus || 'offline'),
                telemetry: 'offline',
                policyViolations: state.policy.activeViolationCount,
                riskLevel: 'high',
                riskScore: 88,
            });
        } finally {
            try {
                const nowTs = Date.now();
                const refreshWindowMs = Math.max(3500, pollMs - 300);
                const shouldForcePolicyRefresh = !state.policy.lastFetchedAt || ((nowTs - state.policy.lastFetchedAt) >= refreshWindowMs);
                await refreshPolicyViolations(shouldForcePolicyRefresh);
            } catch (error) {
                // Policy/alerts failures must not block telemetry polling.
            }
            updatePollMeta();
        }
    }

    async function fetchLiveTelemetryEnvelope(force, options) {
        const opts = options || {};
        const query = [];
        if (force) query.push('force=1');
        if (opts.preferCache) query.push('prefer_cache=1');
        const endpoint = `/api/tracking/real-time/${encodeURIComponent(macAddress)}${query.length ? `?${query.join('&')}` : ''}`;
        return requestJson(endpoint, {
            method: 'GET',
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
        });
    }

    function isTransientLiveFetchError(error) {
        const status = toNumber(error?.status, NaN);
        if (Number.isFinite(status) && status >= 500) {
            return true;
        }
        const message = String(error?.message || '').toLowerCase();
        return (
            message.includes('failed to fetch') ||
            message.includes('networkerror') ||
            message.includes('unexpected response format (5')
        );
    }

    function waitMs(ms) {
        const duration = Math.max(0, toNumber(ms, 0));
        return new Promise((resolve) => window.setTimeout(resolve, duration));
    }

    function renderSnapshot(snapshot) {
        const tracking = ensureObject(snapshot.trackingData);
        const systemMetrics = ensureObject(tracking.system_metrics);
        const activity = ensureObject(tracking.current_activity);
        const todayStats = ensureObject(tracking.today_stats);
        const meta = ensureObject(tracking.meta);
        const network = extractNetworkMetrics(tracking);
        const hasTelemetry = hasTelemetrySnapshot(snapshot, tracking);

        const cpu = toNumber(systemMetrics.cpu_percent ?? systemMetrics.cpu_usage, 0);
        const ram = toNumber(systemMetrics.memory_percent ?? systemMetrics.ram_percent ?? systemMetrics.memory_usage, 0);
        const disk = toNumber(systemMetrics.disk_percent ?? systemMetrics.disk_usage, 0);
        const idleSeconds = toNumber(activity.idle_seconds, 0);
        const keyboardEvents = toNumber(todayStats.keyboard_events, 0);
        const mouseEvents = toNumber(todayStats.mouse_events, 0);
        const scrollEvents = toNumber(todayStats.scroll_events, 0);
        const activeApp = resolveActiveApp(activity, systemMetrics, todayStats);
        const activeWindow = resolveActiveWindow(systemMetrics, activity);
        const agentVersion = meta.agent_version || snapshot.deviceInfo.agent_version || 'N/A';
        const syncAge = ageSecondsFromIso(snapshot.deviceInfo.last_agent_sync_at);
        const lastSeenIso = snapshot.deviceInfo.last_seen || snapshot.deviceInfo.last_agent_sync_at || null;
        const awaitingFirstTelemetry = !snapshot.deviceInfo.last_agent_sync_at && !hasTelemetry;
        const agentState = resolveAgentState(snapshot.status, syncAge, snapshot.deviceInfo.last_agent_sync_at);
        const statusReason = deriveStatusReason(snapshot.status, snapshot.probeErrorCode, hasTelemetry, snapshot.deviceInfo.last_agent_sync_at);
        const agentHealth = resolveAgentHealthLabel(snapshot.status, hasTelemetry, syncAge, snapshot.deviceInfo.last_agent_sync_at);
        const previousStatus = state.lastKnownStatus;
        state.lastKnownStatus = normalizeStatus(snapshot.status);
        setBadgeStatus(state.lastKnownStatus);
        updateSurveillanceReadiness(state.lastKnownStatus);
        if (previousStatus !== state.lastKnownStatus) {
            eventFeedStore?.push?.({
                id: `status:${Date.now()}`,
                time: formatClockTime(new Date()),
                text: `Device state changed: ${state.lastKnownStatus.toUpperCase()}`,
            });
        }
        if (state.lastKnownStatus === 'offline') {
            if (state.cameraStreaming) {
                stopCameraStream(true);
            }
            if (state.micStreaming) {
                stopMicMonitor(true);
            }
        }
        setAgentStateBadge(agentState);
        const displayIp = sanitizeDisplayIp(snapshot.deviceInfo.ip_address || snapshot.deviceInfo.ip, snapshot.deviceInfo.last_agent_sync_ip);
        setText('metaEmployee', snapshot.deviceInfo.employee_name || 'Unassigned');
        setText('metaIp', displayIp || 'N/A');
        setText('metaHostname', snapshot.deviceInfo.hostname || 'N/A');
        setText('metaMac', snapshot.deviceInfo.mac_address || macAddress);
        setText('metaSystem', snapshot.deviceInfo.system || snapshot.deviceInfo.os || 'Unknown');
        setLastSeenDisplay(lastSeenIso);
        setStatusReason(statusReason);
        applyDailyUptimeSnapshot(snapshot.deviceInfo.daily_uptime);
        setText('metaAgentVersion', agentVersion);
        setText('metaLatency', `${Math.round(toNumber(state.lastPollDurationMs, 0))} ms`);
        setText('metaAgentHealth', agentHealth);
        setAgentAwaitingVisibility(awaitingFirstTelemetry);
        showTelemetryBanner(awaitingFirstTelemetry, snapshot.deviceInfo.last_agent_sync_at);

        const telemetrySignal = typeof telemetryStateApi.deriveTelemetryState === 'function'
            ? telemetryStateApi.deriveTelemetryState({
                latencyMs: toNumber(state.lastPollDurationMs, 0),
                heartbeatAgeSeconds: Math.max(0, toNumber(syncAge, 0)),
                pollSeconds: Math.round(pollMs / 1000),
                hasResponse: true,
            })
            : { state: hasTelemetry ? 'healthy' : 'partial', label: 'LIVE TELEMETRY' };
        reconcileGlobalDeviceState({
            connectivity: state.lastKnownStatus,
            telemetry: telemetrySignal.state,
            policyViolations: state.policy.activeViolationCount,
            alertsCount: (state.policy.alerts || []).length,
            riskLevel: state.deviceState.risk,
            riskScore: state.deviceState.risk_score,
        });

        if (!hasTelemetry) {
            applyAwaitingTelemetryState(snapshot.status, snapshot.probeErrorCode, awaitingFirstTelemetry);
            if (snapshot.status === 'online' || snapshot.status === 'degraded') {
                setCameraStates('Available', state.cameraStreaming ? 'Active' : 'Inactive', {
                    isActive: state.cameraStreaming,
                    fallbackText: 'Stream inactive',
                });
                setMicStates('Available', state.micStreaming ? 'Active' : 'Inactive', {
                    isActive: state.micStreaming,
                    fallbackText: 'Microphone monitor inactive',
                });
            } else {
                setCameraStates('Disabled', 'Disabled', {
                    isActive: false,
                    fallbackText: 'Webcam not available or agent not connected',
                });
                setMicStates('Disabled', 'Disabled', {
                    isActive: false,
                    fallbackText: 'Microphone monitoring not available or agent not connected',
                });
            }
            return;
        }

        setText('overviewCpu', `${Math.round(cpu)}%`);
        setText('overviewRam', `${Math.round(ram)}%`);
        setText('overviewDisk', `${Math.round(disk)}%`);
        setText('overviewUpload', `${formatSpeed(network.uploadKbps)}`);
        setText('overviewDownload', `${formatSpeed(network.downloadKbps)}`);
        setText('overviewIdle', `${Math.floor(idleSeconds / 60)}m`);
        setText('activityIdleCompact', `${Math.floor(idleSeconds / 60)}m`);
        setText('overviewActiveApp', activeApp || 'No active app');
        setText('overviewWindowTitle', activeWindow || 'N/A');
        setText('overviewKeyboardState', activity.keyboard_active ? 'Active' : 'Inactive');
        setText('overviewMouseState', activity.mouse_active ? 'Active' : 'Inactive');
        setText('overviewActiveTime', formatDuration(todayStats.active_time_seconds ?? todayStats.total_active_seconds ?? 0));
        setText('overviewTotalTime', formatDuration(todayStats.total_time_seconds ?? todayStats.tracked_seconds ?? 0));
        setText('overviewAppCount', String(Array.isArray(todayStats.applications_used) ? todayStats.applications_used.length : 0));
        setText('overviewLastPoll', formatTimestamp(snapshot.timestampIso));

        setText('activityKeyboardCount', String(keyboardEvents));
        setText('activityMouseCount', String(mouseEvents));
        setText('activityScrollCount', String(scrollEvents));
        setText('activityIdleDuration', `${idleSeconds}s`);
        setText('activityFocusedApp', activeApp || 'N/A');
        setText('activityFocusedWindow', activeWindow || 'N/A');
        setText('activityFocusChanged', formatTimestamp(snapshot.timestampIso));
        setText('activityConfidence', snapshot.metricsAvailable ? 'High' : 'Low');

        setText('networkUpload', formatSpeed(network.uploadKbps));
        setText('networkDownload', formatSpeed(network.downloadKbps));
        setText('networkUploadTotal', `${toFixed(network.uploadMb, 2)} MB`);
        setText('networkDownloadTotal', `${toFixed(network.downloadMb, 2)} MB`);
        renderNetworkConsumers(systemMetrics);
        renderProcesses(systemMetrics);
        setTabCounter('processes', getSuspiciousProcessCount(systemMetrics));

        pushSeriesValue('cpu', cpu);
        pushSeriesValue('ram', ram);
        pushSeriesValue('network', network.uploadKbps + network.downloadKbps);
        const palette = getChartPalette();
        renderSparkline('chartCpu', state.series.cpu, palette.healthy);
        renderSparkline('chartRam', state.series.ram, palette.info);
        renderSparkline('chartNetwork', state.series.network, palette.warning);
        renderSparkline('overviewCpuSpark', state.series.cpu, palette.healthy, 80, 18);
        renderSparkline('overviewRamSpark', state.series.ram, palette.info, 80, 18);

        setText('chartCpuMeta', `Last: ${Math.round(cpu)}%`);
        setText('chartRamMeta', `Last: ${Math.round(ram)}%`);
        setText('chartNetworkMeta', `Last: ${formatSpeed(network.uploadKbps + network.downloadKbps)}`);
        setTrendIndicator('overviewCpuTrend', state.series.cpu);
        setTrendIndicator('overviewRamTrend', state.series.ram);
        setThresholdClass('overviewCpu', cpu);
        setThresholdClass('overviewRam', ram);

        const riskSnapshot = renderAlerts(snapshot.status, cpu, ram, disk, idleSeconds, snapshot.probeErrorCode);
        state.baseRiskLevel = String(riskSnapshot.level || 'LOW').toUpperCase();
        setHeaderRiskBadge(state.baseRiskLevel);
        setText('metaSecurityScore', String(riskSnapshot.securityScore));
        applyRiskScoreVisual(riskSnapshot.riskScore, String(riskSnapshot.level || 'LOW').toLowerCase());

        if (snapshot.status === 'online' || snapshot.status === 'degraded') {
            setCameraStates('Available', state.cameraStreaming ? 'Active' : 'Inactive', {
                isActive: state.cameraStreaming,
                fallbackText: 'Stream inactive',
            });
            setMicStates('Available', state.micStreaming ? 'Active' : 'Inactive', {
                isActive: state.micStreaming,
                fallbackText: 'Microphone monitor inactive',
            });
        } else {
            setCameraStates('Disabled', 'Disabled', {
                isActive: false,
                fallbackText: 'Webcam not available or agent not connected',
            });
            setMicStates('Disabled', 'Disabled', {
                isActive: false,
                fallbackText: 'Microphone monitoring not available or agent not connected',
            });
        }
    }

    function renderProcesses(systemMetrics) {
        const body = document.getElementById('processTableBody');
        const empty = document.getElementById('processEmptyState');
        if (!body) return;

        const processes = Array.isArray(systemMetrics.top_processes) ? systemMetrics.top_processes : [];
        if (!processes.length) {
            patchKeyedChildren(body, [], () => '', 'tr', () => {});
            empty?.classList.remove('d-none');
            return;
        }

        patchKeyedChildren(
            body,
            processes.slice(0, 15),
            (process, index) => `${process.pid || 'na'}:${process.process_name || process.name || 'unknown'}:${index}`,
            'tr',
            (row, process) => {
                const name = process.process_name || process.name || 'Unknown';
                const cpu = toNumber(process.cpu_percent ?? process.cpu, 0);
                const memoryMb = toNumber(process.memory_mb ?? process.memory, 0);
                const pid = process.pid ?? 'n/a';
                row.innerHTML = `
                    <td>${escapeHtml(String(name))}</td>
                    <td class="metric">${toFixed(cpu, 1)}</td>
                    <td class="metric">${toFixed(memoryMb, 1)}</td>
                    <td class="metric">${escapeHtml(String(pid))}</td>
                `;
            }
        );
        empty?.classList.add('d-none');
    }

    function getSuspiciousProcessCount(systemMetrics) {
        const processes = Array.isArray(systemMetrics?.top_processes) ? systemMetrics.top_processes : [];
        return processes.filter((process) => {
            const cpu = toNumber(process?.cpu_percent ?? process?.cpu, 0);
            const memoryMb = toNumber(process?.memory_mb ?? process?.memory, 0);
            return cpu >= 40 || memoryMb >= 500;
        }).length;
    }

    function renderNetworkConsumers(systemMetrics) {
        const container = document.getElementById('networkConsumersList');
        if (!container) return;
        const consumers = Array.isArray(systemMetrics.top_processes) ? systemMetrics.top_processes.slice(0, 8) : [];
        const rows = consumers.length ? consumers : [{ __empty: true }];
        patchKeyedChildren(
            container,
            rows,
            (process, index) => process.__empty ? '__empty' : `${process.pid || 'na'}:${process.process_name || process.name || 'unknown'}:${index}`,
            'div',
            (row, process) => {
                row.className = 'device-info-row';
                if (process.__empty) {
                    row.innerHTML = '<span>N/A</span><strong>No network consumer data.</strong>';
                    return;
                }
                const name = escapeHtml(String(process.process_name || process.name || 'Unknown'));
                const up = toNumber(process.upload_kbps ?? process.net_upload_kbps, 0);
                const down = toNumber(process.download_kbps ?? process.net_download_kbps, 0);
                row.innerHTML = `<span>${name}</span><strong>UP ${toFixed(up, 1)} / DOWN ${toFixed(down, 1)} KB/s</strong>`;
            }
        );
    }

    function renderAlerts(status, cpu, ram, disk, idleSeconds, probeErrorCode) {
        const riskNode = document.getElementById('alertsRiskScore');
        const riskContextNode = document.getElementById('alertsRiskContext');
        const feedNode = document.getElementById('alertsFeedList');
        if (!riskNode || !feedNode) return { level: 'LOW', securityScore: 92 };

        const alerts = [];
        let risk = 'LOW';
        let context = 'Telemetry healthy';

        if (status === 'offline') {
            risk = 'HIGH';
            context = 'Device unreachable';
            alerts.push('Device is offline or unreachable.');
        }
        if (status === 'degraded') {
            if (risk !== 'HIGH') risk = 'MEDIUM';
            context = 'Partial telemetry';
            alerts.push('Device is reachable but telemetry is degraded.');
        }
        if (cpu >= 90) {
            risk = 'HIGH';
            context = 'CPU saturation';
            alerts.push(`CPU usage is high (${Math.round(cpu)}%).`);
        } else if (cpu >= 80 && risk === 'LOW') {
            risk = 'MEDIUM';
            context = 'Elevated CPU';
            alerts.push(`CPU usage is elevated (${Math.round(cpu)}%).`);
        }
        if (ram >= 90) {
            if (risk !== 'HIGH') risk = 'MEDIUM';
            alerts.push(`Memory usage is high (${Math.round(ram)}%).`);
        }
        if (disk >= 95) {
            if (risk !== 'HIGH') risk = 'MEDIUM';
            alerts.push(`Disk usage is critical (${Math.round(disk)}%).`);
        }
        if (idleSeconds >= 1800) {
            if (risk === 'LOW') risk = 'MEDIUM';
            alerts.push(`User idle for ${Math.floor(idleSeconds / 60)} minutes.`);
        }
        if (probeErrorCode) {
            alerts.push(`Probe note: ${String(probeErrorCode).replace(/_/g, ' ')}`);
        }

        const riskScore = risk === 'HIGH' ? 90 : (risk === 'MEDIUM' ? 55 : 20);
        const securityScore = Math.max(0, 100 - riskScore);
        riskNode.textContent = risk;
        riskNode.classList.remove('risk-high', 'risk-medium', 'risk-low', 'risk-unknown');
        if (risk === 'HIGH') riskNode.classList.add('risk-high');
        else if (risk === 'MEDIUM') riskNode.classList.add('risk-medium');
        else riskNode.classList.add('risk-low');
        if (riskContextNode) riskContextNode.textContent = context;
        const timeLabel = formatClockTime(new Date());
        const timelineRows = alerts.map((line, index) => ({
            id: `telemetry:${timeLabel}:${index}`,
            time: timeLabel,
            text: line,
        }));
        timelineRows.forEach((event) => eventFeedStore?.push?.(event));
        const persisted = eventFeedStore?.list?.() || [];
        const feedRows = timelineRows.length ? timelineRows : persisted.slice(0, 8);
        if (!feedRows.length) {
            feedNode.innerHTML = '<div class="policy-violation-empty">No alerts detected</div>';
        } else {
            patchKeyedChildren(
                feedNode,
                feedRows,
                (entry) => `${entry.id}:${entry.time}`,
                'div',
                (row, entry) => {
                    row.className = 'device-event-row';
                    row.innerHTML = `<span>${escapeHtml(String(entry.time || '--:--'))}</span><strong>${escapeHtml(String(entry.text || 'Event'))}</strong>`;
                }
            );
        }
        setTabCounter('alerts', Math.max(alerts.length, state.policy.activeViolationCount));

        return { level: risk, securityScore, riskScore };
    }

    async function refreshPolicyViolations(forceReload) {
        const previousCount = Math.max(0, Math.floor(toNumber(state.policy.activeViolationCount, 0)));
        const previousActiveAlertKeys = new Set(
            (Array.isArray(state.policy.alerts) ? state.policy.alerts : [])
                .filter((alert) => normalizeViolationStatus(alert?.status) !== 'resolved')
                .map((alert, index) => getAlertIdentityKey(alert, index))
        );
        const response = await fetchAlertsEnvelope(Boolean(forceReload));
        const payload = ensureObject(response?.payload);
        const normalized = normalizeAlertsApiPayload(payload);

        state.policy.activeViolationCount = Math.max(0, Math.floor(toNumber(normalized.activeAlertCount, 0)));
        state.policy.highestViolationSeverity = normalizeViolationSeverity(payload.highest_severity || payload.highest_violation_severity);
        state.policy.latestViolationTimestamp = normalized.alerts[0]?.time || null;
        state.policy.alerts = normalized.alerts;
        state.policy.lastFetchedAt = Date.now();

        setTabCounter('alerts', state.policy.activeViolationCount);
        setTabCounter('websitePolicy', state.policy.activeViolationCount);

        renderPolicyViolations(normalized.alerts);
        renderAlertFeedTimeline(normalized.alerts);
        applyPolicyOverlay();
        syncWebsitePolicyViolationsFromAlerts(normalized.alerts);
        applyRiskScoreVisual(normalized.riskScore, normalized.riskLevel);

        if (state.policy.hasHydratedAlerts && state.policy.activeViolationCount > previousCount) {
            const activeAlerts = (Array.isArray(normalized.alerts) ? normalized.alerts : [])
                .filter((alert) => normalizeViolationStatus(alert?.status) !== 'resolved');
            const newestAlert = activeAlerts.find((alert, index) => !previousActiveAlertKeys.has(getAlertIdentityKey(alert, index)))
                || activeAlerts[0]
                || null;
            const detectedDomain = String(
                newestAlert?.site_visited
                || newestAlert?.domain
                || newestAlert?.site
                || ''
            ).trim();
            showToast(
                detectedDomain
                    ? `Restricted site visit detected: ${detectedDomain}`
                    : 'Restricted site visit detected.',
                'warning'
            );
        }
        state.policy.hasHydratedAlerts = true;

        reconcileGlobalDeviceState({
            policyViolations: state.policy.activeViolationCount,
            alertsCount: normalized.alerts.length,
            riskLevel: normalized.riskLevel,
            riskScore: normalized.riskScore,
        });
        return normalized;
    }

    async function loadAlertsTabData(forceReload) {
        try {
            await refreshPolicyViolations(forceReload);
        } catch (error) {
            renderPolicyViolations([]);
            if (dom.policyViolationList) {
                dom.policyViolationList.innerHTML = `
                    <div class="policy-error-card">
                        <strong>Alerts failed to load</strong>
                        <button type="button" class="tactical-btn tactical-btn-outline" data-action-retry-alerts="1">Retry</button>
                    </div>
                `;
            }
        }
    }

    function applyPolicyOverlay() {
        const activeCount = Math.max(0, Math.floor(toNumber(state.policy.activeViolationCount, 0)));
        const highestViolationSeverity = normalizeViolationSeverity(state.policy.highestViolationSeverity);
        const baseRisk = normalizeRiskLevel(state.baseRiskLevel);
        let effectiveRisk = baseRisk;

        if (activeCount > 0) {
            if (highestViolationSeverity === 'HIGH') {
                effectiveRisk = 'HIGH';
            } else if (baseRisk === 'HIGH') {
                effectiveRisk = 'HIGH';
            } else if (baseRisk === 'MEDIUM') {
                effectiveRisk = 'HIGH';
            } else {
                effectiveRisk = 'MEDIUM';
            }
        }

        const latestActiveAlert = Array.isArray(state.policy.alerts)
            ? state.policy.alerts.find((alert) => normalizeViolationStatus(alert.status) !== 'resolved')
            : null;
        const latestDomain = latestActiveAlert
            ? String(latestActiveAlert.site_visited || latestActiveAlert.domain || '').trim()
            : '';

        if (activeCount > 0) {
            setPolicyBadge('violating', latestDomain, activeCount);
        } else {
            setPolicyBadge('compliant', '', 0);
        }
        setHeaderRiskBadge(effectiveRisk);

        dom.riskBadge?.classList.toggle('risk-policy-active', activeCount > 0);
        document.querySelector('.device-identity-panel')?.classList.toggle('policy-violation-active', activeCount > 0);

        if (dom.policyBadge) {
            dom.policyBadge.title = activeCount > 0
                ? `${activeCount} active restricted-site violation${activeCount === 1 ? '' : 's'}${latestDomain ? ` (latest: ${latestDomain})` : ''}`
                : 'No active policy violations';
        }
    }

    function renderPolicyViolations(alerts) {
        if (!dom.policyViolationList) {
            return;
        }
        if (dom.policyViolationList.childElementCount === 0 && dom.policyViolationList.textContent.trim()) {
            dom.policyViolationList.textContent = '';
        }

        if (!Array.isArray(alerts) || alerts.length === 0) {
            patchKeyedChildren(
                dom.policyViolationList,
                [{ id: 'no-violations' }],
                (row) => row.id,
                'div',
                (row) => {
                    row.className = 'policy-violation-empty';
                    row.innerHTML = '<span class="policy-violation-ok">&#10003; No policy violations</span>';
                }
            );
            return;
        }

        patchKeyedChildren(
            dom.policyViolationList,
            alerts.slice(0, 12),
            (alert, index) => String(alert.eventId || alert.dashboard_event_id || `${alert.domain || 'domain'}:${alert.time || alert.timestamp || index}`),
            'div',
            (row, alert) => {
                const domain = escapeHtml(String(alert.site || alert.site_visited || alert.domain || 'N/A'));
                const matchedRule = escapeHtml(String(alert.matched_rule || alert.action || 'Blocked'));
                const status = normalizeViolationStatus(alert.status);
                const statusLabel = status === 'active' ? 'Active' : (status === 'acknowledged' ? 'Acknowledged' : 'Resolved');
                const severity = normalizeViolationSeverity(alert.severity || alert.confidence);
                const source = String(alert.source || '').trim().toLowerCase();
                const sourceLabel = source === 'window_title'
                    ? 'Foreground window'
                    : (source === 'dns_cache' ? 'DNS cache' : 'Unknown');
                const detectedAt = formatTimestamp(alert.time || alert.timestamp || alert.observed_at_utc);
                const eventId = String(alert.eventId || alert.dashboard_event_id || '').trim();
                const ackLocked = status !== 'active' || Boolean(ackFallbackStore?.isAcked?.(eventId));

                row.className = `policy-violation-card severity-${severity.toLowerCase()} status-${status}`;
                row.innerHTML = `
                    <div class="policy-violation-head">
                        <strong class="policy-violation-site">${domain}</strong>
                        <span class="policy-severity-badge severity-${severity.toLowerCase()}">${severity}</span>
                        <span class="policy-status-badge status-${status}">${escapeHtml(statusLabel.toUpperCase())}</span>
                    </div>
                    <div class="policy-violation-row"><span>Rule</span><strong>${matchedRule}</strong></div>
                    <div class="policy-violation-row"><span>Detected</span><strong>${escapeHtml(detectedAt)}</strong></div>
                    <div class="policy-violation-row"><span>Source</span><strong>${escapeHtml(sourceLabel)}</strong></div>
                    <div class="policy-violation-actions">
                        <button type="button" class="tactical-btn tactical-btn-outline policy-action-btn" data-policy-action="ack" data-alert-event-id="${escapeHtml(eventId)}" ${ackLocked ? 'disabled' : ''}>Acknowledge</button>
                        <button type="button" class="tactical-btn tactical-btn-outline policy-action-btn" data-policy-action="investigate" data-alert-event-id="${escapeHtml(eventId)}">Investigate</button>
                    </div>
                `;
            }
        );
    }

    function handleAlertActionClick(event) {
        const actionButton = event?.target?.closest('[data-policy-action]');
        if (!actionButton) return;
        const action = String(actionButton.getAttribute('data-policy-action') || '').trim().toLowerCase();
        if (action === 'investigate') {
            window.location.href = `/devices/${encodeURIComponent(deviceId)}/policy-history`;
            return;
        }
        if (action === 'ack') {
            const eventId = String(actionButton.getAttribute('data-alert-event-id') || '').trim();
            if (!eventId) return;
            void acknowledgePolicyAlert(eventId, actionButton);
        }
    }

    async function acknowledgePolicyAlert(eventId, actionButton) {
        const run = async () => {
            setButtonBusy(actionButton, true);
            try {
                await requestJson(`/api/devices/${encodeURIComponent(deviceId)}/alerts/${encodeURIComponent(eventId)}/acknowledge`, {
                    method: 'POST',
                    headers: { Accept: 'application/json' },
                    credentials: 'same-origin',
                });
                ackFallbackStore?.markAcked?.(eventId);
                invalidateDeviceConsoleCaches();
                await refreshPolicyViolations(true);
                showInfo('Policy updated');
            } catch (error) {
                ackFallbackStore?.markAcked?.(eventId);
                showInfo('Acknowledge saved locally; backend sync unavailable.');
            } finally {
                setButtonBusy(actionButton, false);
                if (ackFallbackStore?.isAcked?.(eventId) && actionButton) {
                    actionButton.disabled = true;
                }
            }
        };

        if (mutationLocks && typeof mutationLocks.withLock === 'function') {
            try {
                await mutationLocks.withLock(`alert:ack:${eventId}`, run);
                return;
            } catch (error) {
                if (error?.code === 'LOCKED') {
                    return;
                }
                throw error;
            }
        }
        await run();
    }

    function setButtonBusy(button, isBusy) {
        const node = button;
        if (!node) return;
        node.disabled = Boolean(isBusy);
        node.classList.toggle('is-busy', Boolean(isBusy));
    }

    function handleRetryActions(event) {
        const retryPolicyButton = event?.target?.closest('[data-action-retry-policy]');
        if (retryPolicyButton) {
            event.preventDefault();
            void loadWebsitePolicyData(true);
            return;
        }
        const retryAlertsButton = event?.target?.closest('[data-action-retry-alerts]');
        if (retryAlertsButton) {
            event.preventDefault();
            void loadAlertsTabData(true);
        }
    }

    function renderAlertFeedTimeline(alerts) {
        const container = document.getElementById('alertsFeedList');
        if (!container) return;

        const apiEvents = (Array.isArray(alerts) ? alerts : []).slice(0, 8).map((alert, index) => ({
            id: `alert:${alert.eventId || index}`,
            time: formatClockTime(alert.time || alert.timestamp || alert.observed_at_utc),
            text: `${alert.domain || alert.site || 'unknown'} (${normalizeViolationSeverity(alert.severity)})`,
        }));

        const persisted = eventFeedStore?.list?.() || [];
        const rows = apiEvents.length ? apiEvents : persisted.slice(0, 8);
        if (!rows.length) {
            container.innerHTML = '<div class="policy-violation-empty">No alerts detected</div>';
            return;
        }

        patchKeyedChildren(
            container,
            rows,
            (row) => String(row.id || row.time || row.text),
            'div',
            (node, row) => {
                node.className = 'device-event-row';
                node.innerHTML = `
                    <span>${escapeHtml(String(row.time || '--:--'))}</span>
                    <strong>${escapeHtml(String(row.text || 'Event'))}</strong>
                `;
            }
        );
    }

    function applyRiskScoreVisual(riskScore, riskLevel) {
        const score = Math.max(0, Math.min(100, Math.floor(toNumber(riskScore, 0))));
        const normalizedLevel = String(riskLevel || '').trim().toLowerCase() || normalizeRiskLevel(score >= 70 ? 'HIGH' : (score >= 35 ? 'MEDIUM' : 'LOW')).toLowerCase();
        setText('alertsRiskScore', normalizedLevel.toUpperCase());
        const riskNode = document.getElementById('alertsRiskScore');
        riskNode?.classList.remove('risk-high', 'risk-medium', 'risk-low', 'risk-unknown');
        if (riskNode) {
            if (normalizedLevel === 'high') riskNode.classList.add('risk-high');
            else if (normalizedLevel === 'medium') riskNode.classList.add('risk-medium');
            else riskNode.classList.add('risk-low');
        }
        const barSegments = typeof riskApi.riskBarSegments === 'function' ? riskApi.riskBarSegments(score, 10) : Math.round((score / 100) * 10);
        const filled = Math.max(0, Math.min(10, barSegments));
        const bar = `${'█'.repeat(filled)}${'░'.repeat(10 - filled)}`;
        if (dom.alertsRiskBar) {
            dom.alertsRiskBar.textContent = bar;
            dom.alertsRiskBar.classList.remove('low', 'medium', 'high');
            dom.alertsRiskBar.classList.add(normalizedLevel === 'high' ? 'high' : (normalizedLevel === 'medium' ? 'medium' : 'low'));
        }
        setText('metaSecurityScore', String(Math.max(0, 100 - score)));
    }

    function reconcileGlobalDeviceState(options) {
        const source = options || {};
        const fallbackRiskScore = typeof riskApi.calculateRiskScore === 'function'
            ? riskApi.calculateRiskScore({
                alerts: state.policy.alerts || [],
                policyViolations: source.policyViolations,
                suspiciousProcesses: toNumber(state.tabCounters.processes, 0),
                telemetry: source.telemetry || state.deviceState.telemetry || 'stale',
            })
            : toNumber(source.riskScore, 0);

        if (typeof stateMachineApi.deriveDeviceState === 'function') {
            state.deviceState = stateMachineApi.deriveDeviceState({
                connectivity: source.connectivity || state.lastKnownStatus,
                telemetry: source.telemetry || state.deviceState.telemetry || 'stale',
                policyViolations: toNumber(source.policyViolations, state.policy.activeViolationCount),
                suspiciousProcesses: toNumber(state.tabCounters.processes, 0),
                alertsCount: toNumber(source.alertsCount, (state.policy.alerts || []).length),
                riskScore: toNumber(source.riskScore, fallbackRiskScore),
                risk: source.riskLevel || state.deviceState.risk,
            });
        } else {
            state.deviceState = {
                connectivity: normalizeStatus(source.connectivity || state.lastKnownStatus),
                telemetry: String(source.telemetry || state.deviceState.telemetry || 'stale').toLowerCase(),
                policy: toNumber(source.policyViolations, state.policy.activeViolationCount) > 0 ? 'violations' : 'compliant',
                risk: String(source.riskLevel || state.deviceState.risk || 'low').toLowerCase(),
                risk_score: toNumber(source.riskScore, fallbackRiskScore),
            };
        }
        setHeaderRiskBadge(String(state.deviceState.risk || 'LOW').toUpperCase());
    }

    function setTabCounter(counterName, rawValue) {
        const count = Math.max(0, Math.floor(toNumber(rawValue, 0)));
        if (counterName === 'processes') {
            state.tabCounters.processes = count;
        } else if (counterName === 'alerts') {
            state.tabCounters.alerts = count;
        } else if (counterName === 'websitePolicy') {
            state.tabCounters.websitePolicy = count;
        }

        let node = null;
        if (counterName === 'processes') node = dom.tabCountProcesses;
        if (counterName === 'alerts') node = dom.tabCountAlerts;
        if (counterName === 'websitePolicy') node = dom.tabCountPolicy;
        if (!node) return;

        if (count > 0) {
            node.textContent = count > 99 ? '99+' : String(count);
            node.classList.remove('d-none');
            return;
        }
        node.textContent = '0';
        node.classList.add('d-none');
    }

    function normalizeViolationStatus(value) {
        const normalized = String(value || '').trim().toLowerCase();
        if (normalized === 'active' || normalized === 'acknowledged' || normalized === 'resolved') {
            return normalized;
        }
        if (normalized === 'new' || normalized === 'open') {
            return 'active';
        }
        return 'resolved';
    }

    function normalizeViolationSeverity(value) {
        const normalized = String(value || '').trim().toUpperCase();
        if (normalized === 'HIGH' || normalized === 'CRITICAL') {
            return 'HIGH';
        }
        if (normalized === 'LOW') {
            return 'LOW';
        }
        if (normalized === 'MEDIUM' || normalized === 'WARNING' || normalized === 'INFO') {
            return 'MEDIUM';
        }
        return 'MEDIUM';
    }

    function normalizeRiskLevel(value) {
        const normalized = String(value || 'UNKNOWN').trim().toUpperCase();
        if (normalized === 'HIGH' || normalized === 'MEDIUM' || normalized === 'LOW' || normalized === 'UNKNOWN') {
            return normalized;
        }
        if (normalized === 'CRITICAL') {
            return 'HIGH';
        }
        if (normalized === 'WARNING' || normalized === 'DEGRADED') {
            return 'MEDIUM';
        }
        return 'UNKNOWN';
    }

    function getAlertIdentityKey(alert, index) {
        const row = ensureObject(alert);
        const preferred = String(
            row.eventId
            || row.event_id
            || row.dashboard_event_id
            || ''
        ).trim();
        if (preferred) {
            return preferred;
        }
        const domain = String(row.domain || row.site || row.site_visited || 'domain').trim();
        const timestamp = String(row.time || row.timestamp || row.observed_at_utc || '').trim();
        return `${domain}:${timestamp}:${index}`;
    }

    function initModalHandles() {
        if (!window.bootstrap || typeof window.bootstrap.Modal !== 'function') {
            return;
        }
        state.modals.isolateConfirm = createModal('isolateConfirmModal');
        state.modals.message = createModal('messageModal');
        state.modals.remoteView = createModal('remoteViewModal');
        state.modals.policyAddSite = createModal('policyAddSiteModal');
        state.modals.policyRemoveSite = createModal('policyRemoveSiteModal');
    }

    function createModal(id) {
        const node = document.getElementById(id);
        if (!node) return null;
        try {
            return new window.bootstrap.Modal(node);
        } catch (error) {
            return null;
        }
    }

    function showModal(modalKey) {
        const modal = state.modals[modalKey];
        if (modal && typeof modal.show === 'function') {
            modal.show();
            return true;
        }
        return false;
    }

    function hideModal(modalKey) {
        const modal = state.modals[modalKey];
        if (modal && typeof modal.hide === 'function') {
            modal.hide();
        }
    }

    async function loadTabDataIfNeeded(tabKey) {
        const normalized = String(tabKey || '').trim().toLowerCase();
        if (!normalized) {
            return;
        }

        if (normalized === 'history' && !state.lazyLoaded.history) {
            await loadHistorySnapshot();
            state.lazyLoaded.history = true;
            return;
        }
        if (normalized === 'website-policy') {
            await loadWebsitePolicyData(false);
            state.lazyLoaded.websitePolicy = true;
            return;
        }
        if (normalized === 'alerts') {
            await loadAlertsTabData(false);
            state.lazyLoaded.alerts = true;
        }
    }

    async function openRemoteViewModal() {
        refreshRemoteViewSnapshot();
        if (!showModal('remoteView')) {
            window.open(`/api/tracking/stream/screenshot/${encodeURIComponent(macAddress)}`, '_blank', 'noopener,noreferrer');
        }
    }

    function refreshRemoteViewSnapshot() {
        if (!dom.remoteViewImage) return;
        dom.remoteViewImage.src = `/api/tracking/stream/screenshot/${encodeURIComponent(macAddress)}?t=${Date.now()}`;
    }

    async function openRemoteViewFullscreen() {
        const imageNode = dom.remoteViewImage;
        if (!imageNode) return;
        try {
            if (document.fullscreenElement) {
                await document.exitFullscreen();
                return;
            }
            if (imageNode.requestFullscreen) {
                await imageNode.requestFullscreen();
            }
        } catch (error) {
            showInfo('Fullscreen mode is not available in this browser context.');
        }
    }

    function confirmIsolateAction() {
        hideModal('isolateConfirm');
        showInfo('Isolation endpoint is not configured yet for tracked agents.');
    }

    function submitMessageAction() {
        const message = String(dom.messageText?.value || '').trim();
        hideModal('message');
        dom.messageText && (dom.messageText.value = '');
        if (!message) {
            showInfo('Message canceled.');
            return;
        }
        showInfo('Messaging endpoint is not configured yet.');
    }

    function cacheKey(scope) {
        return `${scope}:${deviceId}`;
    }

    function invalidateDeviceConsoleCaches() {
        if (!responseCache) return;
        responseCache.invalidateMany([
            cacheKey('website-policy'),
            cacheKey('alerts'),
            cacheKey('device-summary'),
            cacheKey('risk-score'),
            cacheKey('policy-counter'),
        ]);
    }

    async function fetchWebsitePolicyEnvelope(forceReload) {
        const endpoint = `/api/devices/${encodeURIComponent(deviceId)}/website-policy`;
        const key = cacheKey('website-policy');
        if (!forceReload && responseCache && responseCache.has(key)) {
            return { payload: ensureObject(responseCache.get(key)) };
        }
        const envelope = await requestJson(endpoint, {
            method: 'GET',
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
        });
        responseCache?.set(key, ensureObject(envelope.payload), 10000);
        return envelope;
    }

    async function fetchAlertsEnvelope(forceReload) {
        const key = cacheKey('alerts');
        if (!forceReload && responseCache && responseCache.has(key)) {
            return { payload: ensureObject(responseCache.get(key)) };
        }
        let envelope;
        try {
            envelope = await requestJson(`/api/devices/${encodeURIComponent(deviceId)}/alerts`, {
                method: 'GET',
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
        } catch (error) {
            envelope = await requestJson(`/api/tracking/devices/${encodeURIComponent(deviceId)}/alerts`, {
                method: 'GET',
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
        }
        responseCache?.set(key, ensureObject(envelope.payload), 10000);
        return envelope;
    }

    function normalizeWebsitePolicyApiPayload(payload) {
        if (typeof apiNormalizer.normalizeWebsitePolicyResponse === 'function') {
            const normalized = apiNormalizer.normalizeWebsitePolicyResponse(payload);
            return {
                mode: normalized.mode || 'active',
                restrictedSites: normalized.restrictedDomains.map((row) => row.domain),
                restrictedMeta: normalized.restrictedDomains,
                violationsToday: toNumber(normalized.violationsToday, 0),
                recentViolations: normalized.recentViolations,
            };
        }

        const source = ensureObject(payload);
        const meta = Array.isArray(source.restricted_site_meta)
            ? source.restricted_site_meta
            : (Array.isArray(source.restricted_sites) ? source.restricted_sites.map((domain) => ({ domain })) : []);
        const restrictedMeta = meta.map((row) => {
            const obj = typeof row === 'object' ? row : { domain: row };
            return {
                domain: String(obj.domain || '').trim(),
                category: String(obj.category || 'Custom').trim() || 'Custom',
                reason: String(obj.reason || '').trim(),
            };
        }).filter((row) => row.domain);

        return {
            mode: String(source.mode || 'active').toLowerCase(),
            restrictedSites: restrictedMeta.map((row) => row.domain),
            restrictedMeta: restrictedMeta,
            violationsToday: Math.max(0, Math.floor(toNumber(source.violations_today, 0))),
            recentViolations: Array.isArray(source.recent_violations) ? source.recent_violations : [],
        };
    }

    function normalizeAlertsApiPayload(payload) {
        if (typeof apiNormalizer.normalizeAlertsResponse === 'function') {
            return apiNormalizer.normalizeAlertsResponse(payload);
        }
        const source = ensureObject(payload);
        const rows = Array.isArray(source.alerts) ? source.alerts : [];
        const activeCount = toNumber(
            source.active_alert_count || source.active_violation_count,
            rows.filter((alert) => normalizeViolationStatus(alert.status) !== 'resolved').length
        );
        return {
            alerts: rows,
            activeAlertCount: Math.max(0, Math.floor(activeCount)),
            riskScore: Math.max(0, Math.floor(toNumber(source.risk_score, 0))),
            riskLevel: String(source.risk_level || 'low').toLowerCase(),
        };
    }

    async function loadWebsitePolicyData(forceReload) {
        if (state.websitePolicy.loading) return;
        state.websitePolicy.loading = true;
        try {
            const envelope = await fetchWebsitePolicyEnvelope(Boolean(forceReload));
            const normalized = normalizeWebsitePolicyApiPayload(envelope.payload);
            state.websitePolicy = {
                ...state.websitePolicy,
                ...normalized,
                source: 'device_api',
                loadedAt: Date.now(),
            };
            setTabCounter('websitePolicy', Math.max(state.policy.activeViolationCount, state.websitePolicy.violationsToday));
            renderWebsitePolicyPanel(state.websitePolicy);
        } catch (error) {
            state.websitePolicy = {
                ...state.websitePolicy,
                mode: 'unavailable',
                restrictedSites: [],
                restrictedMeta: [],
                recentViolations: [],
                violationsToday: 0,
                source: 'error',
            };
            renderWebsitePolicyUnavailable();
        } finally {
            state.websitePolicy.loading = false;
        }
    }

    function renderWebsitePolicyUnavailable() {
        if (dom.policyRestrictedSitesList) {
            dom.policyRestrictedSitesList.innerHTML = `
                <div class="policy-error-card">
                    <strong>Policy data unavailable</strong>
                    <button type="button" class="tactical-btn tactical-btn-outline" data-action-retry-policy="1">Retry</button>
                </div>
            `;
        }
        if (dom.policyRecentViolationsList) {
            dom.policyRecentViolationsList.innerHTML = `
                <div class="policy-error-card">
                    <strong>Policy data unavailable</strong>
                    <button type="button" class="tactical-btn tactical-btn-outline" data-action-retry-policy="1">Retry</button>
                </div>
            `;
        }
        setText('policyModeValue', 'Unavailable');
        setText('policyRestrictedCount', '0');
        setText('policyViolationsToday', '0');
        setTabCounter('websitePolicy', state.policy.activeViolationCount);
    }

    function syncWebsitePolicyViolationsFromAlerts(alerts) {
        const rows = Array.isArray(alerts) ? alerts : [];
        const todayKey = new Date().toISOString().slice(0, 10);
        const activeCount = rows.filter((alert) => normalizeViolationStatus(alert?.status) !== 'resolved').length;
        const todayViolations = rows.filter((alert) => {
            const parsed = parseUniversalDate(alert?.time || alert?.timestamp || alert?.observed_at_utc);
            return parsed ? parsed.toISOString().slice(0, 10) === todayKey : false;
        });
        state.websitePolicy.violationsToday = todayViolations.length;
        state.websitePolicy.recentViolations = rows.slice(0, 10).map((alert) => ({
            domain: alert?.domain || alert?.site || alert?.site_visited || 'N/A',
            time: alert?.time || alert?.timestamp || alert?.observed_at_utc || null,
            severity: normalizeViolationSeverity(alert?.severity || alert?.confidence),
            status: normalizeViolationStatus(alert?.status),
            user: alert?.user || 'unknown',
            action: alert?.action || 'Blocked',
        }));
        setTabCounter('alerts', activeCount);
        setTabCounter('websitePolicy', activeCount);

        if (state.activeTab === 'website-policy') {
            renderWebsitePolicyPanel(state.websitePolicy);
        }
    }

    function renderWebsitePolicyPanel(payload) {
        const data = ensureObject(payload);
        const mode = String(data.mode || 'unavailable').toLowerCase();
        const restrictedMeta = Array.isArray(data.restrictedMeta) ? data.restrictedMeta : [];
        const recentViolations = Array.isArray(data.recentViolations) ? data.recentViolations : [];
        const violationsToday = Math.max(0, Math.floor(toNumber(data.violationsToday, 0)));

        setText('policyModeValue', mode.toUpperCase());
        setText('policyRestrictedCount', String(restrictedMeta.length));
        setText('policyViolationsToday', String(violationsToday));
        if (dom.policyModeDot) {
            dom.policyModeDot.classList.toggle('offline', mode !== 'active');
        }

        if (dom.policyRestrictedSitesList) {
            if (!restrictedMeta.length) {
                dom.policyRestrictedSitesList.innerHTML = '<div class="policy-sites-empty">No restricted domains configured</div>';
            } else {
                patchKeyedChildren(
                    dom.policyRestrictedSitesList,
                    restrictedMeta,
                    (entry) => String(entry.domain || ''),
                    'div',
                    (row, entry) => {
                        const domain = String(entry.domain || '').trim();
                        const category = String(entry.category || 'Custom').trim();
                        const checked = state.websitePolicy.selectedDomains.includes(domain);
                        row.className = 'policy-domain-pill';
                        row.innerHTML = `
                            <input type="checkbox" data-policy-domain-check="${escapeHtml(domain)}" ${checked ? 'checked' : ''} aria-label="Select ${escapeHtml(domain)}">
                            <span>${escapeHtml(domain)}</span>
                            <span class="policy-domain-meta">${escapeHtml(category)}</span>
                            <button type="button" class="policy-domain-remove" data-policy-remove="${escapeHtml(domain)}" aria-label="Remove ${escapeHtml(domain)}">✖</button>
                        `;
                    }
                );
            }
        }

        if (dom.policyRecentViolationsList) {
            if (!recentViolations.length) {
                dom.policyRecentViolationsList.innerHTML = '<div class="policy-recent-empty">No violations detected today</div>';
            } else {
                patchKeyedChildren(
                    dom.policyRecentViolationsList,
                    recentViolations,
                    (item, index) => `${item.domain || item.site || 'site'}:${item.time || index}`,
                    'div',
                    (row, item) => {
                        const domain = item.domain || item.site || 'N/A';
                        row.className = 'policy-recent-row';
                        row.innerHTML = `
                            <span class="policy-recent-time">${escapeHtml(formatClockTime(item.time))}</span>
                            <strong class="policy-recent-site">${escapeHtml(String(domain))}</strong>
                        `;
                    }
                );
            }
        }
    }

    function handlePolicyDomainCheckboxToggle(event) {
        const target = event?.target;
        const domain = String(target?.getAttribute('data-policy-domain-check') || '').trim();
        if (!domain) return;
        const selected = new Set(state.websitePolicy.selectedDomains || []);
        if (target.checked) selected.add(domain);
        else selected.delete(domain);
        state.websitePolicy.selectedDomains = Array.from(selected);
    }

    function handlePolicyDomainListClick(event) {
        const removeButton = event?.target?.closest('[data-policy-remove]');
        if (!removeButton) return;
        event.preventDefault();
        const domain = String(removeButton.getAttribute('data-policy-remove') || '').trim();
        if (!domain) return;
        void submitPolicyRemoveSite(domain);
    }

    async function submitPolicyAddSite() {
        const domain = normalizePolicyDomain(dom.policyAddSiteInput?.value);
        if (!domain) {
            showError('Enter a valid domain (example.com).');
            return;
        }
        const category = String(dom.policyAddSiteCategory?.value || 'Custom').trim() || 'Custom';
        const reason = String(dom.policyAddSiteReason?.value || '').trim();

        const run = async () => {
            setButtonBusy(dom.policyAddSiteConfirmBtn, true);
            try {
                await requestJson(`/api/devices/${encodeURIComponent(deviceId)}/website-policy`, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ domain, category, reason }),
                });
                hideModal('policyAddSite');
                if (dom.policyAddSiteInput) dom.policyAddSiteInput.value = '';
                if (dom.policyAddSiteReason) dom.policyAddSiteReason.value = '';
                invalidateDeviceConsoleCaches();
                await loadWebsitePolicyData(true);
                await refreshPolicyViolations(true);
                showInfo('Domain added to policy');
            } finally {
                setButtonBusy(dom.policyAddSiteConfirmBtn, false);
            }
        };

        if (mutationLocks && typeof mutationLocks.withLock === 'function') {
            try {
                await mutationLocks.withLock('policy:add', run);
            } catch (error) {
                if (error?.code === 'LOCKED') {
                    showInfo('Add domain request already in progress.');
                    return;
                }
                throw error;
            }
            return;
        }
        await run();
    }

    async function submitPolicyRemoveSite(singleDomain) {
        const domains = singleDomain
            ? [String(singleDomain).trim().toLowerCase()]
            : (Array.isArray(state.websitePolicy.selectedDomains) ? state.websitePolicy.selectedDomains.slice() : []);
        if (!domains.length) {
            showError('Select at least one domain to remove.');
            return;
        }

        const run = async () => {
            setButtonBusy(dom.policyRemoveSiteBtn, true);
            try {
                await requestJson(`/api/devices/${encodeURIComponent(deviceId)}/website-policy`, {
                    method: 'DELETE',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ domains }),
                });
                state.websitePolicy.selectedDomains = [];
                invalidateDeviceConsoleCaches();
                await loadWebsitePolicyData(true);
                await refreshPolicyViolations(true);
                showInfo('Policy updated');
            } finally {
                setButtonBusy(dom.policyRemoveSiteBtn, false);
            }
        };

        if (mutationLocks && typeof mutationLocks.withLock === 'function') {
            try {
                await mutationLocks.withLock('policy:remove', run);
            } catch (error) {
                if (error?.code === 'LOCKED') {
                    showInfo('Remove request already in progress.');
                    return;
                }
                throw error;
            }
            return;
        }
        await run();
    }

    function normalizePolicyDomain(value) {
        const text = String(value || '').trim().toLowerCase();
        if (!text) return '';
        const cleaned = text
            .replace(/^https?:\/\//i, '')
            .replace(/^www\./i, '')
            .split('/')[0]
            .split(':')[0]
            .trim();
        if (!/^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/i.test(cleaned)) {
            return '';
        }
        return cleaned;
    }

    function isLikelyIpv4(value) {
        return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(value || '').trim());
    }

    async function loadHistorySnapshot() {
        if (state.historyLoaded) return;
        try {
            const { response, payload } = await requestJson(`/api/tracking/history/${deviceId}/summary`, {
                method: 'GET',
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            if (!response.ok || payload.success === false) return;
            const data = ensureObject(payload.data);
            setText('historyReachability', formatPercent(data.reachability_7d));
            setText('historyConfidence', formatPercent(data.data_confidence_pct));
            setText('historySamples', String(toNumber(data.sample_count, 0)));
            setText('historyCurrentStatus', titleCase(data.current_status || 'unknown'));
            state.historyLoaded = true;
        } catch (error) {
            // Non-blocking.
        }
    }

    async function startCameraStream() {
        if (state.cameraStreaming) return;
        if (!(state.lastKnownStatus === 'online' || state.lastKnownStatus === 'degraded')) {
            showError('Camera stream is unavailable while device is offline.');
            return;
        }
        try {
            await postJson(`/api/tracking/toggle-camera/${encodeURIComponent(macAddress)}`);
            if (dom.cameraPreview) {
                dom.cameraPreview.src = `/api/tracking/stream/camera/${encodeURIComponent(macAddress)}?t=${Date.now()}`;
            }
            state.cameraStreaming = true;
            updateSurveillanceReadiness(state.lastKnownStatus);
            setCameraStates('Available', 'Active', {
                isActive: true,
                fallbackText: 'Stream inactive',
            });
            appendSurveillanceLog('camera', 'Stream started');
            switchTab('surveillance');
        } catch (error) {
            showError(error?.message || 'Failed to start camera stream.');
        }
    }

    async function stopCameraStream(silent, options) {
        const opts = options || {};
        const shouldAttemptRemoteStop = Boolean(opts.forceRemote || state.cameraStreaming);
        let remoteStopSucceeded = false;
        if (shouldAttemptRemoteStop) {
            try {
                await postJson(`/api/tracking/stop-camera/${encodeURIComponent(macAddress)}`, true);
                remoteStopSucceeded = true;
            } catch (error) {
                if (!silent) {
                    showError(error?.message || 'Failed to stop camera stream.');
                }
            }
        }
        if (dom.cameraPreview) {
            dom.cameraPreview.src = '';
            dom.cameraPreview.removeAttribute('src');
        }
        state.cameraStreaming = false;
        const reachable = state.lastKnownStatus === 'online' || state.lastKnownStatus === 'degraded';
        if (reachable) {
            setCameraStates('Available', 'Inactive', {
                isActive: false,
                fallbackText: 'Stream inactive',
            });
        } else {
            setCameraStates('Disabled', 'Disabled', {
                isActive: false,
                fallbackText: 'Webcam not available or agent not connected',
            });
        }
        updateSurveillanceReadiness(state.lastKnownStatus);
        if (!silent && (remoteStopSucceeded || !shouldAttemptRemoteStop)) {
            appendSurveillanceLog('camera', 'Stream stopped');
        } else if (!silent) {
            appendSurveillanceLog('camera', 'Camera shutdown (local)');
        }
    }

    function captureCameraSnapshot() {
        const image = dom.cameraPreview;
        const canvas = dom.captureCanvas;
        const link = dom.captureDownload;
        if (!image || !canvas || !link || !image.src) {
            showError('Camera preview is not active.');
            return;
        }
        try {
            const width = image.naturalWidth || image.width || 640;
            const height = image.naturalHeight || image.height || 480;
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(image, 0, 0, width, height);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
            link.href = dataUrl;
            link.classList.remove('d-none');
            appendSurveillanceLog('camera', 'Snapshot captured');
        } catch (error) {
            showError('Failed to capture snapshot from camera stream.');
        }
    }

    async function startMicMonitor() {
        if (state.micStreaming) return;
        if (!(state.lastKnownStatus === 'online' || state.lastKnownStatus === 'degraded')) {
            showError('Microphone monitor is unavailable while device is offline.');
            return;
        }
        try {
            await postJson(`/api/tracking/toggle-mic/${encodeURIComponent(macAddress)}`);
            if (dom.micAudio) {
                dom.micAudio.pause();
                dom.micAudio.muted = false;
                dom.micAudio.volume = getMicVolumeValue();
                setText('survMicPlaybackState', 'Connecting...');
                dom.micAudio.src = `/api/tracking/stream/audio/${encodeURIComponent(macAddress)}?t=${Date.now()}`;
                dom.micAudio.load();
                const playPromise = dom.micAudio.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch(() => {
                        setText('survMicPlaybackState', 'Playback blocked - press Play/Unmute');
                        showInfo('Browser audio policy blocked autoplay. Press Play/Unmute on the audio control.');
                    });
                }
            }
            state.micStreaming = true;
            updateSurveillanceReadiness(state.lastKnownStatus);
            setMicStates('Available', 'Active', {
                isActive: true,
                fallbackText: 'Microphone monitor inactive',
            });
            appendSurveillanceLog('mic', 'Monitor started');
            switchTab('surveillance');
        } catch (error) {
            showError(error?.message || 'Failed to start microphone monitor.');
        }
    }

    async function stopMicMonitor(silent) {
        if (!state.micStreaming) {
            const reachable = state.lastKnownStatus === 'online' || state.lastKnownStatus === 'degraded';
            if (reachable) {
                setMicStates('Available', 'Inactive', {
                    isActive: false,
                    fallbackText: 'Microphone monitor inactive',
                });
            } else {
                setMicStates('Disabled', 'Disabled', {
                    isActive: false,
                    fallbackText: 'Microphone monitoring not available or agent not connected',
                });
            }
            updateSurveillanceReadiness(state.lastKnownStatus);
            return;
        }

        let remoteStopSucceeded = false;
        try {
            await postJson(`/api/tracking/toggle-mic/${encodeURIComponent(macAddress)}`, true);
            remoteStopSucceeded = true;
        } catch (error) {
            if (!silent) {
                showError(error?.message || 'Failed to stop microphone monitor.');
            }
        } finally {
            if (dom.micAudio) {
                dom.micAudio.pause();
                dom.micAudio.src = '';
                dom.micAudio.load();
            }
            setText('survMicPlaybackState', 'Microphone monitor inactive');
            state.micStreaming = false;
            const reachable = state.lastKnownStatus === 'online' || state.lastKnownStatus === 'degraded';
            if (reachable) {
                setMicStates('Available', 'Inactive', {
                    isActive: false,
                    fallbackText: 'Microphone monitor inactive',
                });
            } else {
                setMicStates('Disabled', 'Disabled', {
                    isActive: false,
                    fallbackText: 'Microphone monitoring not available or agent not connected',
                });
            }
            updateSurveillanceReadiness(state.lastKnownStatus);
            if (!silent) {
                appendSurveillanceLog('mic', remoteStopSucceeded ? 'Monitor stopped' : 'Monitor stopped (local)');
            }
        }
    }

    async function postJson(url, keepalive) {
        const { payload } = await requestJson(url, {
            method: 'POST',
            headers: { Accept: 'application/json' },
            keepalive: Boolean(keepalive),
            credentials: 'same-origin',
        });
        return payload;
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
            const forbiddenError = new Error('Access denied for this action.');
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

    function extractNetworkMetrics(trackingData) {
        const systemMetrics = ensureObject(trackingData.system_metrics);
        const candidate = ensureObject(trackingData.network || systemMetrics.network_speed);
        return {
            uploadKbps: toNumber(candidate.upload_speed_kbps ?? candidate.upload_kbps ?? candidate.upload, 0),
            downloadKbps: toNumber(candidate.download_speed_kbps ?? candidate.download_kbps ?? candidate.download, 0),
            uploadMb: toNumber(candidate.total_upload_mb ?? candidate.upload_mb, 0),
            downloadMb: toNumber(candidate.total_download_mb ?? candidate.download_mb, 0),
        };
    }

    function resolveActiveApp(activity, systemMetrics, todayStats) {
        if (activity.active_application) return String(activity.active_application);
        if (systemMetrics.active_window && systemMetrics.active_window.process) return String(systemMetrics.active_window.process);
        const apps = Array.isArray(todayStats.applications_used) ? todayStats.applications_used : [];
        if (!apps.length) return '';
        const candidate = apps[apps.length - 1];
        if (candidate && typeof candidate === 'object') return String(candidate.application_name || candidate.name || 'Unknown');
        return String(candidate);
    }

    function resolveActiveWindow(systemMetrics, activity) {
        if (systemMetrics.active_window && systemMetrics.active_window.title) return String(systemMetrics.active_window.title);
        if (activity.active_window_title) return String(activity.active_window_title);
        return '';
    }

    function resolveAgentState(status, syncAgeSeconds, lastSyncIso) {
        if (!lastSyncIso) return 'NEVER_SEEN';
        if (status === 'offline') return 'OFFLINE';
        if (!Number.isFinite(syncAgeSeconds)) return 'DELAYED';
        if (syncAgeSeconds <= 180) return 'CONNECTED';
        if (syncAgeSeconds <= 600) return 'DELAYED';
        return 'OFFLINE';
    }

    function resolveAgentHealthLabel(status, hasTelemetry, syncAgeSeconds, lastSyncIso) {
        if (!lastSyncIso) return 'Awaiting telemetry';
        if (status === 'offline') return 'Unreachable';
        if (!hasTelemetry) return 'Awaiting telemetry';
        if (!Number.isFinite(syncAgeSeconds)) return 'Unknown';
        if (syncAgeSeconds <= 180) return 'Heartbeat OK';
        if (syncAgeSeconds <= 600) return 'Delayed';
        return 'Stale';
    }

    function sanitizeDisplayIp(primaryIp, syncIp) {
        const ip = String(primaryIp || '').trim();
        const lastSyncIp = String(syncIp || '').trim();
        if (ip.startsWith('127.') && lastSyncIp) return lastSyncIp;
        return ip || lastSyncIp || '';
    }

    function hasTelemetrySnapshot(snapshot, trackingData) {
        if (snapshot.metricsAvailable) return true;
        const systemMetrics = ensureObject(trackingData.system_metrics);
        const activity = ensureObject(trackingData.current_activity);
        const todayStats = ensureObject(trackingData.today_stats);
        return Boolean(
            Object.keys(systemMetrics).length ||
            Object.keys(activity).length ||
            Object.keys(todayStats).length
        );
    }

    function deriveStatusReason(status, probeErrorCode, hasTelemetry, lastSyncIso) {
        const code = String(probeErrorCode || '').trim();
        if (status === 'degraded') {
            if (code) return `Degraded: ${titleCase(code.replace(/_/g, ' '))}`;
            if (!hasTelemetry) return 'Agent reachable; telemetry pending';
            return 'Partial telemetry received';
        }
        if (status === 'offline') {
            if (code) return `Offline: ${titleCase(code.replace(/_/g, ' '))}`;
            if (!lastSyncIso) return 'No heartbeat received yet';
            return 'Agent unreachable';
        }
        if (!hasTelemetry) return 'Connected; waiting for telemetry';
        return 'Telemetry active';
    }

    function pushSeriesValue(key, value) {
        const bucket = state.series[key];
        if (!Array.isArray(bucket)) return;
        bucket.push(toNumber(value, 0));
        if (bucket.length > 40) bucket.shift();
    }

    function getChartPalette() {
        return {
            healthy: readCssVar('--s-healthy', 'rgb(32, 201, 151)'),
            warning: readCssVar('--s-warning', 'rgb(255, 193, 7)'),
            info: readCssVar('--e-text-secondary', 'rgb(195, 207, 219)'),
        };
    }

    function readCssVar(name, fallback) {
        const root = document.querySelector('.dashboard-enterprise') || document.documentElement;
        const value = window.getComputedStyle(root).getPropertyValue(name);
        const normalized = String(value || '').trim();
        return normalized || fallback;
    }

    function patchKeyedChildren(container, items, keyGetter, tagName, patchFn) {
        if (!container) return;
        const existing = new Map();
        Array.from(container.children).forEach((child) => {
            const key = child.getAttribute('data-row-key');
            if (key) {
                existing.set(key, child);
            }
        });

        const nextNodes = [];
        items.forEach((item, index) => {
            const key = String(keyGetter(item, index));
            let node = existing.get(key);
            if (!node) {
                node = document.createElement(tagName);
                node.setAttribute('data-row-key', key);
            }
            patchFn(node, item, index);
            nextNodes.push(node);
            existing.delete(key);
        });

        existing.forEach((node) => node.remove());
        nextNodes.forEach((node, index) => {
            const anchor = container.children[index] || null;
            if (anchor !== node) {
                container.insertBefore(node, anchor);
            }
        });
    }

    function setTrendIndicator(elementId, values) {
        const node = document.getElementById(elementId);
        if (!node || !Array.isArray(values) || values.length < 2) {
            if (node) node.textContent = '-';
            return;
        }
        const latest = values[values.length - 1];
        const previous = values[values.length - 2];
        const delta = latest - previous;
        node.classList.remove('trend-up', 'trend-down');
        if (Math.abs(delta) < 0.3) {
            node.textContent = '->';
            return;
        }
        if (delta > 0) {
            node.textContent = String.fromCharCode(0x2191);
            node.classList.add('trend-up');
            return;
        }
        node.textContent = String.fromCharCode(0x2193);
        node.classList.add('trend-down');
    }

    function setTrend(elementId, values) {
        const node = document.getElementById(elementId);
        if (!node || !Array.isArray(values) || values.length < 2) {
            if (node) node.textContent = '-';
            return;
        }
        const latest = values[values.length - 1];
        const previous = values[values.length - 2];
        const delta = latest - previous;
        node.classList.remove('trend-up', 'trend-down');
        if (Math.abs(delta) < 0.3) {
            node.textContent = '->';
            return;
        }
        if (delta > 0) {
            node.textContent = String.fromCharCode(0x2191);
            node.classList.add('trend-up');
        } else {
            node.textContent = String.fromCharCode(0x2193);
            node.classList.add('trend-down');
        }
    }

    function setThresholdClass(elementId, value) {
        const node = document.getElementById(elementId);
        if (!node) return;
        node.classList.remove('metric-critical', 'metric-warning', 'metric-ok');
        const numeric = toNumber(value, 0);
        if (numeric >= 80) node.classList.add('metric-critical');
        else if (numeric >= 60) node.classList.add('metric-warning');
        else node.classList.add('metric-ok');
    }

    function renderSparkline(svgId, values, color, width, height) {
        const svg = document.getElementById(svgId);
        if (!svg) return;
        const chartWidth = width || 240;
        const chartHeight = height || 60;
        const points = Array.isArray(values) ? values : [];
        while (svg.firstChild) {
            svg.removeChild(svg.firstChild);
        }
        if (points.length < 2) return;

        const max = Math.max(...points, 1);
        const min = Math.min(...points, 0);
        const range = Math.max(max - min, 1);
        const step = chartWidth / (points.length - 1);
        const pathData = points.map((value, index) => {
            const x = index * step;
            const y = chartHeight - ((value - min) / range) * (chartHeight - 4) - 2;
            return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
        }).join(' ');

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', pathData);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', color);
        path.setAttribute('stroke-width', '1.6');
        path.setAttribute('stroke-linecap', 'round');
        svg.appendChild(path);
    }

    function setBadgeStatus(status) {
        if (!dom.statusBadge) return;
        const normalized = normalizeStatus(status);
        dom.statusBadge.textContent = normalized.toUpperCase();
        dom.statusBadge.className = `tactical-badge ${statusBadgeClass(normalized)}`;
    }

    function setAgentStateBadge(stateLabel) {
        const label = String(stateLabel || 'NEVER_SEEN').toUpperCase();
        const pretty = titleCase(label.replace('_', ' '));
        const node = document.getElementById('metaAgentState');
        if (!node) return;
        node.textContent = pretty;
        node.classList.remove('agent-state-connected', 'agent-state-delayed', 'agent-state-offline', 'agent-state-never');
        if (label === 'CONNECTED') node.classList.add('agent-state-connected');
        else if (label === 'DELAYED') node.classList.add('agent-state-delayed');
        else if (label === 'OFFLINE') node.classList.add('agent-state-offline');
        else node.classList.add('agent-state-never');
    }

    function setHeaderRiskBadge(level) {
        if (!dom.riskBadge) return;
        const normalized = String(level || 'LOW').toUpperCase();
        dom.riskBadge.textContent = `Risk: ${normalized}`;
        dom.riskBadge.className = 'tactical-badge';
        if (normalized === 'HIGH') dom.riskBadge.classList.add('tactical-badge-danger');
        else if (normalized === 'MEDIUM') dom.riskBadge.classList.add('tactical-badge-warning');
        else if (normalized === 'UNKNOWN') dom.riskBadge.classList.add('tactical-badge-secondary');
        else dom.riskBadge.classList.add('tactical-badge-success');
    }

    function setPolicyBadge(status, domain, activeCount) {
        if (!dom.policyBadge) return;
        const normalized = String(status || 'compliant').toLowerCase();
        const hasViolation = normalized === 'violating';
        const count = Math.max(0, Math.floor(toNumber(activeCount, 0)));
        dom.policyBadge.textContent = hasViolation
            ? `Policy: VIOLATING${count > 0 ? ` (${count})` : ''}`
            : 'Policy: COMPLIANT';
        dom.policyBadge.className = `tactical-badge ${hasViolation ? 'tactical-badge-danger' : 'tactical-badge-success'}`;
        if (hasViolation) {
            dom.policyBadge.title = domain
                ? `Latest restricted site: ${domain}`
                : 'Restricted site policy is currently violated';
        } else {
            dom.policyBadge.title = 'No active policy violations';
        }
    }

    function updateSurveillanceReadiness(status) {
        if (!dom.surveillanceStateBadge) return;
        const normalized = normalizeStatus(status);
        const streamActive = state.cameraStreaming || state.micStreaming;
        const isReady = normalized === 'online' || normalized === 'degraded' || streamActive;
        dom.surveillanceStateBadge.classList.toggle('state-ready', isReady);
        dom.surveillanceStateBadge.classList.toggle('state-offline', !isReady);
        if (dom.surveillanceStateText) {
            dom.surveillanceStateText.textContent = streamActive
                ? 'Surveillance Active'
                : (isReady ? 'Surveillance Ready' : 'Device Offline');
        }
    }

    function setSurveillanceBadgeState(node, stateName) {
        if (!node) return;
        node.classList.remove('status-good', 'status-warn', 'status-bad', 'status-neutral');
        node.classList.add(`status-${stateName}`);
    }

    function resolveCapabilityBadgeState(value) {
        const normalized = String(value || '').trim().toLowerCase();
        if (normalized === 'available') return 'good';
        if (normalized === 'disabled') return 'bad';
        return 'neutral';
    }

    function resolveOperationalBadgeState(value) {
        const normalized = String(value || '').trim().toLowerCase();
        if (normalized === 'active' || normalized === 'in use') return 'warn';
        if (normalized === 'inactive' || normalized === 'available') return 'good';
        if (normalized === 'disabled') return 'bad';
        return 'neutral';
    }

    function appendSurveillanceLog(channel, message) {
        const container = channel === 'camera' ? dom.cameraLog : dom.micLog;
        if (!container) return;

        const emptyNode = container.querySelector('.surveillance-log-empty');
        if (emptyNode) {
            emptyNode.remove();
        }

        const entry = document.createElement('div');
        entry.className = 'surveillance-log-entry';
        entry.innerHTML = `
            <span class="surveillance-log-time">${escapeHtml(formatClockTime(new Date()))}</span>
            <span class="surveillance-log-message">${escapeHtml(String(message || 'Updated'))}</span>
        `;
        container.insertBefore(entry, container.firstChild);

        const rows = Array.from(container.querySelectorAll('.surveillance-log-entry'));
        rows.slice(5).forEach((row) => row.remove());
    }

    function formatClockTime(dateValue) {
        const parsed = parseUniversalDate(dateValue);
        if (!parsed) {
            const raw = String(dateValue || '').trim();
            if (/^\d{1,2}:\d{2}(\s?[APMapm]{2})?$/.test(raw)) {
                return raw.toUpperCase();
            }
            return '--:--';
        }
        return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function setCameraStates(headerText, panelText, options) {
        const opts = options || {};
        const isActive = Boolean(opts.isActive);
        const fallbackText = String(opts.fallbackText || 'Webcam not available or agent not connected');
        const capabilityState = resolveCapabilityBadgeState(headerText);
        const statusState = resolveOperationalBadgeState(panelText);
        const capabilityAvailable = capabilityState === 'good';
        setText('survCameraHeaderState', headerText);
        setText('survCameraState', panelText);
        setSurveillanceBadgeState(dom.cameraCapabilityBadge, capabilityState);
        setSurveillanceBadgeState(dom.cameraStatusBadge, statusState);

        if (dom.cameraStartBtn) {
            dom.cameraStartBtn.classList.toggle('d-none', isActive || !capabilityAvailable);
        }
        if (dom.cameraStopBtn) {
            dom.cameraStopBtn.classList.toggle('d-none', !isActive || !capabilityAvailable);
        }
        if (dom.cameraCaptureBtn) {
            dom.cameraCaptureBtn.disabled = !capabilityAvailable;
        }
        if (dom.cameraPreviewWrap) {
            dom.cameraPreviewWrap.classList.toggle('active', isActive);
            dom.cameraPreviewWrap.classList.toggle('inactive', !isActive);
        }
        if (dom.cameraPreview) {
            dom.cameraPreview.classList.toggle('d-none', !isActive);
        }
        if (dom.cameraFallback) {
            dom.cameraFallback.classList.toggle('d-none', isActive);
        }
        if (dom.cameraFallbackText) {
            dom.cameraFallbackText.textContent = fallbackText;
        }
        if (dom.cameraRecordingIndicator) {
            dom.cameraRecordingIndicator.classList.toggle('d-none', !isActive);
        }
        if (dom.cameraFullscreenBtn) {
            dom.cameraFullscreenBtn.classList.toggle('d-none', !isActive);
            dom.cameraFullscreenBtn.disabled = !isActive;
        }
        if (!isActive && dom.captureDownload) {
            dom.captureDownload.classList.add('d-none');
        }
    }

    function setMicStates(headerText, panelText, options) {
        const opts = options || {};
        const isActive = Boolean(opts.isActive);
        const fallbackText = String(opts.fallbackText || 'Microphone monitoring not available or agent not connected');
        const capabilityState = resolveCapabilityBadgeState(headerText);
        const statusState = resolveOperationalBadgeState(panelText);
        const capabilityAvailable = capabilityState === 'good';
        setText('survMicHeaderState', headerText);
        setText('survMicState', panelText);
        setSurveillanceBadgeState(dom.micCapabilityBadge, capabilityState);
        setSurveillanceBadgeState(dom.micStatusBadge, statusState);

        if (dom.micStartBtn) {
            dom.micStartBtn.classList.toggle('d-none', isActive || !capabilityAvailable);
        }
        if (dom.micStopBtn) {
            dom.micStopBtn.classList.toggle('d-none', !isActive || !capabilityAvailable);
        }
        if (dom.micAudioShell) {
            dom.micAudioShell.classList.toggle('d-none', !isActive);
        }
        if (dom.micFallback) {
            dom.micFallback.classList.toggle('d-none', isActive);
        }
        if (dom.micFallbackText) {
            dom.micFallbackText.textContent = fallbackText;
        }
        if (dom.micLevelMeter) {
            dom.micLevelMeter.classList.toggle('active', isActive);
        }
        if (isActive) {
            applyMicVolumeSetting();
        }
        if (!isActive) {
            setText('survMicPlaybackState', fallbackText);
        }
    }

    function getMicVolumeValue() {
        const slider = dom.micVolume;
        const sliderValue = slider ? Number(slider.value) : 100;
        const bounded = Number.isFinite(sliderValue) ? Math.max(0, Math.min(100, sliderValue)) : 100;
        return bounded / 100;
    }

    function applyMicVolumeSetting() {
        if (!dom.micAudio) {
            return;
        }
        const volume = getMicVolumeValue();
        dom.micAudio.volume = volume;
        dom.micAudio.muted = volume <= 0;
    }

    function setStatusReason(reasonText) {
        const node = document.getElementById('metaStatusReason');
        if (!node) return;
        const text = String(reasonText || 'Unknown status');
        node.textContent = text;
        node.setAttribute('title', text);
    }

    function showTelemetryBanner(show, lastHeartbeatIso) {
        if (!dom.telemetryBanner) return;
        dom.telemetryBanner.classList.toggle('d-none', !show);
        if (dom.telemetryHeartbeat) {
            dom.telemetryHeartbeat.textContent = `Last heartbeat: ${formatRelativeFromIso(lastHeartbeatIso)}`;
        }
        if (dom.telemetryPoll) {
            dom.telemetryPoll.textContent = `Polling every ${Math.round(pollMs / 1000)}s`;
        }
    }

    function setAgentAwaitingVisibility(isVisible) {
        if (!dom.agentHealthAwaiting) return;
        dom.agentHealthAwaiting.classList.toggle('d-none', !isVisible);
    }

    function applyAwaitingTelemetryState(status, probeErrorCode, awaitingFirstTelemetry) {
        setText('overviewCpu', '--');
        setText('overviewRam', '--');
        setText('overviewDisk', '--');
        setText('overviewUpload', '--');
        setText('overviewDownload', '--');
        setText('overviewIdle', '--');
        setText('activityIdleCompact', '--');
        setText('overviewActiveApp', 'Awaiting activity data');
        setText('overviewWindowTitle', 'Awaiting activity data');
        setText('overviewKeyboardState', 'Awaiting');
        setText('overviewMouseState', 'Awaiting');
        setText('overviewActiveTime', '--');
        setText('overviewTotalTime', '--');
        setText('overviewAppCount', '--');
        setText('overviewLastPoll', awaitingFirstTelemetry ? 'Awaiting first telemetry sample' : 'Awaiting telemetry data');

        setText('activityKeyboardCount', '--');
        setText('activityMouseCount', '--');
        setText('activityScrollCount', '--');
        setText('activityIdleDuration', '--');
        setText('activityFocusedApp', 'Awaiting activity data');
        setText('activityFocusedWindow', 'Awaiting activity data');
        setText('activityFocusChanged', 'Awaiting activity data');
        setText('activityConfidence', 'Pending');

        setText('networkUpload', '--');
        setText('networkDownload', '--');
        setText('networkUploadTotal', '--');
        setText('networkDownloadTotal', '--');

        patchKeyedChildren(document.getElementById('processTableBody'), [], () => '', 'tr', () => {});
        setTabCounter('processes', 0);
        patchKeyedChildren(
            document.getElementById('networkConsumersList'),
            [{ id: 'empty', text: 'Awaiting telemetry data.' }],
            (row) => row.id,
            'div',
            (node, row) => {
                node.className = 'device-info-row';
                node.innerHTML = `<span>N/A</span><strong>${escapeHtml(row.text)}</strong>`;
            }
        );
        patchKeyedChildren(
            document.getElementById('alertsFeedList'),
            [{ id: 'awaiting', text: `Awaiting telemetry data${probeErrorCode ? ` (${probeErrorCode})` : ''}.` }],
            (row) => row.id,
            'div',
            (node, row) => {
                node.className = 'device-info-row';
                node.innerHTML = `<span>-</span><strong>${escapeHtml(row.text)}</strong>`;
            }
        );
        document.getElementById('processEmptyState')?.classList.remove('d-none');

        setText('chartCpuMeta', 'Awaiting telemetry');
        setText('chartRamMeta', 'Awaiting telemetry');
        setText('chartNetworkMeta', 'Awaiting telemetry');
        setText('overviewCpuTrend', '-');
        setText('overviewRamTrend', '-');
        document.getElementById('overviewCpu')?.classList.remove('metric-critical', 'metric-warning', 'metric-ok');
        document.getElementById('overviewRam')?.classList.remove('metric-critical', 'metric-warning', 'metric-ok');
        setText('alertsRiskScore', status === 'offline' ? 'HIGH' : 'UNKNOWN');
        const riskNode = document.getElementById('alertsRiskScore');
        riskNode?.classList.remove('risk-high', 'risk-medium', 'risk-low', 'risk-unknown');
        if (riskNode) {
            if (status === 'offline') riskNode.classList.add('risk-high');
            else riskNode.classList.add('risk-unknown');
        }
        setText('alertsRiskContext', awaitingFirstTelemetry ? 'Waiting for first telemetry sample' : 'Telemetry not yet available');
        state.baseRiskLevel = status === 'offline' ? 'HIGH' : 'UNKNOWN';
        setHeaderRiskBadge(state.baseRiskLevel);
        reconcileGlobalDeviceState({
            connectivity: normalizeStatus(status),
            telemetry: status === 'offline' ? 'offline' : 'partial',
            policyViolations: state.policy.activeViolationCount,
            riskLevel: status === 'offline' ? 'high' : 'medium',
            riskScore: status === 'offline' ? 90 : 45,
        });
    }

    function updatePollMeta() {
        if (!dom.pollMeta) return;
        const pollSeconds = Math.round(pollMs / 1000);
        const latencyLabel = Number.isFinite(state.lastPollDurationMs)
            ? `${Math.round(state.lastPollDurationMs)} ms`
            : '-- ms';
        if (!state.lastPollTs) {
            dom.pollMeta.textContent = `Polling ${pollSeconds}s - Latency ${latencyLabel}`;
            setText('telemetryStatusTitle', 'LIVE TELEMETRY');
            if (dom.pollDot) dom.pollDot.classList.remove('live');
            return;
        }
        const age = Math.max(0, Math.floor((Date.now() - state.lastPollTs) / 1000));
        dom.pollMeta.textContent = `Polling ${pollSeconds}s - Latency ${latencyLabel} - ${age}s ago`;
        const telemetry = typeof telemetryStateApi.deriveTelemetryState === 'function'
            ? telemetryStateApi.deriveTelemetryState({
                latencyMs: toNumber(state.lastPollDurationMs, 0),
                heartbeatAgeSeconds: age,
                pollSeconds: pollSeconds,
                hasResponse: true,
            })
            : { state: age <= pollSeconds * 2 ? 'healthy' : 'degraded', label: age <= pollSeconds * 2 ? 'LIVE TELEMETRY' : 'TELEMETRY DELAYED' };
        if (dom.pollDot) {
            dom.pollDot.classList.toggle('live', telemetry.state === 'healthy');
        }
        if (dom.telemetryIndicator) {
            dom.telemetryIndicator.classList.remove('state-healthy', 'state-degraded', 'state-critical', 'state-offline');
            const className = telemetry.state === 'healthy'
                ? 'state-healthy'
                : (telemetry.state === 'offline' ? 'state-offline' : (telemetry.state === 'critical' ? 'state-critical' : 'state-degraded'));
            dom.telemetryIndicator.classList.add(className);
        }
        setText('telemetryStatusTitle', telemetry.label || 'LIVE TELEMETRY');
        reconcileGlobalDeviceState({
            connectivity: state.lastKnownStatus,
            telemetry: telemetry.state,
            policyViolations: state.policy.activeViolationCount,
            riskLevel: state.deviceState.risk,
            riskScore: state.deviceState.risk_score,
        });
    }

    function statusBadgeClass(status) {
        if (status === 'online') return 'tactical-badge-success';
        if (status === 'degraded') return 'tactical-badge-warning';
        return 'tactical-badge-secondary';
    }

    function normalizeStatus(value) {
        const status = String(value || 'offline').toLowerCase();
        if (status === 'online' || status === 'degraded' || status === 'offline') return status;
        return 'offline';
    }

    function showError(message) {
        showBanner(message, 'danger');
    }

    function showInfo(message) {
        showToast(message, 'info');
    }

    function showToast(message, level) {
        const container = dom.toastRoot;
        const text = String(message || 'Update');
        const nowTs = Date.now();
        if (state.lastToast.message === text && (nowTs - state.lastToast.at) < 1200) {
            return;
        }
        state.lastToast.message = text;
        state.lastToast.at = nowTs;
        if (!container) {
            showBanner(text, level || 'info', 2400);
            return;
        }
        const variant = String(level || 'info').toLowerCase();
        const toast = document.createElement('div');
        toast.className = `device-toast toast-${variant}`;
        toast.innerHTML = `
            <span>${escapeHtml(text)}</span>
            <button type="button" aria-label="Dismiss">&times;</button>
        `;
        const closeBtn = toast.querySelector('button');
        closeBtn?.addEventListener('click', () => toast.remove());
        container.appendChild(toast);
        window.setTimeout(() => {
            toast.classList.add('fade-out');
            window.setTimeout(() => toast.remove(), 220);
        }, 2600);
    }

    function showBanner(message, level, autoHideMs) {
        if (!dom.errorBanner) return;
        const variant = String(level || 'danger').toLowerCase();
        dom.errorBanner.textContent = message;
        dom.errorBanner.classList.remove('d-none', 'alert-danger', 'alert-warning', 'alert-info', 'alert-success');
        dom.errorBanner.classList.add(`alert-${variant}`);
        if (Number.isFinite(autoHideMs) && autoHideMs > 0) {
            window.setTimeout(clearError, autoHideMs);
        }
    }

    function clearError() {
        if (!dom.errorBanner) return;
        dom.errorBanner.textContent = '';
        dom.errorBanner.classList.remove('alert-warning', 'alert-info', 'alert-success');
        dom.errorBanner.classList.add('alert-danger');
        dom.errorBanner.classList.add('d-none');
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

    function ageSecondsFromIso(isoValue) {
        const parsed = parseUniversalDate(isoValue);
        if (!parsed) return NaN;
        const ts = parsed.getTime();
        return Math.max(0, Math.floor((Date.now() - ts) / 1000));
    }

    function formatTimestamp(value) {
        const parsed = parseUniversalDate(value);
        if (!parsed) return 'Never';
        return parsed.toLocaleString();
    }

    function setLastSeenDisplay(value) {
        const node = document.getElementById('metaLastSeen');
        if (!node) return;
        const parsed = parseUniversalDate(value);
        if (!parsed) {
            node.textContent = 'Never';
            node.removeAttribute('title');
            return;
        }
        node.textContent = parsed.toLocaleString();
        node.title = formatRelativeFromIso(parsed);
    }

    function formatRelativeFromIso(value) {
        const parsed = parseUniversalDate(value);
        if (!parsed) return 'Never';
        const ageSeconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
        if (ageSeconds < 60) return `${ageSeconds}s ago`;
        const minutes = Math.floor(ageSeconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    }

    function formatDuration(totalSeconds) {
        const seconds = Math.max(0, Math.floor(toNumber(totalSeconds, 0)));
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        return `${hours}h ${minutes}m`;
    }

    function applyDailyUptimeSnapshot(payload) {
        const daily = ensureObject(payload);
        const uptimePercent = toNumber(daily.uptime_percent, NaN);
        const onlineSeconds = toNumber(daily.online_seconds, NaN);
        const downtimeSeconds = toNumber(daily.downtime_seconds, NaN);

        const uptimeLabel = daily.uptime_display || (Number.isFinite(uptimePercent) ? `${toFixed(uptimePercent, 1)}%` : 'N/A');
        const onlineLabel = daily.online_display || (Number.isFinite(onlineSeconds) ? formatDuration(onlineSeconds) : 'N/A');
        const downtimeLabel = daily.downtime_display || (Number.isFinite(downtimeSeconds) ? formatDuration(downtimeSeconds) : 'N/A');

        setText('metaTotalUptime', uptimeLabel);
        setText('metaUptime', onlineLabel);
        setText('metaDowntime', downtimeLabel);
    }

    function formatSpeed(kbps) {
        const value = toNumber(kbps, 0);
        if (value >= 1024) return `${toFixed(value / 1024, 2)} MB/s`;
        return `${toFixed(value, 1)} KB/s`;
    }

    function formatPercent(value) {
        const numeric = toNumber(value, NaN);
        if (!Number.isFinite(numeric)) return 'N/A';
        return `${toFixed(numeric, 1)}%`;
    }

    function titleCase(text) {
        return String(text || '')
            .split(/[\s_]+/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
            .join(' ');
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    }

    function toFixed(value, digits) {
        const parsed = toNumber(value, 0);
        return parsed.toFixed(digits);
    }

    function toNumber(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function ensureObject(value) {
        return value && typeof value === 'object' ? value : {};
    }

    function escapeHtml(input) {
        return String(input || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
})();
