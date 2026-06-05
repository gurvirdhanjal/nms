(function () {
  'use strict';

  var root = document.querySelector('[data-site-id]');
  if (!root) return;

  var SITE_ID   = root.dataset.siteId;
  var STATS_URL = '/api/sites/' + SITE_ID + '/dashboard-stats';
  var MODAL_URL = function (deviceId) { return '/api/sites/' + SITE_ID + '/device/' + deviceId + '/modal'; };
  var pollTimer = null;
  var currentModalDeviceId = null;

  var KPI = {
    total:   document.getElementById('siteKpiTotal'),
    online:  document.getElementById('siteKpiOnline'),
    offline: document.getElementById('siteKpiOffline'),
    alerts:  document.getElementById('siteKpiAlerts'),
  };

  var freshnessText  = document.getElementById('dash-freshness-text');
  var freshnessDot   = document.querySelector('.dash-live-dot');
  var alertBanner    = document.getElementById('dash-alert-banner');
  var alertBannerTxt = document.getElementById('dash-alert-banner-text');

  function ageSeconds(isoString) {
    if (!isoString) return null;
    var ts = isoString.endsWith('Z') ? isoString : isoString + 'Z';
    return (Date.now() - new Date(ts).getTime()) / 1000;
  }

  function staleClass(lastScanAt, intervalS) {
    var age = ageSeconds(lastScanAt);
    if (age === null) return 'ping-unknown';
    if (age > intervalS * 5) return 'ping-critical';
    if (age > intervalS * 2) return 'ping-stale';
    return 'ping-fresh';
  }

  function formatAge(lastScanAt) {
    var age = ageSeconds(lastScanAt);
    if (age === null) return '—';
    if (age < 60)   return Math.round(age) + 's ago';
    if (age < 3600) return Math.round(age / 60) + 'm ago';
    return Math.round(age / 3600) + 'h ago';
  }

  function formatPing(ms) {
    if (ms == null) return '—';
    return Math.round(ms) + ' ms';
  }

  function timeLabel(isoString) {
    if (!isoString) return '—';
    var ts = isoString.endsWith('Z') ? isoString : isoString + 'Z';
    return new Date(ts).toLocaleTimeString();
  }

  function updateKpis(stats) {
    if (!stats) return;
    if (KPI.total   && stats.device_count  != null) KPI.total.textContent   = stats.device_count;
    if (KPI.online  && stats.online_count  != null) KPI.online.textContent  = stats.online_count;
    if (KPI.offline && stats.offline_count != null) KPI.offline.textContent = stats.offline_count;
    if (KPI.alerts  && stats.warning_count != null) KPI.alerts.textContent  = stats.warning_count;
  }

  function updateDeptCards(deptAggregates) {
    if (!Array.isArray(deptAggregates)) return;
    deptAggregates.forEach(function (d) {
      var card = document.querySelector('.dept-score-card[data-dept-id="' + d.dept_id + '"]');
      if (!card) return;
      var pctEl    = document.getElementById('dept-pct-'    + d.dept_id);
      var onlineEl = document.getElementById('dept-online-' + d.dept_id);
      var alertsEl = document.getElementById('dept-alerts-' + d.dept_id);
      if (pctEl)    pctEl.textContent = d.health_pct + '%';
      if (onlineEl) onlineEl.textContent = d.online;
      if (alertsEl) {
        if (d.alerts > 0) {
          alertsEl.textContent = d.alerts + '!';
          alertsEl.style.display = '';
        } else {
          alertsEl.style.display = 'none';
        }
      }
      card.dataset.healthPct = d.health_pct;
    });
  }

  function updateDeviceRows(devices, intervalS) {
    if (!Array.isArray(devices)) return;
    devices.forEach(function (d) {
      var rows = root.querySelectorAll('tr[data-device-id="' + d.device_id + '"]');
      rows.forEach(function (row) {
        var dot = row.querySelector('[data-device-status]');
        if (dot) {
          var isOnline = d.state === 'healthy' || d.state === 'degraded';
          dot.className = 'dash-status-dot ' + (isOnline ? 'dot-online' : 'dot-offline');
          dot.dataset.deviceStatus = isOnline ? 'online' : 'offline';
        }

        var pingCell = row.querySelector('[data-ping-cell]');
        if (pingCell) {
          pingCell.className  = d.ping_ms != null ? 'ping-fresh' : 'ping-unknown';
          pingCell.textContent = formatPing(d.ping_ms);
        }

        var lossCell = row.querySelector('[data-loss-cell]');
        if (lossCell) {
          var loss = d.packet_loss;
          lossCell.textContent = loss != null ? loss.toFixed(1) + '%' : '—';
          lossCell.className   = loss > 5 ? 'ping-stale' : (loss > 0 ? 'ping-fresh' : 'text-muted');
        }

        var lastCell = row.querySelector('[data-last-ping]');
        if (lastCell) {
          var sc = staleClass(d.last_scan_at, intervalS);
          lastCell.className   = sc;
          lastCell.textContent = formatAge(d.last_scan_at);
          lastCell.title       = formatPing(d.ping_ms) + (d.last_scan_at ? ' · ' + timeLabel(d.last_scan_at) : '');
        }
      });
    });
  }

  function updateFreshnessBar(generatedAt) {
    if (!freshnessText) return;
    freshnessText.textContent = generatedAt ? 'Live · ' + timeLabel(generatedAt) : 'Live';
    if (freshnessDot) freshnessDot.style.background = '';
  }

  function markFreshnessError() {
    if (!freshnessText) return;
    freshnessText.textContent = 'Update failed — retrying…';
    if (freshnessDot) freshnessDot.style.background = '#f59e0b';
  }

  function updateAlertBanner(activeCount) {
    if (!alertBanner) return;
    if (activeCount > 0) {
      alertBanner.style.display = 'flex';
      if (alertBannerTxt) alertBannerTxt.textContent = 'Active Alerts: ' + activeCount + ' unresolved';
    } else {
      alertBanner.style.display = 'none';
    }
  }

  function poll() {
    fetch(STATS_URL, { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        var intervalS = data.monitoring_interval_s || 15;
        updateKpis(data.stats);
        updateDeptCards(data.dept_aggregates);
        updateDeviceRows(data.devices, intervalS);
        updateFreshnessBar(data.generated_at);
        updateAlertBanner(data.active_alert_count || 0);
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, Math.max(intervalS * 2, 20) * 1000);
      })
      .catch(function () {
        markFreshnessError();
        clearTimeout(pollTimer);
        pollTimer = setTimeout(poll, 60000);
      });
  }

  var searchInput = document.getElementById('dashDeviceSearch');
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      var q = this.value.trim().toLowerCase();
      root.querySelectorAll('.dept-device-row').forEach(function (row) {
        var name = (row.dataset.deviceName || '').toLowerCase();
        var ip   = (row.dataset.deviceIp   || '').toLowerCase();
        var type = (row.dataset.deviceType || '').toLowerCase();
        row.style.display = (!q || name.includes(q) || ip.includes(q) || type.includes(q)) ? '' : 'none';
      });
    });
  }

  root.addEventListener('click', function (e) {
    var row = e.target.closest('.dept-device-row');
    if (!row) return;
    if (e.target.closest('.dept-device-ext-link') || e.target.closest('.dept-alert-chip')) return;
    var deviceId = parseInt(row.dataset.deviceId, 10);
    if (deviceId) openDeviceModal(deviceId, false);
  });

  var modalOverlay    = document.getElementById('device-modal-overlay');
  var modalTitle      = document.getElementById('modal-device-name');
  var modalStateDot   = document.getElementById('modal-status-dot');
  var modalStateBadge = document.getElementById('modal-state-badge');
  var modalMeta       = document.getElementById('modal-device-meta');
  var modalBody       = document.getElementById('modal-body');
  var modalBtnDevice  = document.getElementById('modal-btn-device');
  var modalBtnFloor   = document.getElementById('modal-btn-floorplan');
  var modalBtnPing    = document.getElementById('modal-btn-ping');

  window.openDeviceModal = function (deviceId, focusAlerts) {
    currentModalDeviceId = deviceId;
    if (modalTitle)  modalTitle.textContent = 'Loading…';
    if (modalBody)   modalBody.innerHTML    = '<div class="text-muted text-center py-4">Loading device data…</div>';
    if (modalBtnFloor) modalBtnFloor.style.display = 'none';
    if (modalOverlay)  modalOverlay.style.display  = 'flex';

    fetch(MODAL_URL(deviceId), { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) { renderModal(data, focusAlerts); })
      .catch(function () {
        if (modalBody) modalBody.innerHTML =
          '<div class="text-danger text-center py-4">Unable to load device data. ' +
          '<a href="#" onclick="openDeviceModal(' + deviceId + ', false)">Retry?</a></div>';
      });
  };

  function renderModal(data, focusAlerts) {
    var dev    = data.device  || {};
    var net    = data.network || {};
    var health = data.health  || {};
    var alerts = data.active_alerts || [];
    var fp     = data.floor_plan_placement || {};

    if (modalTitle) modalTitle.textContent = dev.device_name || 'Unknown';
    if (modalStateDot) {
      var dotMap = { healthy: 'dot-online', degraded: 'dot-degraded', offline: 'dot-offline' };
      modalStateDot.className = 'dash-status-dot ' + (dotMap[net.state] || 'dot-unknown');
    }
    if (modalStateBadge) {
      var lblMap   = { healthy: 'Online', degraded: 'Degraded', offline: 'Offline', unknown: 'Unknown' };
      var clsMap   = { healthy: 'status-healthy', degraded: 'severity-warning', offline: 'status-offline' };
      modalStateBadge.textContent = lblMap[net.state] || 'Unknown';
      modalStateBadge.className   = 'ops-badge ' + (clsMap[net.state] || 'severity-warning');
    }

    var metaParts = [dev.device_type, dev.device_ip, dev.dept_name].filter(Boolean);
    if (modalMeta) modalMeta.textContent = metaParts.join(' · ');

    if (modalBtnDevice) modalBtnDevice.href = '/devices/' + dev.device_id + '/details';
    if (fp.has_placement && modalBtnFloor) {
      modalBtnFloor.href = '/sites/' + dev.site_id + '/floor-plans';
      modalBtnFloor.style.display = '';
    }

    var html = '';

    html += '<div class="device-modal-section-title">Network</div>';
    html += '<div class="device-modal-grid">'
      + '<div class="device-modal-stat"><span class="device-modal-stat-val ' + (net.state === 'offline' ? 'text-danger' : 'text-success') + '">' + (net.state || '—') + '</span><span class="device-modal-stat-lbl">Status</span></div>'
      + '<div class="device-modal-stat"><span class="device-modal-stat-val">' + formatPing(net.ping_ms) + '</span><span class="device-modal-stat-lbl">Ping</span></div>'
      + '<div class="device-modal-stat"><span class="device-modal-stat-val ' + (net.packet_loss > 5 ? 'text-warning' : '') + '">' + (net.packet_loss != null ? net.packet_loss.toFixed(1) + '%' : '—') + '</span><span class="device-modal-stat-lbl">Pkt Loss</span></div>'
      + '</div>';
    if (net.last_scan_at) {
      html += '<div class="text-muted mb-3" style="font-size:0.75rem">Last scan: ' + formatAge(net.last_scan_at) + '</div>';
    }

    html += '<div class="device-modal-section-title">Server Health</div>';
    if (health.available) {
      function healthBar(label, pct) {
        var pctVal  = pct != null ? pct : 0;
        var color   = pctVal > 90 ? '#ef4444' : pctVal > 75 ? '#f59e0b' : '#22c55e';
        return '<div class="modal-health-row">'
          + '<span class="modal-health-label">' + label + '</span>'
          + '<div class="modal-health-bar"><div class="modal-health-fill" style="width:' + Math.min(pctVal,100) + '%;background:' + color + '"></div></div>'
          + '<span class="modal-health-val" style="color:' + color + '">' + (pct != null ? pct.toFixed(1) + '%' : '—') + '</span>'
          + '</div>';
      }
      html += healthBar('CPU',  health.cpu_pct);
      html += healthBar('RAM',  health.memory_pct);
      html += healthBar('Disk', health.disk_pct);
    } else {
      html += '<div class="text-muted mb-3" style="font-size:0.82rem">No health data — agent not installed or not reporting.</div>';
    }

    html += '<div class="device-modal-section-title" id="modal-alerts-section">Active Alerts (' + alerts.length + ')</div>';
    if (alerts.length > 0) {
      alerts.forEach(function (a) {
        var sevClass = a.severity === 'CRITICAL' ? 'severity-critical' : a.severity === 'WARNING' ? 'severity-warning' : 'severity-info';
        var timeStr  = a.timestamp ? new Date(a.timestamp.endsWith('Z') ? a.timestamp : a.timestamp + 'Z').toLocaleTimeString() : '—';
        html += '<div class="device-modal-alert-row">'
          + '<span class="ops-badge ' + sevClass + '">' + a.severity + '</span>'
          + '<span>' + (a.message || a.metric_name || '—') + '</span>'
          + '<span class="device-modal-alert-time">' + timeStr + '</span>'
          + '</div>';
      });
    } else {
      html += '<div class="text-muted mb-2" style="font-size:0.82rem">No active alerts for this device.</div>';
    }

    if (modalBody) modalBody.innerHTML = html;

    if (focusAlerts) {
      var alertsSection = document.getElementById('modal-alerts-section');
      if (alertsSection) setTimeout(function () { alertsSection.scrollIntoView({ behavior: 'smooth' }); }, 100);
    }
  }

  window.closeDeviceModal = function (event) {
    if (event && event.target !== modalOverlay) return;
    if (modalOverlay) modalOverlay.style.display = 'none';
    currentModalDeviceId = null;
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modalOverlay && modalOverlay.style.display !== 'none') {
      modalOverlay.style.display = 'none';
      currentModalDeviceId = null;
    }
  });

  window.pingDeviceFromModal = function () {
    if (!currentModalDeviceId) return;
    var btn = modalBtnPing;
    if (btn) { btn.disabled = true; btn.textContent = 'Pinging…'; }
    fetch(STATS_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var intervalS = data.monitoring_interval_s || 15;
        updateDeviceRows(data.devices, intervalS);
        if (currentModalDeviceId) openDeviceModal(currentModalDeviceId, false);
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Ping now'; }
      });
  };

  document.addEventListener('DOMContentLoaded', function () { poll(); });
})();
