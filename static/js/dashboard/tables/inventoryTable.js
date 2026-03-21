/**
 * Device Inventory Table Component
 */
import { patchKeyedTableRows } from '../domPatch.js';

export function renderInventoryTable(devices) {
    const tableBody = document.getElementById('table-inventory-body');
    if (!tableBody) return;

    patchKeyedTableRows(tableBody, devices || [], {
        getKey: (device, index) => device.device_id || `${device.device_ip || 'unknown'}-${index}`,
        emptyColSpan: 6,
        emptyMessage: 'No devices in inventory',
        emptyClassName: 'text-center p-3 text-secondary',
        renderCells: (device) => {
            const brands = ['Cisco', 'Juniper', 'Aruba', 'Ubiquiti', 'HP', 'MikroTik', 'Generic'];
            const tiers = ['Critical', 'Standard', 'Low'];

            const brandOptions = brands.map(b =>
                `<option value="${b}" ${device.switch_brand === b ? 'selected' : ''}>${b}</option>`
            ).join('');

            const tierOptions = tiers.map(t =>
                `<option value="${t}" ${device.cos_tier === t ? 'selected' : ''}>${t}</option>`
            ).join('');

            const serverHealth = device.server_health || 'Unknown';
            const deviceType = (device.device_type || '').toLowerCase();
            const isServer = deviceType === 'server';
            let healthBadge = '';

            if (isServer || serverHealth !== 'Unknown') {
                const colors = {
                    'Healthy': 'text-success',
                    'Warning': 'text-warning',
                    'Critical': 'text-danger',
                    'Offline': 'text-secondary',
                    'Maintenance': 'text-warning', // Yellow for maintenance
                    'Unknown': 'text-muted'
                };
                const icons = {
                    'Healthy': 'fa-check-circle',
                    'Warning': 'fa-exclamation-triangle',
                    'Critical': 'fa-times-circle',
                    'Offline': 'fa-plug',
                    'Maintenance': 'fa-wrench',
                    'Unknown': 'fa-question-circle'
                };
                const colorClass = colors[serverHealth] || 'text-muted';
                const iconClass = icons[serverHealth] || 'fa-question-circle';

                healthBadge = `<div class="mt-1 small ${colorClass}"><i class="fas ${iconClass}"></i> ${serverHealth}</div>`;
            }

            return `
            <td>
                <div class="form-check d-flex justify-content-center">
                    <input class="form-check-input inventory-checkbox" type="checkbox" value="${device.device_id}">
                </div>
            </td>
            <td class="device-cell">
                <div class="fw-bold">${device.device_name}</div>
                <div class="small text-secondary">${device.device_type || 'Unknown'}</div>
                ${(device.maintenance_mode || device.status === 'Maintenance')
                    ? '<div class="mt-1 small text-warning"><i class="fas fa-wrench"></i> Maintenance</div>'
                    : healthBadge}
            </td>
            <td>
                ${(device.device_type === 'Switch') ? `
                    <select class="tactical-select brand-select" data-id="${device.device_id}">
                        <option value="">Unknown</option>
                        ${brandOptions}
                    </select>` : '<span class="text-secondary opacity-50">-</span>'}
            </td>
            <td>
                <select class="tactical-select tier-select" data-id="${device.device_id}">
                    ${tierOptions}
                </select>
            </td>
            <td class="font-monospace">${device.device_ip}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary btn-save-device" data-id="${device.device_id}" title="Save Selection">
                        <i class="fas fa-save"></i>
                    </button>
                    <!-- Edit Button -->
                    <button class="btn btn-outline-secondary btn-edit-device" data-id="${device.device_id}" title="Edit Device">
                        <i class="fas fa-edit"></i>
                    </button>
                    <!-- Toggle Monitoring Button -->
                    <button class="btn ${device.is_monitored ? 'btn-outline-success' : 'btn-outline-secondary'} btn-toggle-monitor" data-id="${device.device_id}" title="${device.is_monitored ? 'Pause Monitoring' : 'Resume Monitoring'}">
                        <i class="fas ${device.is_monitored ? 'fa-chart-line' : 'fa-pause'}"></i>
                    </button>
                </div>
            </td>
        `;
        },
        applyRow: (row, device) => {
            row.className = 'inventory-row';
            row.style.cursor = 'pointer';
            row.dataset.id = device.device_id || '';
            row.dataset.ip = device.device_ip || '';
        }
    });
}

