/**
 * Device Inventory Table Component
 */
import { openServerModal } from '../modals/serverDetailModal.js';

export function renderInventoryTable(devices) {
    const tableBody = document.getElementById('table-inventory-body');
    if (!tableBody) return;

    if (!devices || devices.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="6" class="text-center p-3 text-secondary">No devices in inventory</td></tr>';
        return;
    }

    tableBody.innerHTML = devices.map(device => {
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
                'Unknown': 'text-muted'
            };
            const icons = {
                'Healthy': 'fa-check-circle',
                'Warning': 'fa-exclamation-triangle',
                'Critical': 'fa-times-circle',
                'Offline': 'fa-plug',
                'Unknown': 'fa-question-circle'
            };
            const colorClass = colors[serverHealth] || 'text-muted';
            const iconClass = icons[serverHealth] || 'fa-question-circle';

            healthBadge = `<div class="mt-1 small ${colorClass}"><i class="fas ${iconClass}"></i> ${serverHealth}</div>`;
        }

        return `
            <tr data-id="${device.device_id}" data-ip="${device.device_ip}" class="${isServer ? 'server-row' : ''}" style="${isServer ? 'cursor: pointer;' : ''}">
                <td>
                    <div class="form-check d-flex justify-content-center">
                        <input class="form-check-input inventory-checkbox" type="checkbox" value="${device.device_id}">
                    </div>
                </td>
                <td class="device-cell">
                    <div class="fw-bold">${device.device_name}</div>
                    <div class="small text-secondary">${device.device_type || 'Unknown'}</div>
                    ${healthBadge}
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
                    <button class="btn btn-sm btn-outline-primary btn-save-device" data-id="${device.device_id}">
                        <i class="fas fa-save"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
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

        // Handle row click for servers (drill-down)
        const row = e.target.closest('tr.server-row');
        if (row && !e.target.closest('td:first-child') && !e.target.closest('td:last-child') && !e.target.closest('select')) {
            openServerModal(row.dataset.id);
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
