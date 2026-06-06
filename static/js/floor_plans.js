/* Floor-plan geotagging UI
 *
 * - Lists a site's floor plans; renders the selected plan with the image
 *   auto-fitted to the canvas and zoom controls (+/-/Fit, ctrl+wheel).
 * - Markers are icon-only (device-type glyph + status color + connection + lock
 *   badges); the full hostname lives in the tooltip / hover label.
 * - Admins drag devices from the "unplaced" tray onto the plan and reposition
 *   markers (Pointer Events → works with mouse AND touch). Locked markers are
 *   not moved by drag.
 * - Clicking a marker opens a details popup (no forced navigation).
 * - Search + type/offline filters narrow both the tray and the markers.
 * - Live status: ONE batched poll of /api/monitoring/status for the whole floor.
 *
 * Coordinates are stored as 0-100 (percent) and map directly to CSS left/top %.
 */
(function () {
  'use strict';

  const root = document.getElementById('fpRoot');
  if (!root) return;
  const SITE_ID = root.dataset.siteId;
  const IS_ADMIN = root.dataset.isAdmin === 'true';
  const STATUS_POLL_MS = 20000;
  const DRAG_THRESHOLD = 4; // px before a press becomes a drag (vs a click)

  const els = {
    planList: document.getElementById('fpPlanList'),
    unplaced: document.getElementById('fpUnplaced'),
    unplacedHint: document.getElementById('fpUnplacedHint'),
    stageOuter: document.getElementById('fpStageOuter'),
    stage: document.getElementById('fpStage'),
    stageEmpty: document.getElementById('fpStageEmpty'),
    planTitle: document.getElementById('fpPlanTitle'),
    planActions: document.getElementById('fpPlanActions'),
    zoom: document.getElementById('fpZoom'),
    search: document.getElementById('fpSearch'),
    typeFilter: document.getElementById('fpTypeFilter'),
    offlineOnly: document.getElementById('fpOfflineOnly'),
  };

  let plans = [];
  let siteDevices = [];
  let currentPlan = null;
  let statusTimer = null;
  let suggestionsByDevice = {};
  let statusByDevice = {};        // device_id -> 'Online'|'Offline'|...
  let zoom = 1;
  let popupEl = null;

  // ---- helpers --------------------------------------------------------------
  function toast(msg, type) {
    if (window.UI && window.UI.Toast && window.UI.Toast.show) window.UI.Toast.show(msg, type || 'info');
    else console.log('[floor-plans]', type || 'info', msg);
  }

  async function api(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
    let body = null;
    try { body = await res.json(); } catch (e) { /* image/no-body */ }
    if (!res.ok) throw new Error((body && (body.message || body.error)) || ('HTTP ' + res.status));
    return body;
  }

  function statusClass(status) {
    const s = String(status || 'unknown').toLowerCase();
    if (s === 'online') return 'st-online';
    if (s === 'offline') return 'st-offline';
    if (s === 'maintenance') return 'st-maintenance';
    return 'st-unknown';
  }

  function deviceTypeIcon(type) {
    const t = String(type || '').toLowerCase();
    // NOTE: Font Awesome is pinned at 6.0.0 — only use glyphs present in 6.0.0.
    if (t.indexOf('print') >= 0) return 'fa-print';
    if (t.indexOf('server') >= 0) return 'fa-server';
    if (t.indexOf('switch') >= 0) return 'fa-network-wired';
    if (t.indexOf('router') >= 0) return 'fa-route';
    if (t.indexOf('firewall') >= 0) return 'fa-shield';
    if (t.indexOf('access') >= 0 || t === 'ap' || t.indexOf('wireless') >= 0) return 'fa-wifi';
    if (t.indexOf('camera') >= 0 || t.indexOf('iot') >= 0) return 'fa-video';
    if (t.indexOf('workstation') >= 0 || t.indexOf('pc') >= 0 || t.indexOf('desktop') >= 0 ||
        t.indexOf('laptop') >= 0) return 'fa-desktop';
    return 'fa-circle';
  }

  function connIcon(type, cls) {
    const t = String(type || '').toLowerCase();
    if (t === 'wifi') return `<i class="fas fa-wifi ${cls || ''}" title="WiFi"></i>`;
    if (t === 'lan') return `<i class="fas fa-network-wired ${cls || ''}" title="Wired LAN"></i>`;
    return '';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ---- plan list ------------------------------------------------------------
  async function loadPlans() {
    const r = await api(`/api/sites/${SITE_ID}/floor-plans`);
    plans = (r && r.data) || [];
    renderPlanList();
    if (plans.length && !currentPlan) selectPlan(plans[0].id);
    else if (!plans.length) {
      els.planList.innerHTML = '<div class="text-muted small">No plans yet.'
        + (IS_ADMIN ? ' Click “Upload plan”.' : '') + '</div>';
    }
  }

  function renderPlanList() {
    if (!plans.length) return;
    els.planList.innerHTML = '';
    plans.forEach(function (p) {
      const item = document.createElement('div');
      item.className = 'fp-plan-item' + (currentPlan && currentPlan.id === p.id ? ' active' : '');
      item.innerHTML = `<span><i class="fas fa-layer-group me-2"></i>${escapeHtml(p.name)}`
        + (p.version > 1 ? ` <small>v${p.version}</small>` : '')
        + `</span><small>${p.device_count || 0}</small>`;
      item.addEventListener('click', function () { selectPlan(p.id); });
      els.planList.appendChild(item);
    });
  }

  // ---- plan selection + render ---------------------------------------------
  async function selectPlan(planId) {
    stopStatusPolling();
    closePopup();
    const r = await api(`/api/floor-plans/${planId}`);
    currentPlan = r.data;
    renderPlanList();
    await loadSiteDevices();
    await loadSuggestions();
    renderStage();
    renderUnplaced();
    renderPlanActions();
    populateTypeFilter();
    startStatusPolling();
  }

  async function loadSiteDevices() {
    const r = await api(`/api/sites/${SITE_ID}/placeable-devices`);
    siteDevices = (r && r.data) || [];
  }

  async function loadSuggestions() {
    suggestionsByDevice = {};
    try {
      const r = await api(`/api/floor-plans/${currentPlan.id}/suggestions`);
      (r.data || []).forEach(function (s) { suggestionsByDevice[s.device_id] = s; });
    } catch (e) { /* non-fatal */ }
  }

  function renderStage() {
    const stage = els.stage;
    stage.innerHTML = '';
    els.zoom.style.display = 'none';
    if (!currentPlan) {
      stage.innerHTML = '<div class="fp-empty">Select or upload a floor plan to begin.</div>';
      els.planTitle.textContent = 'No plan selected';
      return;
    }
    els.planTitle.textContent = currentPlan.name + (currentPlan.version > 1 ? ` (v${currentPlan.version})` : '');
    els.zoom.style.display = 'flex';

    const img = document.createElement('img');
    img.src = currentPlan.image_url;
    img.alt = currentPlan.name;
    img.id = 'fpStageImg';
    img.addEventListener('load', fitZoom);
    stage.appendChild(img);

    (currentPlan.placed_devices || []).forEach(addMarker);
    if (IS_ADMIN) enableStageDrop(stage);
    enablePan();
  }

  function placedById(id) {
    return (currentPlan.placed_devices || []).find(function (d) { return d.device_id === id; });
  }

  function addMarker(dev) {
    const m = document.createElement('div');
    m.className = 'fp-marker ' + statusClass(statusByDevice[dev.device_id]) + (dev.map_locked ? ' locked' : '');
    m.dataset.deviceId = dev.device_id;
    m.style.left = (dev.map_x || 0) + '%';
    m.style.top = (dev.map_y || 0) + '%';
    m.style.transform = 'translate(-50%, -50%) rotate(' + (dev.map_rotation || 0) + 'deg)';
    m.innerHTML = `<i class="fas ${deviceTypeIcon(dev.device_type)}"></i>`
      + (dev.map_locked ? '<i class="fas fa-lock fp-lock"></i>' : '')
      + connIcon(dev.connection_type, 'fp-conn')
      + `<span class="fp-hoverlabel">${escapeHtml(dev.device_name)}</span>`;
    m.title = dev.device_name + (dev.device_ip ? ' · ' + dev.device_ip : '');
    attachMarkerInteractions(m, dev);
    els.stage.appendChild(m);
  }

  // ---- unplaced tray --------------------------------------------------------
  function renderUnplaced() {
    if (!IS_ADMIN || !els.unplaced) return;
    const placedIds = new Set((currentPlan.placed_devices || []).map(function (d) { return d.device_id; }));
    const unplaced = siteDevices.filter(function (d) { return !placedIds.has(d.device_id) && !d.floor_plan_id; });
    els.unplacedHint.textContent = unplaced.length ? 'Drag onto the plan:' : 'All site devices are placed.';
    els.unplaced.innerHTML = '';
    unplaced.forEach(function (d) {
      const sugg = suggestionsByDevice[d.device_id];
      const chip = document.createElement('div');
      chip.className = 'fp-chip';
      chip.draggable = true;
      chip.dataset.deviceId = d.device_id;
      chip.dataset.name = (d.device_name || '').toLowerCase();
      let html = `<i class="fas ${deviceTypeIcon(d.device_type)}" style="color:#7c8aa5;"></i>`
        + `<span>${escapeHtml(d.device_name)}</span>`;
      if (connIcon(d.connection_type)) html += `<span class="ms-auto">${connIcon(d.connection_type)}</span>`;
      chip.innerHTML = html;
      if (sugg) {
        const hint = document.createElement('div');
        hint.style.cssText = 'font-size:10px;color:#7c8aa5;width:100%;margin-top:3px;display:flex;justify-content:space-between;align-items:center;';
        hint.innerHTML = `<span title="Downstream of ${escapeHtml(sugg.parent_switch_name)}">↳ ${escapeHtml(sugg.parent_switch_name)}</span>`;
        const btn = document.createElement('button');
        btn.className = 'tactical-btn tactical-btn-outline btn-sm';
        btn.style.cssText = 'padding:1px 7px;font-size:10px;';
        btn.textContent = 'Place near';
        btn.addEventListener('click', function (e) { e.stopPropagation(); placeNearSwitch(d.device_id, sugg); });
        hint.appendChild(btn);
        chip.appendChild(hint);
      }
      chip.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', String(d.device_id));
        e.dataTransfer.effectAllowed = 'copy';
      });
      els.unplaced.appendChild(chip);
    });
    applyFilters();
  }

  let _nearCounter = 0;
  function placeNearSwitch(deviceId, sugg) {
    const baseX = (sugg.switch_x != null) ? sugg.switch_x : 50;
    const baseY = (sugg.switch_y != null) ? sugg.switch_y : 50;
    const angle = (_nearCounter++ * 47) % 360;
    const rad = angle * Math.PI / 180;
    savePlacements([{ device_id: deviceId, map_x: clamp(baseX + Math.cos(rad) * 5), map_y: clamp(baseY + Math.sin(rad) * 5) }]);
  }

  // ---- drag from tray (HTML5 DnD) ------------------------------------------
  function enableStageDrop(stage) {
    stage.addEventListener('dragover', function (e) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; stage.classList.add('dragover'); });
    stage.addEventListener('dragleave', function () { stage.classList.remove('dragover'); });
    stage.addEventListener('drop', function (e) {
      e.preventDefault();
      stage.classList.remove('dragover');
      const deviceId = parseInt(e.dataTransfer.getData('text/plain'), 10);
      if (!deviceId) return;
      const pct = pointToPercent(e.clientX, e.clientY);
      savePlacements([{ device_id: deviceId, map_x: pct.x, map_y: pct.y }]);
    });
  }

  function pointToPercent(clientX, clientY) {
    const img = document.getElementById('fpStageImg') || els.stage;
    const rect = img.getBoundingClientRect();
    return { x: clamp(((clientX - rect.left) / rect.width) * 100), y: clamp(((clientY - rect.top) / rect.height) * 100) };
  }
  function clamp(v) { return Math.max(0, Math.min(100, v)); }

  // ---- marker interactions (Pointer Events: mouse + touch) ------------------
  function attachMarkerInteractions(marker, dev) {
    let start = null, moved = false;
    marker.addEventListener('pointerdown', function (e) {
      if (e.button != null && e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY };
      moved = false;
      // Only admins on unlocked markers can drag; everyone can click for details.
      if (IS_ADMIN && !dev.map_locked) {
        marker.setPointerCapture(e.pointerId);
      }
      e.stopPropagation();
    });
    marker.addEventListener('pointermove', function (e) {
      if (!start) return;
      if (Math.abs(e.clientX - start.x) > DRAG_THRESHOLD || Math.abs(e.clientY - start.y) > DRAG_THRESHOLD) moved = true;
      if (moved && IS_ADMIN && !dev.map_locked && marker.hasPointerCapture(e.pointerId)) {
        const pct = pointToPercent(e.clientX, e.clientY);
        marker.style.left = pct.x + '%';
        marker.style.top = pct.y + '%';
        marker.style.transform = 'translate(-50%, -50%) rotate(' + (dev.map_rotation || 0) + 'deg)';
        marker._last = pct;
      }
    });
    marker.addEventListener('pointerup', function (e) {
      if (!start) return;
      const wasMoved = moved;
      start = null;
      try { marker.releasePointerCapture(e.pointerId); } catch (_) {}
      if (wasMoved && IS_ADMIN && !dev.map_locked && marker._last) {
        savePlacements([{ device_id: dev.device_id, map_x: marker._last.x, map_y: marker._last.y }]);
      } else if (!wasMoved) {
        openPopup(marker, dev);
      }
    });
  }

  // ---- detail popup ---------------------------------------------------------
  function openPopup(marker, dev) {
    closePopup();
    const status = statusByDevice[dev.device_id] || 'Unknown';
    const href = `/devices/${dev.device_id}/details`;
    const p = document.createElement('div');
    p.className = 'fp-popup';
    p.innerHTML =
      `<span class="fp-close" title="Close">&times;</span>`
      + `<h6><i class="fas ${deviceTypeIcon(dev.device_type)}"></i> ${escapeHtml(dev.device_name)}</h6>`
      + `<div class="row"><span>Status</span><span>${escapeHtml(status)}</span></div>`
      + `<div class="row"><span>Type</span><span>${escapeHtml(dev.device_type || '—')}</span></div>`
      + `<div class="row"><span>IP</span><span>${escapeHtml(dev.device_ip || '—')}</span></div>`
      + `<div class="row"><span>Connection</span><span>${dev.connection_type ? escapeHtml(dev.connection_type.toUpperCase()) + ' ' + connIcon(dev.connection_type) : '—'}</span></div>`
      + `<div class="fp-popup-actions">`
      + (IS_ADMIN ? `<button class="tactical-btn tactical-btn-outline btn-sm" data-act="lock">${dev.map_locked ? 'Unlock' : 'Lock'}</button>` : '')
      + (IS_ADMIN ? `<button class="tactical-btn tactical-btn-outline btn-sm" data-act="remove">Remove</button>` : '')
      + `<a class="tactical-btn tactical-btn-primary btn-sm" href="${href}">Open Device</a>`
      + `</div>`;
    els.stageOuter.appendChild(p);

    // Position near the marker within the scrolling stage container.
    const mr = marker.getBoundingClientRect();
    const or = els.stageOuter.getBoundingClientRect();
    let left = mr.left - or.left + els.stageOuter.scrollLeft + 18;
    let top = mr.top - or.top + els.stageOuter.scrollTop - 10;
    left = Math.max(6, Math.min(left, els.stageOuter.scrollLeft + els.stageOuter.clientWidth - 230));
    p.style.left = left + 'px';
    p.style.top = top + 'px';

    p.querySelector('.fp-close').addEventListener('click', closePopup);
    const lockBtn = p.querySelector('[data-act="lock"]');
    if (lockBtn) lockBtn.addEventListener('click', function () { toggleLock(dev.device_id); });
    const remBtn = p.querySelector('[data-act="remove"]');
    if (remBtn) remBtn.addEventListener('click', function () { unplaceDevice(dev.device_id); });
    popupEl = p;
  }
  function closePopup() { if (popupEl) { popupEl.remove(); popupEl = null; } }

  // ---- zoom + pan -----------------------------------------------------------
  function applyZoom() {
    const img = document.getElementById('fpStageImg');
    if (!img || !currentPlan || !currentPlan.image_width) return;
    img.style.width = Math.round(currentPlan.image_width * zoom) + 'px';
  }
  function fitZoom() {
    const img = document.getElementById('fpStageImg');
    if (!img || !currentPlan || !currentPlan.image_width) return;
    const availW = els.stageOuter.clientWidth - 8;
    const availH = els.stageOuter.clientHeight - 8;
    const z = Math.min(availW / currentPlan.image_width, availH / currentPlan.image_height);
    zoom = Math.max(0.05, Math.min(z, 1));
    applyZoom();
  }
  els.zoom.addEventListener('click', function (e) {
    const act = e.target.getAttribute('data-zoom');
    if (!act) return;
    if (act === 'in') zoom = Math.min(zoom * 1.25, 8);
    else if (act === 'out') zoom = Math.max(zoom / 1.25, 0.05);
    else if (act === 'fit') return fitZoom();
    applyZoom();
  });
  els.stageOuter.addEventListener('wheel', function (e) {
    if (!e.ctrlKey) return;             // ctrl+wheel zooms; plain wheel scrolls
    e.preventDefault();
    zoom = e.deltaY < 0 ? Math.min(zoom * 1.1, 8) : Math.max(zoom / 1.1, 0.05);
    applyZoom();
  }, { passive: false });

  function enablePan() {
    // Drag empty canvas to pan (mouse). Touch uses native scroll of the container.
    let panning = null;
    els.stage.addEventListener('pointerdown', function (e) {
      if (e.target.closest('.fp-marker')) return;     // markers handle their own
      if (e.pointerType === 'touch') return;          // native scroll for touch
      panning = { x: e.clientX, y: e.clientY, sl: els.stageOuter.scrollLeft, st: els.stageOuter.scrollTop };
      els.stage.classList.add('panning');
      closePopup();
    });
    window.addEventListener('pointermove', function (e) {
      if (!panning) return;
      els.stageOuter.scrollLeft = panning.sl - (e.clientX - panning.x);
      els.stageOuter.scrollTop = panning.st - (e.clientY - panning.y);
    });
    window.addEventListener('pointerup', function () { panning = null; els.stage.classList.remove('panning'); });
  }

  // ---- persistence ----------------------------------------------------------
  async function savePlacements(placements, extra) {
    try {
      const r = await api(`/api/floor-plans/${currentPlan.id}/placements`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ placements: placements }, extra || {})),
      });
      if (r.skipped_locked && r.skipped_locked.length) toast(`${r.skipped_locked.length} locked device(s) not moved`, 'warning');
      closePopup();
      await selectPlan(currentPlan.id);
      await loadPlans();
    } catch (err) { toast('Save failed: ' + err.message, 'error'); }
  }
  function unplaceDevice(deviceId) { savePlacements([{ device_id: deviceId, map_x: null, map_y: null }], { force: true }); }
  function toggleLock(deviceId) {
    const dev = placedById(deviceId);
    if (!dev) return;
    savePlacements([{ device_id: deviceId, map_x: dev.map_x, map_y: dev.map_y, map_locked: !dev.map_locked }], { force: true });
  }

  // ---- live status ----------------------------------------------------------
  function startStatusPolling() { refreshStatus(); statusTimer = setInterval(refreshStatus, STATUS_POLL_MS); }
  function stopStatusPolling() { if (statusTimer) { clearInterval(statusTimer); statusTimer = null; } }
  async function refreshStatus() {
    if (!currentPlan || !(currentPlan.placed_devices || []).length) return;
    const ids = currentPlan.placed_devices.map(function (d) { return d.device_id; });
    try {
      const r = await api('/api/monitoring/status?mode=cached&device_ids=' + ids.join(','));
      (r && r.devices || []).forEach(function (d) {
        statusByDevice[d.device_id || d.id] = d.status || d.availability_status || 'Unknown';
      });
      ids.forEach(function (id) {
        const marker = els.stage.querySelector('.fp-marker[data-device-id="' + id + '"]');
        if (!marker) return;
        marker.classList.remove('st-online', 'st-offline', 'st-maintenance', 'st-unknown');
        marker.classList.add(statusClass(statusByDevice[id]));
      });
      applyFilters();
    } catch (err) { /* keep last colors */ }
  }

  // ---- search / filter ------------------------------------------------------
  function populateTypeFilter() {
    const types = Array.from(new Set(siteDevices.map(function (d) { return (d.device_type || '').trim(); }).filter(Boolean))).sort();
    const cur = els.typeFilter.value;
    els.typeFilter.innerHTML = '<option value="">All types</option>'
      + types.map(function (t) { return `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`; }).join('');
    if (types.indexOf(cur) >= 0) els.typeFilter.value = cur;
  }
  function applyFilters() {
    const q = (els.search.value || '').trim().toLowerCase();
    const type = els.typeFilter.value;
    const offlineOnly = els.offlineOnly.checked;
    // markers
    (currentPlan ? (currentPlan.placed_devices || []) : []).forEach(function (d) {
      const marker = els.stage.querySelector('.fp-marker[data-device-id="' + d.device_id + '"]');
      if (!marker) return;
      let show = true;
      if (q && (d.device_name || '').toLowerCase().indexOf(q) < 0 && (d.device_ip || '').toLowerCase().indexOf(q) < 0) show = false;
      if (type && (d.device_type || '') !== type) show = false;
      if (offlineOnly && statusClass(statusByDevice[d.device_id]) !== 'st-offline') show = false;
      marker.classList.toggle('dimmed', !show);
    });
    // unplaced chips (search + type)
    if (els.unplaced) {
      Array.prototype.forEach.call(els.unplaced.querySelectorAll('.fp-chip'), function (chip) {
        const id = parseInt(chip.dataset.deviceId, 10);
        const dev = siteDevices.find(function (x) { return x.device_id === id; }) || {};
        let show = true;
        if (q && (chip.dataset.name || '').indexOf(q) < 0) show = false;
        if (type && (dev.device_type || '') !== type) show = false;
        chip.style.display = show ? '' : 'none';
      });
    }
  }
  els.search.addEventListener('input', applyFilters);
  els.typeFilter.addEventListener('change', applyFilters);
  els.offlineOnly.addEventListener('change', applyFilters);

  // ---- plan actions (admin) -------------------------------------------------
  function renderPlanActions() {
    if (!IS_ADMIN || !els.planActions) return;
    els.planActions.style.display = 'flex';
    els.planActions.innerHTML = '';
    const replaceBtn = document.createElement('button');
    replaceBtn.className = 'tactical-btn tactical-btn-outline btn-sm';
    replaceBtn.innerHTML = '<i class="fas fa-sync me-1"></i>Replace image';
    replaceBtn.addEventListener('click', function () { openUpload(currentPlan); });
    const delBtn = document.createElement('button');
    delBtn.className = 'tactical-btn tactical-btn-outline btn-sm';
    delBtn.innerHTML = '<i class="fas fa-trash me-1"></i>Delete';
    delBtn.addEventListener('click', deletePlan);
    els.planActions.appendChild(replaceBtn);
    els.planActions.appendChild(delBtn);
  }
  async function deletePlan() {
    if (!confirm(`Delete plan "${currentPlan.name}"? Device placements will be cleared.`)) return;
    try {
      await api(`/api/floor-plans/${currentPlan.id}`, { method: 'DELETE' });
      toast('Plan deleted', 'success');
      currentPlan = null;
      await loadPlans();
      if (!plans.length) { renderStage(); if (els.unplaced) els.unplaced.innerHTML = ''; els.planActions.style.display = 'none'; }
    } catch (err) { toast('Delete failed: ' + err.message, 'error'); }
  }

  // ---- upload / replace -----------------------------------------------------
  const uploadModal = document.getElementById('fpUploadModal');
  function openUpload(replacePlan) {
    if (!uploadModal) return;
    document.getElementById('fpReplacePlanId').value = replacePlan ? replacePlan.id : '';
    document.getElementById('fpPlanName').value = replacePlan ? replacePlan.name : '';
    document.getElementById('fpFile').value = '';
    uploadModal.style.display = 'flex';
  }
  function closeUpload() { if (uploadModal) uploadModal.style.display = 'none'; }

  if (IS_ADMIN) {
    const uploadBtn = document.getElementById('fpUploadBtn');
    if (uploadBtn) uploadBtn.addEventListener('click', function () { openUpload(null); });
    const cancel = document.getElementById('fpUploadCancel');
    if (cancel) cancel.addEventListener('click', closeUpload);
    const form = document.getElementById('fpUploadForm');
    if (form) form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const submitBtn = document.getElementById('fpUploadSubmit');
      const replaceId = document.getElementById('fpReplacePlanId').value;
      const fd = new FormData();
      fd.append('name', document.getElementById('fpPlanName').value);
      const file = document.getElementById('fpFile').files[0];
      if (file) fd.append('file', file);
      submitBtn.disabled = true;
      try {
        if (replaceId) {
          await api(`/api/floor-plans/${replaceId}`, { method: 'PUT', body: fd });
          toast('Plan updated', 'success');
          await selectPlan(parseInt(replaceId, 10));
        } else {
          const r = await api(`/api/sites/${SITE_ID}/floor-plans`, { method: 'POST', body: fd });
          toast('Plan uploaded', 'success');
          currentPlan = null;
          await loadPlans();
          if (r && r.data) await selectPlan(r.data.id);
        }
        closeUpload();
      } catch (err) { toast('Upload failed: ' + err.message, 'error'); }
      finally { submitBtn.disabled = false; }
    });
  }

  // close popup when clicking outside
  document.addEventListener('pointerdown', function (e) {
    if (popupEl && !e.target.closest('.fp-popup') && !e.target.closest('.fp-marker')) closePopup();
  });
  window.addEventListener('resize', function () { if (currentPlan) applyZoom(); });

  // ---- boot -----------------------------------------------------------------
  loadPlans().catch(function (err) { toast('Failed to load plans: ' + err.message, 'error'); });
  window.addEventListener('beforeunload', stopStatusPolling);
})();
