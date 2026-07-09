/**
 * Maintenance Window Management
 * Handles device maintenance schedule displaying, creating, and cancelling.
 */

let allWindows = [];
let allDevices = [];

let _filterTimer = null;
function debouncedFilter(fn) {
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(fn, 300);
}

document.addEventListener('DOMContentLoaded', () => {
    loadDevices();
    loadWindows();

    // Filters and Listeners
    document.getElementById('maintenanceSearch')?.addEventListener('input', () => debouncedFilter(filterWindows));
    document.getElementById('maintenanceDeviceFilter')?.addEventListener('change', filterWindows);
    document.getElementById('maintenanceStatusFilter')?.addEventListener('change', filterWindows);
    document.getElementById('maintenanceIncludeInactive')?.addEventListener('change', () => {
        loadWindows();
    });

    document.getElementById('btnRefreshWindows')?.addEventListener('click', () => {
        loadWindows();
    });

    document.getElementById('btnOpenSchedule')?.addEventListener('click', openScheduleModal);

    document.getElementById('btnSubmitSchedule')?.addEventListener('click', submitSchedule);

    document.getElementById('scheduleDeviceSearch')?.addEventListener('input', () => debouncedFilter(filterScheduleDevices));
});

function setTableMessage(tbody, colSpan, message, className = 'text-center py-4 text-muted') {
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="${colSpan}" class="${className}">${message}</td></tr>`;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    window.UI?.Toast?.show(String(message || ''), type);
}

function getTypeIcon(type) {
    const icons = {
        'Server':      '<i class="fas fa-server icon-info"></i>',
        'Switch':      '<i class="fas fa-network-wired icon-accent"></i>',
        'Router':      '<i class="fas fa-route icon-warning"></i>',
        'Camera':      '<i class="fas fa-video icon-danger"></i>',
        'Workstation': '<i class="fas fa-desktop icon-muted"></i>',
        'Printer':     '<i class="fas fa-print icon-dim"></i>',
    };
    return icons[type] || '<i class="fas fa-microchip icon-dim"></i>';
}

async function loadDevices() {
    try {
        const res = await fetch('/api/maintenance/devices');
        if (!res.ok) throw new Error('Failed to load devices');
        const data = await res.json();
        allDevices = data.devices || [];
        populateDeviceFilters();
    } catch (e) {
        console.error('Error loading devices:', e);
    }
}

function populateDeviceFilters() {
    const filterSelect = document.getElementById('maintenanceDeviceFilter');
    const scheduleSelect = document.getElementById('scheduleDevice');
    const typeFilter = document.getElementById('scheduleDeviceTypeFilter');
    if (!filterSelect || !scheduleSelect) return;

    // Preserve selection
    const currentFilterVal = filterSelect.value;

    filterSelect.innerHTML = '<option value="">All Devices</option>';
    scheduleSelect.innerHTML = '<option value="">Select device</option>';

    // Populate device type filter from unique types
    if (typeFilter) {
        const types = [...new Set(allDevices.map(d => (d.device_type || 'unknown').toLowerCase()))].sort();
        typeFilter.innerHTML = '<option value="">ALL TYPES</option>';
        types.forEach(t => {
            const o = document.createElement('option');
            o.value = t;
            o.textContent = t.replace(/_/g, ' ').toUpperCase();
            typeFilter.appendChild(o);
        });
    }

    allDevices.forEach(d => {
        const text = `${d.device_name} (${d.device_ip}) - ${d.device_type || 'Unknown'}`;

        const filterOpt = document.createElement('option');
        filterOpt.value = d.device_id;
        filterOpt.textContent = text;
        filterSelect.appendChild(filterOpt);

        const schedOpt = document.createElement('option');
        schedOpt.value = d.device_id;
        schedOpt.textContent = text;
        schedOpt.dataset.search = text.toLowerCase();
        schedOpt.dataset.type = (d.device_type || 'unknown').toLowerCase();
        scheduleSelect.appendChild(schedOpt);
    });

    filterSelect.value = currentFilterVal;
}

function filterScheduleDevices() {
    const search = (document.getElementById('scheduleDeviceSearch')?.value || '').toLowerCase();
    const typeVal = (document.getElementById('scheduleDeviceTypeFilter')?.value || '').toLowerCase();
    const select = document.getElementById('scheduleDevice');
    if (!select) return;

    let hasVisibleOption = false;
    Array.from(select.options).forEach((opt, index) => {
        if (index === 0) return; // Skip "Select device"

        const matchSearch = !search || (opt.dataset.search && opt.dataset.search.includes(search));
        const matchType = !typeVal || (opt.dataset.type && opt.dataset.type === typeVal);

        if (matchSearch && matchType) {
            opt.style.display = '';
            hasVisibleOption = true;
        } else {
            opt.style.display = 'none';
        }
    });
}

function calculateRemaining(endTimeStr) {
    if (!endTimeStr) return '-';
    let end = new Date(endTimeStr);

    // Check if the backend gave us a naive UTC string. If missing 'Z', append it.
    if (endTimeStr && !endTimeStr.endsWith('Z') && !endTimeStr.includes('+')) {
        end = new Date(endTimeStr + 'Z');
    }

    const now = new Date();
    const diff = end - now;
    if (diff <= 0) return 'Expired';

    const hours = Math.floor(diff / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);
    if (hours > 24) {
        return `${Math.floor(hours / 24)}d ${hours % 24}h`;
    }
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
}

function getWindowStatus(windowObj) {
    if (!windowObj.is_active) return { label: 'Cancelled/Inactive', class: 'bg-secondary' };

    let start = new Date(windowObj.start_time);
    let end = new Date(windowObj.end_time);

    // Patch UTC strings
    if (windowObj.start_time && !windowObj.start_time.endsWith('Z') && !windowObj.start_time.includes('+')) {
        start = new Date(windowObj.start_time + 'Z');
    }
    if (windowObj.end_time && !windowObj.end_time.endsWith('Z') && !windowObj.end_time.includes('+')) {
        end = new Date(windowObj.end_time + 'Z');
    }

    const now = new Date();

    if (end < now) {
        return { label: 'Expired', class: 'bg-secondary' };
    }
    if (start > now) {
        return { label: 'Scheduled', class: 'bg-info text-dark' };
    }
    return { label: 'Active Now', class: 'bg-warning text-dark' };
}

async function loadWindows() {
    const tbody = document.getElementById('maintenanceWindowTableBody');
    const updateEl = document.getElementById('maintenanceUpdatedAt');
    const includeInactive = document.getElementById('maintenanceIncludeInactive')?.checked ? 'true' : 'false';

    setTableMessage(tbody, 10, '<i class="fas fa-spinner fa-spin me-2"></i>Loading maintenance windows...');

    try {
        const res = await fetch(`/api/maintenance/windows?include_inactive=${includeInactive}`);
        if (!res.ok) throw new Error('Failed to fetch windows');
        const data = await res.json();

        allWindows = data.windows || [];
        if (updateEl) updateEl.textContent = `Updated: ${new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}`;

        filterWindows();
    } catch (e) {
        setTableMessage(tbody, 10, `<i class="fas fa-exclamation-triangle text-danger me-2"></i>${e.message}`, 'text-center py-4 text-danger');
    }
}

function filterWindows() {
    const tbody = document.getElementById('maintenanceWindowTableBody');
    if (!tbody) return;

    const searchTerm = (document.getElementById('maintenanceSearch')?.value || '').toLowerCase();
    const deviceFilterId = document.getElementById('maintenanceDeviceFilter')?.value || '';
    const statusFilter = document.getElementById('maintenanceStatusFilter')?.value || '';

    let filtered = allWindows;

    // Filter by Device
    if (deviceFilterId) {
        filtered = filtered.filter(w => String(w.device_id) === deviceFilterId);
    }

    // Filter by Status Dropdown
    if (statusFilter) {
        filtered = filtered.filter(w => {
            const status = getWindowStatus(w).label.toLowerCase();
            if (statusFilter === 'active') return status === 'active now';
            if (statusFilter === 'scheduled') return status === 'scheduled';
            return true;
        });
    }

    // Filter by Search Query
    if (searchTerm) {
        filtered = filtered.filter(w =>
            (w.device_name && w.device_name.toLowerCase().includes(searchTerm)) ||
            (w.device_ip && w.device_ip.toLowerCase().includes(searchTerm)) ||
            (w.reason && w.reason.toLowerCase().includes(searchTerm))
        );
    }

    renderWindows(filtered);
}

function renderWindows(windows) {
    const tbody = document.getElementById('maintenanceWindowTableBody');
    const countBadge = document.getElementById('maintenanceCount');

    if (countBadge) {
        const activeCount = allWindows.filter(w => getWindowStatus(w).label === 'Active Now').length;
        countBadge.textContent = activeCount;
    }

    if (!windows || windows.length === 0) {
        setTableMessage(tbody, 10, 'No maintenance windows found matching criteria.');
        return;
    }

    tbody.innerHTML = '';

    windows.forEach(w => {
        const status = getWindowStatus(w);
        const isActiveOrSched = status.label === 'Active Now' || status.label === 'Scheduled';
        const tR = document.createElement('tr');

        // Correct time formats
        let startDt = new Date(w.start_time);
        let endDt = new Date(w.end_time);
        if (w.start_time && !w.start_time.endsWith('Z') && !w.start_time.includes('+')) startDt = new Date(w.start_time + 'Z');
        if (w.end_time && !w.end_time.endsWith('Z') && !w.end_time.includes('+')) endDt = new Date(w.end_time + 'Z');

        const formatTime = (d) => `<div class="font-monospace">${d.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' })}</div><div class="small text-muted font-monospace">${d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}</div>`;

        let actions = '-';
        if (isActiveOrSched && w.is_active) {
            actions = `<button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="cancelWindow(${w.id})"><i class="fas fa-times me-1"></i>Cancel</button>`;
        }

        tR.innerHTML = `
            <td><strong>${escapeHtml(w.device_name || 'Unknown')}</strong></td>
            <td><code style="color:var(--ui-text-muted);">${escapeHtml(w.device_ip)}</code></td>
            <td>${getTypeIcon(w.device_type)} <span class="small">${escapeHtml(w.device_type)}</span></td>
            <td><span class="badge ${status.class} maintenance-status-badge">${status.label}</span></td>
            <td>${formatTime(startDt)}</td>
            <td>${formatTime(endDt)}</td>
            <td class="font-monospace small">${calculateRemaining(w.end_time)}</td>
            <td class="reason-col" title="${escapeHtml(w.reason || '')}">${escapeHtml(w.reason || '-')}</td>
            <td class="small">${escapeHtml(w.created_by || 'System')}</td>
            <td class="text-center">${actions}</td>
        `;
        tbody.appendChild(tR);
    });
}

