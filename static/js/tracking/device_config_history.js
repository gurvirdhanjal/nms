/**
 * device_config_history.js
 * Config History tab for the Device History page.
 * Reads window.TRACKING_HISTORY_CONFIG.linkedDeviceId + isAdmin.
 * Lazy-loads on first tab open; wires diff modal.
 */
(function () {
    'use strict';

    var cfg = window.TRACKING_HISTORY_CONFIG || {};
    var linkedDeviceId = cfg.linkedDeviceId || null;
    var isAdmin = Boolean(cfg.isAdmin);

    var state = {
        snapshots: [],
        diffModal: null,
        loaded: false,
    };

    // ── Entry point ───────────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        initDiffModal();

        if (!linkedDeviceId) {
            bindTabActivation(function () {
                showError(
                    'This device has no linked inventory device. ' +
                    'Config backups require an active identity link.'
                );
            });
            return;
        }

        bindTabActivation(function () {
            if (!state.loaded) {
                loadConfigHistory();
            }
        });

        if (isAdmin) {
            var captureBtn = document.getElementById('configCaptureNowBtn');
            if (captureBtn) {
                captureBtn.addEventListener('click', triggerManualBackup);
            }
        }
    });

    // ── Tab activation ────────────────────────────────────────────────────────

    function bindTabActivation(callback) {
        var tabBtn = document.querySelector('[data-bs-target="#configTab"]');
        if (!tabBtn) { return; }
        tabBtn.addEventListener('shown.bs.tab', function () {
            callback();
        });
    }

    // ── Config history fetch + render ─────────────────────────────────────────

    function loadConfigHistory() {
        showLoading(true);
        hideError();

        requestJson('/api/devices/' + linkedDeviceId + '/config-history')
            .then(function (snapshots) {
                state.snapshots = Array.isArray(snapshots) ? snapshots : [];
                state.loaded = true;
                renderHistoryTable(state.snapshots);
            })
            .catch(function (err) {
                showError(err.message || 'Failed to load configuration snapshots.');
            })
            .finally(function () {
                showLoading(false);
            });
    }

    function renderHistoryTable(snapshots) {
        var body = document.getElementById('configSnapshotBody');
        var wrap = document.getElementById('configTableWrap');
        var empty = document.getElementById('configEmptyState');
        if (!body || !wrap || !empty) { return; }

        body.innerHTML = '';

        if (!snapshots.length) {
            wrap.classList.add('d-none');
            empty.classList.remove('d-none');
            return;
        }

        empty.classList.add('d-none');
        wrap.classList.remove('d-none');

        snapshots.forEach(function (snap, index) {
            var tr = document.createElement('tr');

            // Only rows after the first (newest) can be diffed against the one above them
            var hasDiff = index > 0;
            if (hasDiff) { tr.style.cursor = 'pointer'; }

            var dateText = formatSnapshotDate(snap.captured_at);
            var sourceHtml = snap.source === 'manual'
                ? '<span class="config-source-badge-manual">Manual</span>'
                : '<span class="config-source-badge-scheduled">Scheduled</span>';
            var changedHtml = snap.changed
                ? '<span class="config-changed-badge-yes">Changed</span>'
                : '<span class="config-changed-badge-no">Unchanged</span>';
            var hashText = snap.config_hash
                ? '<span class="config-hash-mono">' + escapeHtml(String(snap.config_hash).slice(0, 8)) + '</span>'
                : '<span class="history-meta">\u2014</span>';
            var capturedBy = snap.captured_by
                ? escapeHtml(snap.captured_by)
                : '<span class="history-meta">system</span>';

            // Diff direction: snapshots[index] is older, snapshots[index-1] is newer.
            // API expects from=older, to=newer.
            var actionsHtml = '';
            if (hasDiff) {
                var newerSnap = snapshots[index - 1];
                actionsHtml = '<button type="button" class="tactical-btn-outline history-inline-btn config-diff-btn"'
                    + ' data-from-id="' + snap.id + '"'
                    + ' data-to-id="' + newerSnap.id + '">'
                    + 'View Diff</button>';
            }

            tr.innerHTML = [
                '<td><span class="history-time-cell-main">' + escapeHtml(dateText) + '</span></td>',
                '<td>' + sourceHtml + '</td>',
                '<td>' + capturedBy + '</td>',
                '<td>' + changedHtml + '</td>',
                '<td>' + hashText + '</td>',
                '<td>' + (actionsHtml || '<span class="history-meta">\u2014</span>') + '</td>',
            ].join('');

            if (hasDiff) {
                var newerSnap2 = snapshots[index - 1];
                var fromId = snap.id;
                var toId = newerSnap2.id;
                tr.addEventListener('click', function (e) {
                    if (e.target.closest && e.target.closest('.config-diff-btn')) { return; }
                    openDiffModal(fromId, toId);
                });
            }

            body.appendChild(tr);
        });

        // Wire diff buttons via delegation on tbody
        body.addEventListener('click', function (e) {
            var btn = e.target.closest('.config-diff-btn');
            if (!btn) { return; }
            var fromId = Number(btn.dataset.fromId);
            var toId = Number(btn.dataset.toId);
            openDiffModal(fromId, toId);
        });
    }

    // ── Diff modal ────────────────────────────────────────────────────────────

    function initDiffModal() {
        var el = document.getElementById('configDiffModal');
        if (!el || !window.bootstrap) { return; }
        try {
            state.diffModal = new window.bootstrap.Modal(el);
        } catch (e) {
            state.diffModal = null;
        }
    }

    function openDiffModal(fromId, toId) {
        if (!state.diffModal) { return; }

        var titleEl = document.getElementById('configDiffModalTitle');
        var bodyEl = document.getElementById('configDiffModalBody');
        if (!bodyEl) { return; }

        bodyEl.innerHTML = '<div class="text-center py-4 history-meta">'
            + '<i class="fas fa-spinner fa-spin me-2"></i>Loading diff...</div>';
        if (titleEl) { titleEl.textContent = 'Config Diff \u2014 Loading\u2026'; }

        state.diffModal.show();

        requestJson(
            '/api/devices/' + linkedDeviceId + '/config-diff?from=' + fromId + '&to=' + toId
        )
            .then(function (data) {
                var fromDate = formatSnapshotDate(data.from_captured_at);
                var toDate = formatSnapshotDate(data.to_captured_at);
                if (titleEl) {
                    titleEl.textContent = 'Config Diff \u2014 ' + fromDate + ' \u2192 ' + toDate;
                }
                bodyEl.innerHTML = renderDiffContent(data);
            })
            .catch(function (err) {
                bodyEl.innerHTML = '<div class="alert alert-danger py-2">'
                    + escapeHtml(err.message || 'Failed to load diff.') + '</div>';
            });
    }

    function renderDiffContent(data) {
        var changedBadge = data.changed
            ? '<span class="config-changed-badge-yes">Changed</span>'
            : '<span class="config-changed-badge-no">Unchanged</span>';

        var metaRow = '<div class="d-flex align-items-center gap-2 mb-3">'
            + changedBadge
            + '<span class="history-meta">Snapshot #' + escapeHtml(String(data.from_id))
            + ' \u2192 #' + escapeHtml(String(data.to_id)) + '</span>'
            + '</div>';

        if (!data.changed || !data.diff || !data.diff.trim()) {
            return metaRow
                + '<div class="alert py-2" style="background:rgba(32,201,151,.10);'
                + 'border:1px solid rgba(32,201,151,.24);color:#7acfb8;">'
                + '<i class="fas fa-check-circle me-2"></i>'
                + 'No configuration changes detected between these snapshots.</div>';
        }

        var lines = data.diff.split('\n');
        var lineHtml = lines.map(function (line) {
            var cls = '';
            if (line.startsWith('+') && !line.startsWith('+++')) {
                cls = 'diff-line-added';
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                cls = 'diff-line-removed';
            } else if (line.startsWith('@@')) {
                cls = 'diff-line-header';
            }
            return '<span class="diff-line ' + cls + '">' + escapeHtml(line) + '</span>';
        }).join('\n');

        return metaRow + '<pre class="config-diff-pre">' + lineHtml + '</pre>';
    }

    // ── Manual backup ─────────────────────────────────────────────────────────

    function triggerManualBackup() {
        var btn = document.getElementById('configCaptureNowBtn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i> Capturing\u2026';
        }

        requestJson('/api/devices/' + linkedDeviceId + '/config-backup', { method: 'POST' })
            .then(function (result) {
                var msg = result.changed
                    ? 'Config captured \u2014 changes detected (snapshot #' + result.snapshot_id + ').'
                    : 'Config captured \u2014 no changes from previous snapshot.';
                showToast(msg, result.changed ? 'info' : 'success');
                // Force reload of snapshot list
                state.loaded = false;
                state.snapshots = [];
                loadConfigHistory();
            })
            .catch(function (err) {
                showToast('Capture failed: ' + (err.message || 'Unknown error'), 'danger');
            })
            .finally(function () {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-download me-1"></i> Capture Now';
                }
            });
    }

    // ── UI helpers ────────────────────────────────────────────────────────────

    function showLoading(visible) {
        var el = document.getElementById('configLoadingState');
        if (el) { el.classList.toggle('d-none', !visible); }
    }

    function showError(message) {
        var wrap = document.getElementById('configErrorState');
        var text = document.getElementById('configErrorText');
        if (!wrap || !text) { return; }
        text.textContent = message;
        wrap.classList.remove('d-none');
    }

    function hideError() {
        var el = document.getElementById('configErrorState');
        if (el) { el.classList.add('d-none'); }
    }

    function showToast(message, type) {
        if (window.UI && window.UI.Toast && typeof window.UI.Toast.show === 'function') {
            window.UI.Toast.show(message, type || 'info');
        }
    }

    // ── Utilities ─────────────────────────────────────────────────────────────

    function requestJson(url, options) {
        var opts = Object.assign({ credentials: 'same-origin' }, options || {});
        opts.headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
        return fetch(url, opts).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (payload) {
                if (!response.ok) {
                    throw new Error(
                        payload.error || payload.message || 'Request failed (' + response.status + ')'
                    );
                }
                return payload;
            });
        });
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatSnapshotDate(isoString) {
        if (!isoString) { return 'n/a'; }
        // Ensure UTC interpretation if no tz suffix
        var raw = /Z|[+-]\d{2}:\d{2}$/i.test(isoString) ? isoString : isoString + 'Z';
        var d = new Date(raw);
        if (isNaN(d.getTime())) { return isoString; }
        return new Intl.DateTimeFormat('en-US', {
            year: 'numeric', month: 'short', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        }).format(d);
    }

    // Polyfill for older browsers: String.prototype.startsWith
    if (!String.prototype.startsWith) {
        String.prototype.startsWith = function (search) {
            return this.indexOf(search) === 0;
        };
    }

}());
