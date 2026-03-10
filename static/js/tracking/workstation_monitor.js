(function () {
    'use strict';

    const config = window.TRACKING_WORKSTATION_CONFIG || {};
    const deviceId = Number(config.deviceId || 0);
    if (!deviceId) {
        return;
    }

    const state = {
        days: Number(config.defaultDays || 7),
        availabilityCursor: null,
    };

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        reloadAll();
    });

    function bindEvents() {
        const range = document.getElementById('workstationRange');
        if (range) {
            range.value = String(state.days);
            range.addEventListener('change', () => {
                const parsed = Number(range.value || 7);
                state.days = Number.isFinite(parsed) && parsed > 0 ? parsed : 7;
                state.availabilityCursor = null;
                clearAvailabilityRows();
                reloadAll();
            });
        }

        const loadMore = document.getElementById('availabilityLoadMore');
        if (loadMore) {
            loadMore.addEventListener('click', () => loadAvailability(true));
        }
    }

    function buildWindowQuery() {
        const to = new Date();
        const from = new Date(to.getTime() - (state.days * 24 * 60 * 60 * 1000));
        const params = new URLSearchParams();
        params.set('from', from.toISOString());
        params.set('to', to.toISOString());
        params.set('tz', 'UTC');
        return params;
    }

    function reloadAll() {
        hideError();
        loadOverview();
        loadReports();
        loadAvailability(false);
        loadAnomalies();
    }

    async function loadOverview() {
        try {
            const payload = await requestJson(`/api/tracking/workstation/${deviceId}/overview`);
            const data = payload.data || {};
            setText('lastSampleAt', formatDateTime(data.last_sample_at));
            setText('lastAvailabilityAt', formatDateTime(data.last_availability_event_at));
            applyStaleBadge(Boolean(data.is_stale));
        } catch (error) {
            showError(error.message || 'Failed to load overview.');
        }
    }

    async function loadReports() {
        try {
            const params = buildWindowQuery();
            const payload = await requestJson(`/api/tracking/workstation/${deviceId}/reports?${params.toString()}`);
            const data = payload.data || {};
            setText('reachabilityPct', safeDisplay(data.reachability_display));
            setText('degradedImpactPct', safeDisplay(data.degraded_impact_display));
            setText('dataConfidencePct', safeDisplay(data.data_confidence_display));
            setText('appCoveragePct', safeDisplay(data.app_duration_coverage_display));
        } catch (error) {
            showError(error.message || 'Failed to load reports.');
        }
    }

    async function loadAvailability(isLoadMore) {
        try {
            const params = buildWindowQuery();
            params.set('limit', '100');
            if (isLoadMore && state.availabilityCursor) {
                params.set('cursor', state.availabilityCursor);
            }
            const payload = await requestJson(`/api/tracking/workstation/${deviceId}/availability?${params.toString()}`);
            const rows = Array.isArray(payload.data) ? payload.data : [];
            renderAvailabilityRows(rows, !isLoadMore);
            state.availabilityCursor = payload.next_cursor || null;
            toggleLoadMore(Boolean(payload.next_cursor));
        } catch (error) {
            showError(error.message || 'Failed to load availability timeline.');
        }
    }

    async function loadAnomalies() {
        try {
            const params = buildWindowQuery();
            const payload = await requestJson(`/api/tracking/workstation/${deviceId}/anomalies?${params.toString()}`);
            renderAnomalies(Array.isArray(payload.data) ? payload.data : []);
        } catch (error) {
            showError(error.message || 'Failed to load anomalies.');
        }
    }

    function renderAvailabilityRows(rows, replace) {
        const body = document.getElementById('availabilityBody');
        if (!body) {
            return;
        }
        if (replace) {
            body.innerHTML = '';
        }
        if (!rows.length && replace) {
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No availability events in selected range.</td></tr>';
            return;
        }

        rows.forEach((row) => {
            const tr = document.createElement('tr');
            const status = String(row.status || 'offline').toUpperCase();
            tr.innerHTML = [
                `<td>${formatDateTime(row.observed_at)}</td>`,
                `<td>${escapeHtml(status)}</td>`,
                `<td>${escapeHtml(row.event_type || '-')}</td>`,
                `<td>${escapeHtml(row.source || '-')}</td>`,
                `<td>${escapeHtml(row.probe_method || row.probe_error_code || '-')}</td>`,
            ].join('');
            body.appendChild(tr);
        });
    }

    function renderAnomalies(rows) {
        const list = document.getElementById('anomaliesList');
        const empty = document.getElementById('anomaliesEmpty');
        if (!list || !empty) {
            return;
        }
        list.innerHTML = '';
        if (!rows.length) {
            list.classList.add('d-none');
            empty.classList.remove('d-none');
            return;
        }

        rows.forEach((row) => {
            const item = document.createElement('li');
            item.className = 'list-group-item';
            const code = escapeHtml(row.code || 'ANOMALY');
            const severity = escapeHtml(String(row.severity || 'info').toUpperCase());
            const details = row.details ? escapeHtml(JSON.stringify(row.details)) : '-';
            item.innerHTML = `<div><strong>${code}</strong> <span class="text-muted">(${severity})</span></div><small class="text-muted">${details}</small>`;
            list.appendChild(item);
        });

        empty.classList.add('d-none');
        list.classList.remove('d-none');
    }

    function applyStaleBadge(isStale) {
        const badge = document.getElementById('staleBadge');
        if (!badge) {
            return;
        }
        badge.className = `badge ${isStale ? 'bg-danger' : 'bg-success'}`;
        badge.textContent = isStale ? 'STALE' : 'FRESH';
    }

    function toggleLoadMore(visible) {
        const button = document.getElementById('availabilityLoadMore');
        if (button) {
            button.classList.toggle('d-none', !visible);
        }
    }

    function clearAvailabilityRows() {
        const body = document.getElementById('availabilityBody');
        if (body) {
            body.innerHTML = '';
        }
    }

    function safeDisplay(value) {
        const text = String(value || '').trim();
        return text || 'N/A';
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) {
            node.textContent = String(value);
        }
    }

    function showError(message) {
        const error = document.getElementById('workstationError');
        if (!error) {
            return;
        }
        error.textContent = String(message || 'Request failed.');
        error.classList.remove('d-none');
    }

    function hideError() {
        const error = document.getElementById('workstationError');
        if (!error) {
            return;
        }
        error.textContent = '';
        error.classList.add('d-none');
    }

    async function requestJson(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...options,
            headers: {
                ...(options && options.headers ? options.headers : {}),
            },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || payload.message || `Request failed (${response.status}).`);
        }
        return payload;
    }

    function formatDateTime(raw) {
        if (!raw) {
            return 'n/a';
        }
        const parsed = new Date(raw);
        if (Number.isNaN(parsed.getTime())) {
            return String(raw);
        }
        return parsed.toLocaleString();
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
}());