function openScheduleModal() {
    // Set default times (now) and (now + 1 hour)
    const startInput = document.getElementById('scheduleStartTime');
    const endInput = document.getElementById('scheduleEndTime');
    const deviceInput = document.getElementById('scheduleDevice');
    const reasonInput = document.getElementById('scheduleReason');

    // clear form
    if (deviceInput) deviceInput.value = '';
    if (reasonInput) reasonInput.value = '';
    if (document.getElementById('scheduleDeviceSearch')) document.getElementById('scheduleDeviceSearch').value = '';
    filterScheduleDevices(); // reset visibility

    if (startInput && endInput) {
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset()); // Shift to local time for datetime-local input
        startInput.value = now.toISOString().slice(0, 16);

        now.setHours(now.getHours() + 1);
        endInput.value = now.toISOString().slice(0, 16);
    }

    const modal = new bootstrap.Modal(document.getElementById('scheduleMaintenanceModal'));
    modal.show();
}

async function submitSchedule() {
    const device_id = document.getElementById('scheduleDevice').value;
    const start_time = document.getElementById('scheduleStartTime').value;
    const end_time = document.getElementById('scheduleEndTime').value;
    const reason = document.getElementById('scheduleReason').value;

    if (!device_id) {
        showToast('Please select a device.', 'danger');
        return;
    }
    if (!start_time || !end_time) {
        showToast('Start and end times are required.', 'danger');
        return;
    }

    // We get local times from datetime-local input
    // Convert them to UTC before sending to server
    const tzOffset = (new Date()).getTimezoneOffset() * 60000;
    const utcS = new Date(new Date(start_time).getTime() - (new Date(start_time).getTimezoneOffset() - (new Date().getTimezoneOffset())) * 60000).toISOString();
    const utcE = new Date(new Date(end_time).getTime() - (new Date(end_time).getTimezoneOffset() - (new Date().getTimezoneOffset())) * 60000).toISOString();

    const btn = document.getElementById('btnSubmitSchedule');
    const origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Scheduling...';

    try {
        const payload = {
            device_id: parseInt(device_id),
            start_time: utcS,
            end_time: utcE,
            reason: reason
        };

        const res = await fetch('/api/maintenance/schedule', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to schedule maintenance');

        showToast('Maintenance scheduled successfully', 'success');

        // Hide modal
        bootstrap.Modal.getInstance(document.getElementById('scheduleMaintenanceModal')).hide();

        // Refresh
        loadWindows();

    } catch (e) {
        showToast(e.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHTML;
    }
}

async function cancelWindow(windowId) {
    if (!confirm('Are you sure you want to cancel this maintenance window?')) return;

    try {
        const res = await fetch(`/api/maintenance/windows/${windowId}/cancel`, {
            method: 'POST'
        });
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || 'Failed to cancel window');

        showToast('Maintenance window cancelled', 'success');
        loadWindows();
    } catch (e) {
        showToast(e.message, 'danger');
    }
}

// Attach to window so onclick="cancelWindow(1)" in HTML works
window.cancelWindow = cancelWindow;