async function handleSaveDevice(deviceId) {
    const row = document.querySelector(`tr[data-id="${deviceId}"]`);
    if (!row) return;

    const brandSelect = row.querySelector('.brand-select');
    const tierSelect = row.querySelector('.tier-select');
    const brand = brandSelect ? brandSelect.value : '';
    const tier = tierSelect ? tierSelect.value : '';
    const btn = row.querySelector('.btn-save-device');

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

    try {
        const response = await fetch(`/api/devices/${deviceId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                switch_brand: brand,
                cos_tier: tier
            })
        });

        if (response.ok) {
            btn.classList.replace('btn-outline-primary', 'btn-success');
            btn.innerHTML = '<i class="fas fa-check"></i>';
            setTimeout(() => {
                btn.classList.replace('btn-success', 'btn-outline-primary');
                btn.innerHTML = '<i class="fas fa-save"></i>';
                btn.disabled = false;
            }, 2000);
        } else {
            throw new Error('Save failed');
        }
    } catch (err) {
        console.error("Save Error:", err);
        btn.classList.replace('btn-outline-primary', 'btn-danger');
        btn.innerHTML = '<i class="fas fa-times"></i>';
        setTimeout(() => {
            btn.classList.replace('btn-danger', 'btn-outline-primary');
            btn.innerHTML = '<i class="fas fa-save"></i>';
            btn.disabled = false;
        }, 2000);
    }
}

export function initInventoryInteractions() {
    const tableContainer = document.querySelector('#tab-inventory-list .table-responsive');
    if (!tableContainer) return;

    // Delegate change events for checkboxes
    tableContainer.addEventListener('change', (e) => {
        // Handle "Select All" Header Checkbox
        if (e.target && e.target.id === 'inventory-select-all') {
            const isChecked = e.target.checked;
            const checkboxes = document.querySelectorAll('.inventory-checkbox');
            checkboxes.forEach(cb => cb.checked = isChecked);
        }

        // Handle Individual Row Checkboxes
        if (e.target && e.target.classList.contains('inventory-checkbox')) {
            updateSelectAllState();
        }
    });

    // Delegate clicks
    tableContainer.addEventListener('click', (e) => {
        const saveBtn = e.target.closest('.btn-save-device');
        if (saveBtn) {
            handleSaveDevice(saveBtn.dataset.id);
            return;
        }

        const editBtn = e.target.closest('.btn-edit-device');
        if (editBtn) {
            window.location.href = `/devices?edit_id=${editBtn.dataset.id}`;
            return;
        }

        const toggleBtn = e.target.closest('.btn-toggle-monitor');
        if (toggleBtn) {
            const deviceId = toggleBtn.dataset.id;
            fetch(`/api/devices/${deviceId}/toggle_monitoring`, { method: 'PATCH' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // Just reload or update icon
                        location.reload();
                    }
                });
            return;
        }

        // Navigate to full device detail page from expanded panel rows
        const row = e.target.closest('tr.inventory-row');
        if (row &&
            !e.target.closest('a') && // Do not intercept clicks on anchor tags to let them behave naturally
            !e.target.closest('td:first-child') &&
            !e.target.closest('td:last-child') &&
            !e.target.closest('select') &&
            !e.target.closest('input') &&
            row.dataset.id
        ) {
            window.location.href = `/devices/${row.dataset.id}/details`;
        }
    });
}

function updateSelectAllState() {
    const selectAll = document.getElementById('inventory-select-all');
    const allCheckboxes = document.querySelectorAll('.inventory-checkbox');
    const checkedCheckboxes = document.querySelectorAll('.inventory-checkbox:checked');

    if (selectAll) {
        if (allCheckboxes.length > 0 && checkedCheckboxes.length === allCheckboxes.length) {
            selectAll.indeterminate = false;
            selectAll.checked = true;
        } else if (checkedCheckboxes.length > 0) {
            selectAll.indeterminate = true;
            selectAll.checked = false;
        } else {
            selectAll.indeterminate = false;
            selectAll.checked = false;
        }
    }
}
