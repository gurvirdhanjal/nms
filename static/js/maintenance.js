/**
 * Maintenance Window Management
 * Handles device maintenance mode toggling and display.
 */

let allDevices = [];

function setTableMessageRow(tbody, colSpan, message, className, rowStyle = '') {
    if (!tbody) return;
    const row = document.createElement('tr');
    if (rowStyle) row.style.cssText = rowStyle;
    const cell = document.createElement('td');
    cell.colSpan = colSpan;
    cell.className = className;
    cell.innerHTML = message;
    row.appendChild(cell);
    tbody.textContent = '';
    tbody.appendChild(row);
}

function patchDeviceRows(tbody, devices) {
    Array.from(tbody.querySelectorAll('tr:not([data-device-id])')).forEach((row) => row.remove());

    const existing = new Map();
    Array.from(tbody.querySelectorAll('tr[data-device-id]')).forEach((row) => {
        existing.set(row.dataset.deviceId, row);
    });

    devices.forEach((device, index) => {
        const key = String(device.device_id || `maintenance-${index}`);
        let row = existing.get(key);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.deviceId = key;
        } else {
            existing.delete(key);
        }

        const statusBadge = device.maintenance_mode
            ? '<span class="badge bg-warning"><i class="fas fa-wrench"></i> Maintenance</span>'
            : device.is_active
                ? '<span class="badge bg-success"><i class="fas fa-circle"></i> Online</span>'
                : '<span class="badge bg-danger"><i class="fas fa-circle"></i> Offline</span>';

        const strikeDisplay = device.health_alert_strikes > 0
            ? `<span class="badge bg-danger">${device.health_alert_strikes}/3</span>`
            : '<span style="color:#6a6a80;">0</span>';

        const toggleChecked = device.maintenance_mode ? 'checked' : '';
        const toggleId = `toggle_${device.device_id}`;
        const html = `<td><strong>${escapeHtml(device.device_name)}</strong></td>
            <td><code>${escapeHtml(device.device_ip)}</code></td>
            <td>${getTypeIcon(device.device_type)} ${escapeHtml(device.device_type || 'Unknown')}</td>
            <td>${statusBadge}</td>
            <td class="text-center">${strikeDisplay}</td>
            <td class="text-center">
                <div class="form-check form-switch d-flex justify-content-center">
                    <input class="form-check-input" type="checkbox" role="switch"
                        id="${toggleId}" ${toggleChecked}
                        onchange="toggleMaintenance(${device.device_id}, this)"
                        style="cursor:pointer; width:3em; height:1.5em;">
                </div>
            </td>`;

        if (row.innerHTML !== html) {
            row.innerHTML = html;
        }

        tbody.appendChild(row);
    });

    existing.forEach((staleRow) => staleRow.remove());
}

document.addEventListener('DOMContentLoaded', () => {
    loadDevices();

    // Live search
    document.getElementById('searchInput').addEventListener('input', filterDevices);
    document.getElementById('typeFilter').addEventListener('change', filterDevices);
    document.getElementById('statusFilter').addEventListener('change', filterDevices);
});

async function loadDevices() {
    const tbody = document.getElementById('deviceTableBody');
    setTableMessageRow(
        tbody,
        6,
        '<i class="fas fa-spinner fa-spin"></i> Loading devices...',
        'text-center py-4',
        'color:#6a6a80;'
    );

    try {
        const res = await fetch('/api/maintenance/devices');
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || 'Failed to load');

        allDevices = data.devices || [];
        renderDevices(allDevices);
    } catch (err) {
        setTableMessageRow(
            tbody,
            6,
            `<i class="fas fa-exclamation-triangle"></i> ${err.message}`,
            'text-center py-4',
            'color:#ff3b5c;'
        );
    }
}

function renderDevices(devices) {
    const tbody = document.getElementById('deviceTableBody');

    // Update counter
    const mCount = devices.filter(d => d.maintenance_mode).length;
    document.getElementById('mCountVal').textContent = mCount;

    if (devices.length === 0) {
        setTableMessageRow(tbody, 6, 'No devices found.', 'text-center py-4', 'color:#6a6a80;');
        return;
    }

    patchDeviceRows(tbody, devices);
}

function filterDevices() {
    const search = document.getElementById('searchInput').value.toLowerCase();
    const typeVal = document.getElementById('typeFilter').value;
    const statusVal = document.getElementById('statusFilter').value;

    let filtered = allDevices;

    if (search) {
        filtered = filtered.filter(d =>
            d.device_name.toLowerCase().includes(search) ||
            d.device_ip.toLowerCase().includes(search)
        );
    }

    if (typeVal) {
        filtered = filtered.filter(d => d.device_type === typeVal);
    }

    if (statusVal === 'maintenance') {
        filtered = filtered.filter(d => d.maintenance_mode);
    } else if (statusVal === 'active') {
        filtered = filtered.filter(d => !d.maintenance_mode);
    }

    renderDevices(filtered);
}

async function toggleMaintenance(deviceId, checkbox) {
    checkbox.disabled = true;

    try {
        const res = await fetch('/api/maintenance/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId })
        });

        const data = await res.json();

        if (!res.ok) {
            checkbox.checked = !checkbox.checked; // revert
            throw new Error(data.error || 'Toggle failed');
        }

        // Update local state
        const device = allDevices.find(d => d.device_id === deviceId);
        if (device) {
            device.maintenance_mode = data.maintenance_mode;
            if (!data.maintenance_mode) device.health_alert_strikes = 0;
        }

        // Re-render to update badges and counter
        filterDevices();

        showToast(data.message, 'success');
    } catch (err) {
        showToast(err.message, 'danger');
    } finally {
        checkbox.disabled = false;
    }
}

function getTypeIcon(type) {
    const icons = {
        'Server': '<i class="fas fa-server" style="color:#00aaff;"></i>',
        'Switch': '<i class="fas fa-network-wired" style="color:#00d4aa;"></i>',
        'Router': '<i class="fas fa-route" style="color:#ffaa00;"></i>',
        'Camera': '<i class="fas fa-video" style="color:#ff3b5c;"></i>',
        'Workstation': '<i class="fas fa-desktop" style="color:#b8b8c8;"></i>',
        'Printer': '<i class="fas fa-print" style="color:#6a6a80;"></i>',
    };
    return icons[type] || '<i class="fas fa-microchip" style="color:#6a6a80;"></i>';
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    // Simple toast notification
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = 'position:fixed;top:80px;right:20px;z-index:9999;';
        document.body.appendChild(container);
    }

    const colors = {
        success: { bg: 'rgba(0,255,136,0.15)', border: '#00ff88', text: '#00ff88' },
        danger: { bg: 'rgba(255,59,92,0.15)', border: '#ff3b5c', text: '#ff3b5c' },
        info: { bg: 'rgba(0,170,255,0.15)', border: '#00aaff', text: '#00aaff' },
    };
    const c = colors[type] || colors.info;

    const toast = document.createElement('div');
    toast.style.cssText = `background:${c.bg};border:1px solid ${c.border};color:${c.text};
        padding:12px 20px;border-radius:8px;margin-bottom:10px;font-weight:600;
        font-family:'Rajdhani',sans-serif;letter-spacing:0.5px;
        box-shadow:0 4px 20px rgba(0,0,0,0.5);animation:slideIn 0.3s ease;`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
