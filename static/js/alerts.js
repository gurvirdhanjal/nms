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

  function formatTime(iso) {
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
            ? '<button class="tactical-btn tactical-btn-sm tactical-btn-outline" onclick="resolveAlert(\'' + a.alert_id + '\', this)">Resolve</button>'
            : '';
          var statusBadge = resolved
            ? '<span class="ops-badge" style="background:rgba(100,100,120,0.2);color:#9ca3af">Resolved</span>'
            : '<span class="ops-badge severity-warning">Active</span>';
          return '<tr>'
            + '<td><span class="ops-badge ' + severityClass(a.severity) + '">' + a.severity + '</span></td>'
            + '<td><strong>' + (a.device_name || '—') + '</strong><br><small class="ip-pill">' + (a.device_ip || '') + '</small></td>'
            + '<td class="text-muted">' + (a.dept_name || '—') + '</td>'
            + '<td>' + (a.message || '—') + '</td>'
            + '<td><code>' + (a.metric_name || '—') + '</code></td>'
            + '<td style="font-size:0.78rem;font-family:monospace">' + formatTime(a.timestamp) + '</td>'
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

  document.addEventListener('DOMContentLoaded', loadAlerts);
})();
