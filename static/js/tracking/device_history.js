(function () {
    'use strict';

    const config = window.TRACKING_HISTORY_CONFIG || {};
    const deviceId = Number(config.deviceId || 0);
    if (!deviceId) {
        return;
    }

    const SUSPICIOUS_APP_PATTERNS = [/keylogger/i, /remote/i, /miner/i, /rat/i, /inject/i];
    const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local';
    const historyLoadingApi = window.__UI_SURFACE_FLAGS__?.sharedLoading !== false && window.UI?.Loading
        ? window.UI.Loading
        : null;

    const state = {
        days: Number(config.defaultDays || 7),
        cursors: {
            activity: null,
            resources: null,
            applications: null,
            integrity: null,
        },
        datasets: {
            activity: [],
            resources: [],
            applications: [],
            integrity: [],
            policy: [],
        },
        dashboard: null,
    };

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        setText('tzContext', `Displaying: Local (${localTimezone})`);
        applyInitialFocus();
        reloadAll();
    });

    function bindEvents() {
        const range = document.getElementById('historyRange');
        if (range) {
            range.addEventListener('change', () => {
                const parsed = Number(range.value || 7);
                state.days = Number.isFinite(parsed) && parsed > 0 ? parsed : 7;
                resetState();
                reloadAll();
            });
        }

        bindLoadMore('activityLoadMore', () => loadActivity(true));
        bindLoadMore('resourcesLoadMore', () => loadResources(true));
        bindLoadMore('applicationsLoadMore', () => loadApplications(true));
        bindLoadMore('integrityLoadMore', () => loadIntegrity(true));

        bindClick('historyRefreshBtn', refreshAll);
        bindClick('historyRetryBtn', refreshAll);
        bindClick('actionRefresh', refreshAll);
        bindClick('actionRunIntegrity', runIntegrityCheck);
        bindClick('actionArchive', archiveDevice);
        bindClick('actionAssignScope', () => {
            window.location.href = '/tracking';
        });
    }

    function applyInitialFocus() {
        const focus = String(config.initialFocus || '').trim().toLowerCase();
        if (focus !== 'policy') {
            return;
        }
        const policyTabBtn = document.querySelector('[data-bs-target="#policyTab"]');
        if (policyTabBtn && window.bootstrap && typeof window.bootstrap.Tab === 'function') {
            const tab = window.bootstrap.Tab.getOrCreateInstance(policyTabBtn);
            tab.show();
        }
    }

    function bindClick(id, handler) {
        const node = document.getElementById(id);
        if (node) {
            node.addEventListener('click', handler);
        }
    }

    function bindLoadMore(id, callback) {
        const button = document.getElementById(id);
        if (button) {
            button.addEventListener('click', callback);
        }
    }

    function refreshAll() {
        resetState();
        reloadAll();
    }

    function resetState() {
        state.cursors.activity = null;
        state.cursors.resources = null;
        state.cursors.applications = null;
        state.cursors.integrity = null;
        state.datasets.activity = [];
        state.datasets.resources = [];
        state.datasets.applications = [];
        state.datasets.integrity = [];
        state.datasets.policy = [];
        state.dashboard = null;
        clearTables();
    }

    function clearTables() {
        ['activityBody', 'resourcesBody', 'applicationsBody', 'integrityBody', 'policyBody'].forEach((id) => {
            const body = document.getElementById(id);
            if (body) {
                body.innerHTML = '';
            }
        });
    }

    async function reloadAll() {
        hideError();
        setLoading(true);
        try {
            await loadDashboard();
            await Promise.all([
                loadActivity(false),
                loadResources(false),
                loadApplications(false),
                loadIntegrity(false),
                loadPolicy(),
            ]);
            renderInsights();
        } catch (error) {
            showError(error.message || 'Failed to refresh history data.');
        } finally {
            setLoading(false);
        }
    }

    function setLoading(isLoading) {
        const node = document.getElementById('historyLoading');
        if (node) {
            if (isLoading && historyLoadingApi) {
                historyLoadingApi.setRegionState(node, {
                    state: 'loading',
                    title: 'Loading history data',
                    detail: 'Pulling activity, resource, integrity, and policy data.',
                    compact: true,
                });
            }
            node.classList.toggle('d-none', !isLoading);
        }
    }

    function buildWindowQuery() {
        const to = new Date();
        const from = new Date(to.getTime() - (state.days * 24 * 60 * 60 * 1000));
        const params = new URLSearchParams();
        params.set('from', from.toISOString());
        params.set('to', to.toISOString());
        params.set('tz', 'LOCAL');
        return params;
    }

    async function loadDashboard() {
        const params = buildWindowQuery();
        const payload = await requestJson(`/api/tracking/history/${deviceId}/dashboard?${params.toString()}`);
        state.dashboard = payload.data || {};
        renderDashboard(payload, state.dashboard);
    }

    async function loadActivity(loadMore) {
        const params = buildWindowQuery();
        params.set('limit', '100');
        if (loadMore && state.cursors.activity) {
            params.set('cursor', state.cursors.activity);
        }
        const payload = await requestJson(`/api/tracking/history/${deviceId}/activity?${params.toString()}`);
        const rows = Array.isArray(payload.data) ? payload.data : [];
        state.datasets.activity = loadMore ? state.datasets.activity.concat(rows) : rows;
        renderActivityRows(rows, !loadMore);
        state.cursors.activity = payload.next_cursor || null;
        toggleLoadMore('activityLoadMore', Boolean(payload.next_cursor));
    }

    async function loadResources(loadMore) {
        const params = buildWindowQuery();
        params.set('limit', '100');
        params.set('bucket', 'raw');
        if (loadMore && state.cursors.resources) {
            params.set('cursor', state.cursors.resources);
        }
        const payload = await requestJson(`/api/tracking/history/${deviceId}/resources?${params.toString()}`);
        const rows = Array.isArray(payload.data) ? payload.data : [];
        state.datasets.resources = loadMore ? state.datasets.resources.concat(rows) : rows;
        renderResourceRows(rows, !loadMore);
        state.cursors.resources = payload.next_cursor || null;
        toggleLoadMore('resourcesLoadMore', Boolean(payload.next_cursor));
    }

    async function loadApplications(loadMore) {
        const params = buildWindowQuery();
        params.set('limit', '100');
        if (loadMore && state.cursors.applications) {
            params.set('cursor', state.cursors.applications);
        }
        const payload = await requestJson(`/api/tracking/history/${deviceId}/applications?${params.toString()}`);
        const rows = Array.isArray(payload.data) ? payload.data : [];
        state.datasets.applications = loadMore ? state.datasets.applications.concat(rows) : rows;
        renderApplicationRows(rows, !loadMore);
        state.cursors.applications = payload.next_cursor || null;
        toggleLoadMore('applicationsLoadMore', Boolean(payload.next_cursor));
    }

    async function loadIntegrity(loadMore) {
        const params = buildWindowQuery();
        params.set('limit', '100');
        if (loadMore && state.cursors.integrity) {
            params.set('cursor', state.cursors.integrity);
        }
        const payload = await requestJson(`/api/tracking/history/${deviceId}/integrity?${params.toString()}`);
        const rows = Array.isArray(payload.data) ? payload.data : [];
        state.datasets.integrity = loadMore ? state.datasets.integrity.concat(rows) : rows;
        renderIntegrityRows(rows, !loadMore);
        state.cursors.integrity = payload.next_cursor || null;
        toggleLoadMore('integrityLoadMore', Boolean(payload.next_cursor));
    }

    async function loadPolicy() {
        let payload;
        try {
            payload = await requestJson(`/api/devices/${deviceId}/alerts`);
        } catch (error) {
            payload = await requestJson(`/api/tracking/devices/${deviceId}/alerts`);
        }
        const rows = Array.isArray(payload.alerts) ? payload.alerts : [];
        state.datasets.policy = rows;
        renderPolicyRows(rows);
    }

    async function archiveDevice() {
        if (!config.archiveEndpoint) {
            showError('Archive endpoint is not configured.');
            return;
        }
        if (!window.confirm('Archive this device? History remains available but the device will be hidden from active tracking.')) {
            return;
        }
        try {
            await requestJson(config.archiveEndpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    device_id: deviceId,
                    reason: 'manual_archive_from_history',
                }),
            });
            window.location.href = '/tracking';
        } catch (error) {
            showError(error.message || 'Failed to archive device.');
        }
    }

    async function runIntegrityCheck() {
        try {
            const response = await requestJson(`/api/tracking/history/${deviceId}/run-integrity`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ days: state.days }),
            });
            if (!response.success) {
                throw new Error(response.error || response.message || 'Integrity check failed.');
            }
            refreshAll();
        } catch (error) {
            showError(error.message || 'Failed to run integrity check.');
        }
    }

    function renderDashboard(payload, data) {
        setText('tzContext', `Displaying: Local (${localTimezone})`);

        setText('healthVerdictText', `Health Verdict: ${data.health_verdict || 'Unknown'}`);
        setText('healthVerdictReason', data.health_verdict_reason || 'No verdict reason available.');
        setText('stateCurrentStatus', titleCase(data.current_status || 'unknown'));
        setText('stateLastSeen', formatTime(data.last_seen_utc || data.last_seen, data.last_seen_epoch_ms));
        setText('stateReachability', formatPct(data.reachability_7d));
        setText('stateStability', formatFixed(data.stability_score, 1));

        setText('summarySamples', asInt(data.sample_count));
        setText('summaryActivity', asInt(data.activity_count));
        setText('summaryConfidence', formatPct(data.data_confidence_pct));

        if (data.expected_samples === null || data.expected_samples === undefined) {
            setText('summarySamplingHealth', `${asInt(data.received_samples)} / n/a`);
        } else {
            setText('summarySamplingHealth', `${asInt(data.received_samples)}/${asInt(data.expected_samples)} (${formatPct(data.sampling_health_pct)})`);
        }

        const staleBadge = document.getElementById('staleBadge');
        if (staleBadge) {
            const stale = Array.isArray(data.anomaly_badges) && data.anomaly_badges.some((item) => String(item.code || '').toUpperCase() === 'STALE_DATA');
            staleBadge.classList.toggle('d-none', !stale);
        }

        renderRiskBadges(data.anomaly_badges || []);
        renderIntegrityPanel(data.integrity_summary || {}, data.integrity_timeline || []);
    }

    function renderRiskBadges(badges) {
        const host = document.getElementById('riskBadges');
        if (!host) {
            return;
        }
        host.innerHTML = '';
        if (!Array.isArray(badges) || badges.length === 0) {
            host.innerHTML = '<span class="history-meta">No risk indicators in selected window.</span>';
            return;
        }

        badges.forEach((item) => {
            const badge = document.createElement('span');
            const severity = String(item.severity || '').toLowerCase();
            badge.className = `history-badge history-risk-pill ${severity === 'high' ? 'high' : 'warning'}`;
            const label = item.label || String(item.code || 'RISK').replace(/_/g, ' ');
            const count = Number(item.count || 0);
            badge.textContent = `${label}${count > 0 ? ` (${count})` : ''}`;
            host.appendChild(badge);
        });
    }

    function renderIntegrityPanel(summary, timeline) {
        const host = document.getElementById('integrityInsights');
        if (host) {
            host.innerHTML = [
                insightCardHtml('Data Confidence', formatPct(summary.data_confidence_pct)),
                insightCardHtml('Invalid %', formatPct(summary.invalid_pct)),
                insightCardHtml('Partial %', formatPct(summary.partial_pct)),
                insightCardHtml('Drift Detected', summary.drift_detected ? 'Yes' : 'No'),
            ].join('');
        }

        const timelineHost = document.getElementById('integrityTimeline');
        if (timelineHost) {
            timelineHost.innerHTML = '';
            if (!Array.isArray(timeline) || timeline.length === 0) {
                timelineHost.innerHTML = '<span class="history-meta">No integrity samples in selected window.</span>';
                return;
            }
            timeline.slice(0, 120).forEach((bucket) => {
                const dominant = dominantIntegrity(bucket);
                const cell = document.createElement('div');
                cell.className = `timeline-cell ${dominant}`;
                cell.title = `${bucket.bucket_start_utc}\nverified=${bucket.verified || 0}, partial=${bucket.partial || 0}, legacy=${bucket.legacy_approx || 0}, invalid=${bucket.invalid || 0}`;
                timelineHost.appendChild(cell);
            });
        }
    }

    function dominantIntegrity(bucket) {
        const states = ['verified', 'partial', 'legacy_approx', 'invalid', 'unknown'];
        let best = 'unknown';
        let bestCount = -1;
        states.forEach((stateKey) => {
            const count = Number(bucket[stateKey] || 0);
            if (count > bestCount) {
                bestCount = count;
                best = stateKey;
            }
        });
        return best;
    }

    function renderActivityRows(rows, replace) {
        const body = document.getElementById('activityBody');
        if (!body) {
            return;
        }
        if (replace) {
            body.innerHTML = '';
        }
        if (!rows.length && replace) {
            body.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">No activity data found for selected window.</td></tr>';
            return;
        }

        const clusterMap = buildClusterMap(rows, 'timestamp_utc', 'timestamp');
        rows.forEach((row) => {
            const tr = document.createElement('tr');
            tr.innerHTML = [
                `<td>${buildTimeCell(row, 'timestamp_utc', 'timestamp_epoch_ms', 'timestamp', clusterMap)}</td>`,
                `<td>${escapeHtml(row.activity_type || 'status_update')}</td>`,
                `<td>${asInt(row.event_count)}</td>`,
                `<td><small class="text-muted">${escapeHtml(formatActivityDetails(row.details))}</small></td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function renderResourceRows(rows, replace) {
        const body = document.getElementById('resourcesBody');
        if (!body) {
            return;
        }
        if (replace) {
            body.innerHTML = '';
        }
        if (!rows.length && replace) {
            body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">No resource data found for selected window.</td></tr>';
            return;
        }

        const clusterMap = buildClusterMap(rows, 'timestamp_utc', 'timestamp');
        rows.forEach((row) => {
            const tr = document.createElement('tr');
            tr.innerHTML = [
                `<td>${buildTimeCell(row, 'timestamp_utc', 'timestamp_epoch_ms', 'timestamp', clusterMap)}</td>`,
                `<td>${formatFixed(row.cpu_usage, 2)}</td>`,
                `<td>${formatFixed(row.memory_usage, 2)}</td>`,
                `<td>${formatFixed(row.disk_usage, 2)}</td>`,
                `<td>${formatFixed(row.upload_kbps, 2)}</td>`,
                `<td>${formatFixed(row.download_kbps, 2)}</td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function renderApplicationRows(rows, replace) {
        const body = document.getElementById('applicationsBody');
        if (!body) {
            return;
        }
        if (replace) {
            body.innerHTML = '';
        }
        if (!rows.length && replace) {
            body.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">No application data found for selected window.</td></tr>';
            return;
        }

        const clusterMap = buildClusterMap(rows, 'timestamp_utc', 'timestamp');
        rows.forEach((row) => {
            const duration = row.duration === null || row.duration === undefined ? 'n/a' : asInt(row.duration);
            const tr = document.createElement('tr');
            tr.innerHTML = [
                `<td>${buildTimeCell(row, 'timestamp_utc', 'timestamp_epoch_ms', 'timestamp', clusterMap)}</td>`,
                `<td>${escapeHtml(row.application_name || 'Unknown')}</td>`,
                `<td>${duration}</td>`,
                `<td>${escapeHtml(row.status || 'active')}</td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function renderIntegrityRows(rows, replace) {
        const body = document.getElementById('integrityBody');
        if (!body) {
            return;
        }
        if (replace) {
            body.innerHTML = '';
        }
        if (!rows.length && replace) {
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No integrity records found for selected window.</td></tr>';
            return;
        }

        const clusterMap = buildClusterMap(rows, 'received_at_utc', 'received_at');
        rows.forEach((row) => {
            const notes = formatIntegrityNotes(row.integrity_notes);
            const tr = document.createElement('tr');
            tr.innerHTML = [
                `<td>${buildTimeCell(row, 'received_at_utc', 'received_at_epoch_ms', 'received_at', clusterMap)}</td>`,
                `<td>${integrityBadge(row.integrity_status || 'unknown')}</td>`,
                `<td>${escapeHtml(row.source || 'sync')}</td>`,
                `<td>${escapeHtml(row.schema_version || '1')}</td>`,
                `<td><small class="text-muted">${escapeHtml(notes)}</small></td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function renderPolicyRows(rows) {
        const body = document.getElementById('policyBody');
        if (!body) {
            return;
        }
        body.innerHTML = '';
        if (!Array.isArray(rows) || rows.length === 0) {
            body.innerHTML = '<tr><td colspan=\"5\" class=\"text-center text-muted py-3\">No policy events yet.</td></tr>';
            return;
        }

        rows.slice(0, 100).forEach((row) => {
            const timeText = formatTime(row.time || row.timestamp || row.observed_at_utc, null);
            const tr = document.createElement('tr');
            tr.innerHTML = [
                `<td>${escapeHtml(timeText)}</td>`,
                `<td>${escapeHtml(row.user || 'unknown')}</td>`,
                `<td>${escapeHtml(row.domain || row.site || row.site_visited || 'unknown')}</td>`,
                `<td>${escapeHtml(String(row.severity || 'Medium'))}</td>`,
                `<td>${escapeHtml(row.action || 'Blocked')}</td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function buildTimeCell(row, utcKey, epochKey, fallbackKey, clusterMap) {
        const isoUtc = row[utcKey] || ensureUtcSuffix(row[fallbackKey]);
        const epochCandidate = row[epochKey];
        const epochMs = epochCandidate !== null && epochCandidate !== undefined
            ? Number(epochCandidate)
            : toEpochMs(isoUtc || row[fallbackKey]);
        const secondBucket = timeSecondBucket(isoUtc || row[fallbackKey]);
        const clusterCount = secondBucket && clusterMap ? Number(clusterMap.get(secondBucket) || 0) : 0;
        const clusterHtml = clusterCount > 1
            ? `<div class="history-meta">+${clusterCount - 1} events same second</div>`
            : '';

        const lines = [`<div class="history-time-cell-main">${escapeHtml(formatTime(isoUtc, Number.isFinite(epochMs) ? epochMs : null))}</div>`];
        if (clusterHtml) {
            lines.push(clusterHtml);
        }
        return lines.join('');
    }

    function buildClusterMap(rows, utcKey, fallbackKey) {
        const map = new Map();
        rows.forEach((row) => {
            const bucket = timeSecondBucket(row[utcKey] || row[fallbackKey]);
            if (!bucket) {
                return;
            }
            map.set(bucket, (map.get(bucket) || 0) + 1);
        });
        return map;
    }

    function timeSecondBucket(raw) {
        const parsed = parseTime(raw);
        if (!parsed) {
            return null;
        }
        return parsed.toISOString().slice(0, 19);
    }

    function renderInsights() {
        renderActivityInsights();
        renderResourcesInsights();
        renderApplicationsInsights();
    }

    function renderActivityInsights() {
        const host = document.getElementById('activityInsights');
        if (!host) {
            return;
        }
        const rows = state.datasets.activity;
        if (!rows.length) {
            host.innerHTML = '';
            return;
        }

        let activeCount = 0;
        let idleCount = 0;
        const hours = new Map();
        const days = new Map();

        rows.forEach((row) => {
            const idleSeconds = extractIdleSeconds(row.details);
            if (idleSeconds !== null && idleSeconds >= 300) {
                idleCount += 1;
            } else {
                activeCount += 1;
            }

            const dt = parseTime(row.timestamp_utc || row.timestamp);
            if (dt) {
                const hour = dt.getHours();
                hours.set(hour, (hours.get(hour) || 0) + 1);
                const day = dt.toISOString().slice(0, 10);
                days.set(day, (days.get(day) || 0) + 1);
            }
        });

        const total = rows.length;
        const idlePct = total ? ((idleCount / total) * 100) : 0;
        host.innerHTML = [
            insightCardHtml('Total Active Events', String(activeCount)),
            insightCardHtml('Idle %', `${idlePct.toFixed(1)}%`),
            insightCardHtml('Peak Hour', topKey(hours) === null ? 'n/a' : `${topKey(hours)}:00`),
            insightCardHtml('Most Active Day', topKey(days) || 'n/a'),
        ].join('');
    }

    function renderResourcesInsights() {
        const host = document.getElementById('resourcesInsights');
        if (!host) {
            return;
        }
        const rows = state.datasets.resources;
        if (!rows.length) {
            host.innerHTML = '';
            return;
        }

        const cpu = rows.map((row) => Number(row.cpu_usage)).filter((value) => Number.isFinite(value));
        const memory = rows.map((row) => Number(row.memory_usage)).filter((value) => Number.isFinite(value));
        const spikes = cpu.filter((value) => value >= 90).length;

        host.innerHTML = [
            insightCardHtml('CPU P95', formatFixed(percentile(cpu, 95), 2)),
            insightCardHtml('Memory P95', formatFixed(percentile(memory, 95), 2)),
            insightCardHtml('Spike Count', String(spikes)),
            insightCardHtml('Trend', trendDirection(cpu)),
        ].join('');
    }

    function renderApplicationsInsights() {
        const host = document.getElementById('applicationsInsights');
        if (!host) {
            return;
        }
        const rows = state.datasets.applications;
        if (!rows.length) {
            host.innerHTML = '';
            return;
        }

        const usage = new Map();
        let unknownCount = 0;
        let suspiciousCount = 0;

        rows.forEach((row) => {
            const app = String(row.application_name || 'Unknown');
            const duration = Number(row.duration);
            usage.set(app, (usage.get(app) || 0) + (Number.isFinite(duration) ? duration : 0));
            if (!row.application_name || /unknown/i.test(app)) {
                unknownCount += 1;
            }
            if (SUSPICIOUS_APP_PATTERNS.some((pattern) => pattern.test(app))) {
                suspiciousCount += 1;
            }
        });

        const topApps = Array.from(usage.entries())
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5)
            .map((entry) => `${entry[0]} (${asInt(entry[1])}s)`)
            .join(', ') || 'n/a';

        host.innerHTML = [
            insightCardHtml('Top 5 Apps', escapeHtml(topApps)),
            insightCardHtml('Unknown Apps', String(unknownCount)),
            insightCardHtml('Suspicious Apps', String(suspiciousCount)),
            insightCardHtml('Rows Loaded', String(rows.length)),
        ].join('');
    }

    function insightCardHtml(label, value) {
        return `
            <div class="col-md-3">
                <div class="card history-insight-card">
                    <div class="card-body">
                        <div class="history-insight-label">${label}</div>
                        <div class="history-insight-value">${value}</div>
                    </div>
                </div>
            </div>
        `;
    }

    function integrityBadge(status) {
        const normalized = String(status || '').toLowerCase();
        if (normalized === 'verified') {
            return '<span class="history-badge history-badge-verified">Verified</span>';
        }
        if (normalized === 'legacy_approx') {
            return '<span class="history-badge history-badge-legacy">Legacy Approx</span>';
        }
        if (normalized === 'partial') {
            return '<span class="history-badge history-badge-partial">Partial</span>';
        }
        if (normalized === 'invalid') {
            return '<span class="history-badge history-badge-invalid">Invalid</span>';
        }
        return `<span class="history-badge">${escapeHtml(normalized || 'unknown')}</span>`;
    }

    function toggleLoadMore(buttonId, visible) {
        const button = document.getElementById(buttonId);
        if (button) {
            button.classList.toggle('d-none', !visible);
        }
    }

    function showError(message) {
        const errorNode = document.getElementById('historyError');
        const textNode = document.getElementById('historyErrorText');
        if (!errorNode || !textNode) {
            return;
        }
        textNode.textContent = message;
        errorNode.classList.remove('d-none');
    }

    function hideError() {
        const errorNode = document.getElementById('historyError');
        const textNode = document.getElementById('historyErrorText');
        if (!errorNode || !textNode) {
            return;
        }
        textNode.textContent = '';
        errorNode.classList.add('d-none');
    }

    async function requestJson(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...options,
            headers: {
                'X-Client-Epoch-Ms': String(Date.now()),
                ...(options && options.headers ? options.headers : {}),
            },
        });

        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
        }
        return payload;
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) {
            node.textContent = String(value);
        }
    }

    function ensureUtcSuffix(raw) {
        const text = String(raw || '').trim();
        if (!text) {
            return '';
        }
        return /(Z|[+-]\d{2}:\d{2})$/i.test(text) ? text : `${text}Z`;
    }

    function parseTime(raw) {
        const text = ensureUtcSuffix(raw);
        if (!text) {
            return null;
        }
        const parsed = new Date(text);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function formatTime(raw, epochMs) {
        const parsed = epochMs !== null && epochMs !== undefined
            ? new Date(Number(epochMs))
            : parseTime(raw);
        if (!parsed || Number.isNaN(parsed.getTime())) {
            return 'n/a';
        }

        const options = {
            year: 'numeric',
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        };
        if (localTimezone && localTimezone !== 'Local') {
            options.timeZone = localTimezone;
        }
        return new Intl.DateTimeFormat('en-US', options).format(parsed);
    }

    function toEpochMs(raw) {
        const parsed = parseTime(raw);
        return parsed ? parsed.getTime() : null;
    }

    function formatPct(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) {
            return 'N/A';
        }
        return `${num.toFixed(1)}%`;
    }

    function formatFixed(value, digits) {
        const num = Number(value);
        if (!Number.isFinite(num)) {
            return 'n/a';
        }
        return num.toFixed(digits);
    }

    function asInt(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) {
            return 0;
        }
        return Math.round(num);
    }

    function truncateText(text, maxLength) {
        const value = String(text || '');
        if (value.length <= maxLength) {
            return value;
        }
        return `${value.slice(0, maxLength - 3)}...`;
    }

    function extractIdleSeconds(details) {
        if (!details) {
            return null;
        }
        try {
            const parsed = typeof details === 'string' ? JSON.parse(details) : details;
            const value = Number(parsed.idle_seconds);
            return Number.isFinite(value) ? value : null;
        } catch (error) {
            return null;
        }
    }

    function topKey(map) {
        let bestKey = null;
        let bestValue = -1;
        map.forEach((value, key) => {
            if (value > bestValue) {
                bestValue = value;
                bestKey = key;
            }
        });
        return bestKey;
    }

    function percentile(values, percentileValue) {
        if (!Array.isArray(values) || values.length === 0) {
            return null;
        }
        const sorted = [...values].sort((a, b) => a - b);
        const index = Math.max(0, Math.min(sorted.length - 1, Math.ceil((percentileValue / 100) * sorted.length) - 1));
        return sorted[index];
    }

    function trendDirection(values) {
        if (!Array.isArray(values) || values.length < 2) {
            return 'flat';
        }
        const recent = values.slice(0, Math.min(values.length, 10));
        const older = values.slice(-Math.min(values.length, 10));
        const recentAvg = recent.reduce((sum, item) => sum + item, 0) / recent.length;
        const olderAvg = older.reduce((sum, item) => sum + item, 0) / older.length;
        if (recentAvg - olderAvg > 3) {
            return 'up';
        }
        if (recentAvg - olderAvg < -3) {
            return 'down';
        }
        return 'flat';
    }

    function titleCase(value) {
        return String(value || '')
            .split(/[_\s-]+/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
            .join(' ');
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatActivityDetails(rawDetails) {
        if (!rawDetails) {
            return 'No activity details';
        }
        try {
            const details = typeof rawDetails === 'string' ? JSON.parse(rawDetails) : rawDetails;
            const app = details.current_application || '';
            const idle = Number(details.idle_seconds);
            const idleText = Number.isFinite(idle) ? `${idle.toFixed(1)}s` : 'n/a';
            const kb = details.keyboard_active ? 'active' : 'idle';
            const mouse = details.mouse_active ? 'active' : 'idle';
            const parts = [];
            if (app) {
                parts.push(`App: ${app}`);
            }
            parts.push(`Idle: ${idleText}`);
            parts.push(`Keyboard: ${kb}`);
            parts.push(`Mouse: ${mouse}`);
            return parts.join(' | ');
        } catch (error) {
            return truncateText(String(rawDetails), 160);
        }
    }

    function formatIntegrityNotes(rawNotes) {
        if (!rawNotes) {
            return 'No notes';
        }
        try {
            const notes = typeof rawNotes === 'string' ? JSON.parse(rawNotes) : rawNotes;
            if (notes && typeof notes === 'object') {
                const keys = Object.keys(notes);
                if (!keys.length) {
                    return 'No notes';
                }
                return keys.map((key) => `${key}: ${String(notes[key])}`).join(' | ').slice(0, 180);
            }
        } catch (error) {
            // fall through
        }
        return truncateText(String(rawNotes), 180);
    }
}());
