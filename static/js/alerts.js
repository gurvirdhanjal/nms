(function () {
  'use strict';

  var API_URL       = '/api/alerts';
  var pollTimer     = null;
  var currentStatus = 'active';

  var filterSite     = document.getElementById('filterSite');
  var filterSeverity = document.getElementById('filterSeverity');
  var filterSearch   = document.getElementById('filterSearch');
  var statusBtns     = document.querySelectorAll('#filterStatus [data-status]');
  var tableBody      = document.getElementById('alertsTableBody');
  var emptyMsg       = document.getElementById('alerts-empty');
  var errorMsg       = document.getElementById('alerts-error');

  /* ──────────────────────────────────────────────────────────
     ALERTS TABLE
  ────────────────────────────────────────────────────────── */

  function buildUrl() {
    var params = new URLSearchParams();
    if (filterSite     && filterSite.value)     params.set('site_id',  filterSite.value);
    if (filterSeverity && filterSeverity.value) params.set('severity', filterSeverity.value);
    params.set('status', currentStatus);
    params.set('limit', '200');
    return API_URL + '?' + params.toString();
  }

  function severityClass(sev) {
    var map = { CRITICAL: 'severity-critical', WARNING: 'severity-warning', INFO: 'severity-info' };
    return map[sev] || 'severity-info';
  }

  function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                          .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function fmtTs(iso) {
    if (!iso) return '—';
    var ts = iso.endsWith('Z') ? iso : iso + 'Z';
    return new Date(ts).toLocaleString();
  }

  window.loadAlerts = function () {
    if (errorMsg) errorMsg.style.display = 'none';
    if (emptyMsg) emptyMsg.style.display = 'none';

    fetch(buildUrl(), { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        var q = (filterSearch && filterSearch.value.trim().toLowerCase()) || '';
        var alerts = data.alerts || [];

        if (q) {
          alerts = alerts.filter(function (a) {
            return (a.device_name || '').toLowerCase().includes(q) ||
                   (a.message     || '').toLowerCase().includes(q) ||
                   (a.device_ip   || '').toLowerCase().includes(q);
          });
        }

        if (!tableBody) return;

        if (alerts.length === 0) {
          tableBody.innerHTML = '';
          if (emptyMsg) emptyMsg.style.display = '';
          return;
        }

        tableBody.innerHTML = alerts.map(function (a) {
          var resolved   = a.resolved;
          var resolveBtn = !resolved
            ? '<button class="tactical-btn tactical-btn-sm tactical-btn-outline" onclick="resolveAlert(\'' + esc(a.alert_id) + '\', this)">Resolve</button>'
            : '';
          var statusBadge = resolved
            ? '<span class="ops-badge" style="background:rgba(100,100,120,0.2);color:#9ca3af">Resolved</span>'
            : '<span class="ops-badge severity-warning">Active</span>';
          var devName = esc(a.device_name || '—');
          var devCell = a.device_id
            ? '<strong class="dev-link" onclick="openDeviceHistory(' + a.device_id + ',\'' + esc(a.device_name || '') + '\',\'' + esc(a.device_ip || '') + '\')" title="View alert history">' + devName + '</strong>'
            : '<strong>' + devName + '</strong>';
          return '<tr>'
            + '<td><span class="ops-badge ' + severityClass(a.severity) + '">' + esc(a.severity) + '</span></td>'
            + '<td>' + devCell + '<br><small class="ip-pill">' + esc(a.device_ip || '') + '</small></td>'
            + '<td class="text-muted">' + esc(a.dept_name || '—') + '</td>'
            + '<td>' + esc(a.message || '—') + '</td>'
            + '<td><code>' + esc(a.metric_name || '—') + '</code></td>'
            + '<td style="font-size:0.78rem;font-family:monospace">' + fmtTs(a.timestamp) + '</td>'
            + '<td>' + statusBadge + '</td>'
            + '<td>' + resolveBtn + '</td>'
            + '</tr>';
        }).join('');

        clearTimeout(pollTimer);
        pollTimer = setTimeout(loadAlerts, 30000);
      })
      .catch(function () {
        if (errorMsg) errorMsg.style.display = '';
        clearTimeout(pollTimer);
        pollTimer = setTimeout(loadAlerts, 60000);
      });
  };

  window.resolveAlert = function (alertId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Resolving…'; }
    fetch('/api/alerts/' + alertId + '/resolve', {
      method: 'PATCH',
      credentials: 'same-origin',
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function () { loadAlerts(); })
      .catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Resolve'; }
        alert('Failed to resolve alert. Please try again.');
      });
  };

  if (filterSite)     filterSite.addEventListener('change', loadAlerts);
  if (filterSeverity) filterSeverity.addEventListener('change', loadAlerts);

  if (filterSearch) {
    var debounceTimer;
    filterSearch.addEventListener('input', function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadAlerts, 250);
    });
  }

  statusBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      statusBtns.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      currentStatus = btn.dataset.status;
      loadAlerts();
    });
  });

  /* ──────────────────────────────────────────────────────────
     DEVICE ALERT HISTORY MODAL
  ────────────────────────────────────────────────────────── */

  window.openDeviceHistory = function (deviceId, deviceName, deviceIp) {
    var modal = document.getElementById('deviceHistoryModal');
    if (!modal) return;

    // Reset state
    document.getElementById('deviceHistTitle').textContent   = esc(deviceName || 'Device');
    document.getElementById('deviceHistSubtitle').textContent = deviceIp || '';
    document.getElementById('deviceHistLoading').style.display = '';
    document.getElementById('deviceHistError').style.display   = 'none';
    document.getElementById('deviceHistTable').style.display   = 'none';
    document.getElementById('deviceHistEmpty').style.display   = 'none';
    document.getElementById('deviceHistFooter').style.display  = 'none';
    document.getElementById('deviceHistBody').innerHTML        = '';

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    fetch('/api/alerts/device/' + deviceId + '?limit=100', { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        document.getElementById('deviceHistLoading').style.display = 'none';
        var alerts = data.alerts || [];
        if (!alerts.length) {
          document.getElementById('deviceHistEmpty').style.display = '';
          return;
        }
        var tbody = document.getElementById('deviceHistBody');
        tbody.innerHTML = alerts.map(function (a) {
          var sevCls = 'hist-sev-' + (a.severity || 'INFO');
          var statusTxt = a.resolved
            ? '<span style="color:#9ca3af;font-size:.75rem">Resolved</span>'
            : '<span style="color:#fcd34d;font-size:.75rem">Active</span>';
          return '<tr>'
            + '<td><span class="hist-sev-badge ' + sevCls + '">' + esc(a.severity || '—') + '</span></td>'
            + '<td style="font-size:.75rem;color:#9ca3af">' + esc(a.event_type || '—') + '</td>'
            + '<td>' + esc(a.message || '—') + '</td>'
            + '<td><code style="font-size:.75rem">' + esc(a.metric_name || '—') + '</code></td>'
            + '<td style="font-size:.75rem;font-family:monospace;white-space:nowrap">' + fmtTs(a.timestamp) + '</td>'
            + '<td>' + statusTxt + '</td>'
            + '</tr>';
        }).join('');
        document.getElementById('deviceHistTable').style.display  = '';
        document.getElementById('deviceHistFooter').style.display = '';
        document.getElementById('deviceHistCount').textContent    =
          'Showing ' + alerts.length + ' of ' + (data.total || alerts.length) + ' alerts';
      })
      .catch(function () {
        document.getElementById('deviceHistLoading').style.display = 'none';
        document.getElementById('deviceHistError').style.display   = '';
      });
  };

  /* ──────────────────────────────────────────────────────────
     NOTIFICATION SETTINGS PANEL (admin only)
  ────────────────────────────────────────────────────────── */

  var notifPanel = document.getElementById('notifPanel');
  if (notifPanel) {
    notifPanel.addEventListener('show.bs.offcanvas', function () {
      loadChannels();
      loadSmtpForm();
    });
  }

  /* ── Channels ── */

  function loadChannels() {
    var el = document.getElementById('channelList');
    if (!el) return;
    el.innerHTML = '<div class="text-muted" style="font-size:.82rem">Loading…</div>';
    fetch('/api/settings/alert-channels', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(renderChannels)
      .catch(function () {
        el.innerHTML = '<div class="text-danger" style="font-size:.8rem">Failed to load channels.</div>';
      });
  }

  function renderChannels(channels) {
    var el = document.getElementById('channelList');
    if (!el) return;
    if (!channels || !channels.length) {
      el.innerHTML = '<div class="text-muted" style="font-size:.82rem">No channels configured yet.</div>';
      return;
    }
    el.innerHTML = channels.map(function (ch) {
      var typeIcon = { email: '✉', slack: 'S', teams: 'T' }[ch.channel_type] || '?';
      return '<div class="channel-row d-flex align-items-center gap-2">'
        + '<span class="channel-icon">' + typeIcon + '</span>'
        + '<span style="flex:1;font-size:.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        +   esc(ch.name) + '</span>'
        // severity chips
        + '<span class="sev-chip crit' + (ch.send_on_critical ? ' on' : '') + '" '
        +   'onclick="toggleChannelSev(' + ch.id + ',\'critical\',' + !ch.send_on_critical + ',this)" '
        +   'title="Notify on Critical">C</span>'
        + '<span class="sev-chip warn' + (ch.send_on_warning ? ' on' : '') + '" '
        +   'onclick="toggleChannelSev(' + ch.id + ',\'warning\',' + !ch.send_on_warning + ',this)" '
        +   'title="Notify on Warning">W</span>'
        // enabled toggle
        + '<label class="toggle-pill" title="' + (ch.is_enabled ? 'Enabled' : 'Disabled') + '">'
        +   '<input type="checkbox" ' + (ch.is_enabled ? 'checked' : '') + ' '
        +     'onchange="toggleChannelEnabled(' + ch.id + ',this.checked)">'
        +   '<span class="toggle-track"></span>'
        + '</label>'
        // edit / delete
        + '<button class="tactical-btn tactical-btn-sm tactical-btn-outline" style="padding:.15rem .4rem;font-size:.7rem" '
        +   'onclick="editChannel(' + ch.id + ')">Edit</button>'
        + '<button class="tactical-btn tactical-btn-sm" style="padding:.15rem .4rem;font-size:.7rem;background:rgba(239,68,68,.15);color:#fca5a5;border-color:rgba(239,68,68,.3)" '
        +   'onclick="deleteChannel(' + ch.id + ',\'' + esc(ch.name) + '\')">✕</button>'
        + '</div>';
    }).join('');
  }

  window.toggleChannelEnabled = function (id, enabled) {
    apiFetch('/api/settings/alert-channels/' + id, 'PUT', { is_enabled: enabled })
      .then(loadChannels).catch(function () { loadChannels(); });
  };

  window.toggleChannelSev = function (id, field, value, el) {
    var payload = field === 'critical'
      ? { send_on_critical: value }
      : { send_on_warning: value };
    if (el) { el.classList.toggle('on', value); }
    apiFetch('/api/settings/alert-channels/' + id, 'PUT', payload)
      .catch(function () { loadChannels(); });
  };

  window.deleteChannel = function (id, name) {
    if (!confirm('Delete channel "' + name + '"?')) return;
    apiFetch('/api/settings/alert-channels/' + id, 'DELETE', null)
      .then(loadChannels)
      .catch(function () { alert('Delete failed.'); });
  };

  window.editChannel = function (id) {
    fetch('/api/settings/alert-channels', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (channels) {
        var ch = channels.find(function (c) { return c.id === id; });
        if (!ch) return;
        openChannelModal(ch);
      });
  };

  window.openNewChannelModal = function () { openChannelModal(null); };

  function openChannelModal(ch) {
    document.getElementById('editChannelId').value  = ch ? ch.id : '';
    document.getElementById('channelModalTitle').textContent = ch ? 'Edit Channel' : 'New Alert Channel';
    document.getElementById('chName').value         = ch ? ch.name : '';
    document.getElementById('chCritical').checked   = ch ? ch.send_on_critical : true;
    document.getElementById('chWarning').checked    = ch ? ch.send_on_warning  : false;
    document.getElementById('chEnabled').checked    = ch ? ch.is_enabled       : true;
    document.getElementById('channelModalError').style.display = 'none';

    var type = ch ? ch.channel_type : 'email';
    document.getElementById('chType').value = type;
    if (ch && ch.config_json) {
      document.getElementById('chRecipients').value = (ch.config_json.recipients || []).join(', ');
      document.getElementById('chWebhook').value    = ch.config_json.webhook_url || '';
    } else {
      document.getElementById('chRecipients').value = '';
      document.getElementById('chWebhook').value    = '';
    }
    updateChannelFields();

    var m = document.getElementById('channelModal');
    if (m) bootstrap.Modal.getOrCreateInstance(m).show();
  }

  window.updateChannelFields = function () {
    var t = document.getElementById('chType').value;
    document.getElementById('chEmailFields').style.display   = t === 'email' ? '' : 'none';
    document.getElementById('chWebhookFields').style.display = t !== 'email' ? '' : 'none';
  };

  window.saveChannel = function () {
    var btn = document.getElementById('saveChannelBtn');
    var errEl = document.getElementById('channelModalError');
    errEl.style.display = 'none';
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

    var id   = document.getElementById('editChannelId').value;
    var type = document.getElementById('chType').value;
    var cfg  = {};
    if (type === 'email') {
      cfg.recipients = document.getElementById('chRecipients').value
        .split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    } else {
      cfg.webhook_url = document.getElementById('chWebhook').value.trim();
    }

    var payload = {
      name:             document.getElementById('chName').value.trim(),
      channel_type:     type,
      config_json:      cfg,
      send_on_critical: document.getElementById('chCritical').checked,
      send_on_warning:  document.getElementById('chWarning').checked,
      is_enabled:       document.getElementById('chEnabled').checked,
    };

    var method = id ? 'PUT' : 'POST';
    var url    = id ? '/api/settings/alert-channels/' + id : '/api/settings/alert-channels';

    apiFetch(url, method, payload)
      .then(function () {
        var m = document.getElementById('channelModal');
        if (m) bootstrap.Modal.getOrCreateInstance(m).hide();
        loadChannels();
      })
      .catch(function (err) {
        errEl.textContent   = err.message || 'Save failed.';
        errEl.style.display = '';
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
      });
  };

  /* ── SMTP form ── */

  var _smtpData = null; // cached so save only sends changed keys

  function loadSmtpForm() {
    var el = document.getElementById('smtpForm');
    if (!el) return;
    el.innerHTML = '<div class="text-muted" style="font-size:.82rem">Loading…</div>';
    fetch('/api/settings/smtp', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _smtpData = data.smtp || {};
        renderSmtpForm(_smtpData);
      })
      .catch(function () {
        el.innerHTML = '<div class="text-danger" style="font-size:.8rem">Failed to load SMTP config.</div>';
      });
  }

  function smtpVal(key) {
    return (_smtpData && _smtpData[key] && _smtpData[key].value) || '';
  }

  function renderSmtpForm(smtp) {
    var el = document.getElementById('smtpForm');
    if (!el) return;
    var srcNote = function (key) {
      var src = smtp[key] && smtp[key].source;
      if (src === 'environment') return '<small class="text-muted ms-1">(env)</small>';
      if (src === 'default')     return '<small class="text-muted ms-1">(default)</small>';
      return '';
    };
    el.innerHTML = [
      '<div class="mb-2">',
      '  <label class="form-label mb-0" style="font-size:.75rem">SMTP Server' + srcNote('smtp_server') + '</label>',
      '  <div class="input-group input-group-sm">',
      '    <input type="text" class="form-control" id="smtp_server" value="' + esc(smtpVal('smtp_server')) + '" placeholder="smtp.gmail.com">',
      '    <input type="number" class="form-control" id="smtp_port" value="' + esc(smtpVal('smtp_port') || '587') + '" style="max-width:72px" placeholder="587">',
      '  </div>',
      '</div>',
      '<div class="mb-2">',
      '  <label class="form-label mb-0" style="font-size:.75rem">Username' + srcNote('smtp_user') + '</label>',
      '  <input type="text" class="form-control form-control-sm" id="smtp_user" value="' + esc(smtpVal('smtp_user')) + '" autocomplete="off">',
      '</div>',
      '<div class="mb-2">',
      '  <label class="form-label mb-0" style="font-size:.75rem">Password' + srcNote('smtp_password') + '</label>',
      '  <div class="input-group input-group-sm">',
      '    <input type="password" class="form-control" id="smtp_password" value="' + esc(smtpVal('smtp_password')) + '" placeholder="leave blank to keep current" autocomplete="new-password">',
      '    <button class="tactical-btn tactical-btn-sm tactical-btn-outline" type="button"',
      '            onclick="togglePwVis(\'smtp_password\',this)" style="padding:.25rem .5rem">',
      '      <i class="fas fa-eye"></i></button>',
      '  </div>',
      '</div>',
      '<div class="mb-2">',
      '  <label class="form-label mb-0" style="font-size:.75rem">From Address' + srcNote('smtp_from') + '</label>',
      '  <input type="email" class="form-control form-control-sm" id="smtp_from" value="' + esc(smtpVal('smtp_from')) + '" placeholder="nms@company.com">',
      '</div>',
      '<div class="mb-2">',
      '  <label class="form-label mb-0" style="font-size:.75rem">Alert Recipients' + srcNote('smtp_recipients') + '</label>',
      '  <input type="text" class="form-control form-control-sm" id="smtp_recipients" value="' + esc(smtpVal('smtp_recipients')) + '" placeholder="ops@company.com, team@company.com">',
      '</div>',
      '<div class="mb-3 form-check form-switch">',
      '  <input class="form-check-input" type="checkbox" id="smtp_use_tls" ' + (smtpVal('smtp_use_tls') !== 'false' ? 'checked' : '') + '>',
      '  <label class="form-check-label" style="font-size:.82rem">Use TLS</label>',
      '</div>',
      '<div id="smtpSaveMsg" style="font-size:.8rem;min-height:1.2rem"></div>',
      '<div class="d-flex gap-2">',
      '  <button class="tactical-btn tactical-btn-sm" onclick="saveSmtp()" id="smtpSaveBtn">Save</button>',
      '  <button class="tactical-btn tactical-btn-sm tactical-btn-outline" onclick="testSmtp()">Test</button>',
      '</div>',
    ].join('\n');
  }

  window.togglePwVis = function (fieldId, btn) {
    var f = document.getElementById(fieldId);
    if (!f) return;
    f.type = f.type === 'password' ? 'text' : 'password';
    var icon = btn.querySelector('i');
    if (icon) icon.className = 'fas fa-eye' + (f.type === 'text' ? '-slash' : '');
  };

  window.saveSmtp = function () {
    var btn = document.getElementById('smtpSaveBtn');
    var msg = document.getElementById('smtpSaveMsg');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    if (msg) { msg.textContent = ''; msg.className = ''; }

    var pw = document.getElementById('smtp_password').value;
    var payload = {
      smtp_server:     document.getElementById('smtp_server').value.trim(),
      smtp_port:       document.getElementById('smtp_port').value.trim(),
      smtp_user:       document.getElementById('smtp_user').value.trim(),
      smtp_from:       document.getElementById('smtp_from').value.trim(),
      smtp_recipients: document.getElementById('smtp_recipients').value.trim(),
      smtp_use_tls:    document.getElementById('smtp_use_tls').checked ? 'true' : 'false',
    };
    if (pw && pw.indexOf('••') === -1) payload.smtp_password = pw;

    apiFetch('/api/settings/smtp', 'POST', payload)
      .then(function () {
        if (msg) { msg.textContent = '✓ Saved'; msg.style.color = '#86efac'; }
        setTimeout(function () { if (msg) msg.textContent = ''; }, 3000);
      })
      .catch(function (err) {
        if (msg) { msg.textContent = '✗ ' + (err.message || 'Save failed'); msg.style.color = '#fca5a5'; }
      })
      .finally(function () { if (btn) { btn.disabled = false; btn.textContent = 'Save'; } });
  };

  window.testSmtp = function () {
    var msg = document.getElementById('smtpSaveMsg');
    if (msg) { msg.textContent = 'Sending test…'; msg.style.color = '#9ca3af'; }
    apiFetch('/api/settings/smtp/test', 'POST', {})
      .then(function (d) {
        if (msg) {
          msg.textContent = '✓ ' + (d.message || 'Test sent successfully');
          msg.style.color = '#86efac';
        }
      })
      .catch(function (err) {
        if (msg) { msg.textContent = '✗ ' + (err.message || 'Test failed'); msg.style.color = '#fca5a5'; }
      });
  };

  /* ── Shared fetch helper ── */
  function apiFetch(url, method, body) {
    var opts = {
      method: method || 'GET',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== null && body !== undefined && method !== 'DELETE') {
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || d.message || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  /* ──────────────────────────────────────────────────────────
     INIT
  ────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', loadAlerts);
})();
