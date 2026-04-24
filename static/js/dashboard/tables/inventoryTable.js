/**
 * Device Inventory Table Component
 */
import { patchKeyedTableRows } from '../domPatch.js';

export function renderInventoryTable(devices) {
    const tableBody = document.getElementById('table-inventory-body');
    if (!tableBody) return;

    patchKeyedTableRows(tableBody, devices || [], {
        getKey: (device, index) => device.device_id || `${device.device_ip || 'unknown'}-${index}`,
        emptyColSpan: 8,
        emptyMessage: 'No devices in inventory',
        emptyClassName: 'text-center p-3 text-secondary',
        renderCells: (device) => {
            const tiers = ['Critical', 'Standard', 'Low'];
            const tierOptions = tiers.map(t =>
                `<option value="${t}" ${device.cos_tier === t ? 'selected' : ''}>${t}</option>`
            ).join('');

            const status = String(device.status_label || device.availability_status || device.server_health || 'No Data');
            const lastSeen = device.last_seen
                ? new Date(device.last_seen).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })
                : 'No sample';
            const primaryMetricValue = device.primary_metric_value != null
                ? `${device.primary_metric_value}${device.primary_metric_unit || ''}`
                : 'No data';
            const trend = Number(device.primary_metric_trend);
            const trendArrow = Number.isFinite(trend)
                ? trend > 0.2
                    ? '&uarr;'
                    : trend < -0.2
                        ? '&darr;'
                        : '&rarr;'
                : '&rarr;';
            const trendClass = Number.isFinite(trend)
                ? trend > 0.2
                    ? 'text-danger'
                    : trend < -0.2
                        ? 'text-success'
                        : 'text-secondary'
                : 'text-secondary';
            const alertLabel = Number(device.active_alert_count || 0) > 0
                ? `${device.active_alert_count} active alerts`
                : 'No active alerts';

            return `
            <td>
                <div class="form-check d-flex justify-content-center">
                    <input class="form-check-input inventory-checkbox" type="checkbox" value="${device.device_id}">
                </div>
            </td>
            <td class="device-cell">
                <div class="inventory-device-name">${device.device_name}</div>
                <div class="inventory-device-meta">${device.device_type || 'Unknown'} - ${device.device_ip || 'No IP'}</div>
                <span class="inventory-secondary">${alertLabel}</span>
            </td>
            <td>
                <span class="inventory-state">
                    <span class="inventory-state-dot"></span>${status}
                </span>
            </td>
            <td><span class="inventory-metric-value">${lastSeen}</span></td>
            <td>
                <span class="inventory-metric-value">${device.primary_metric_label || 'Metric'}: ${primaryMetricValue}</span>
            </td>
            <td>
                <span class="inventory-trend ${trendClass}">${trendArrow} ${Number.isFinite(trend) ? Math.abs(trend).toFixed(1) : '0.0'} ${device.primary_metric_unit || ''}</span>
                <span class="inventory-secondary">vs previous sample</span>
            </td>
            <td>
                <select class="tactical-select tier-select" data-id="${device.device_id}">
                    ${tierOptions}
                </select>
            </td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary btn-save-device" data-id="${device.device_id}" title="Save Selection">
                        <i class="fas fa-save"></i>
                    </button>
                    <button class="btn btn-outline-secondary btn-edit-device" data-id="${device.device_id}" title="Edit Device">
                        <i class="fas fa-edit"></i>
                    </button>
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
            const status = String(device.status_label || device.availability_status || device.server_health || 'No Data').toLowerCase();
            row.dataset.status = status.includes('critical') || status.includes('offline')
                ? 'critical'
                : status.includes('healthy') || status.includes('online')
                    ? 'healthy'
                    : status.includes('maintenance')
                        ? 'maintenance'
                        : 'nodata';
        }
    });
}

async function handleSaveDevice(deviceId) {
    const row = document.querySelector(`tr[data-id="${deviceId}"]`);
    if (!row) return;

    const tierSelect = row.querySelector('.tier-select');
    const tier = tierSelect ? tierSelect.value : '';
    const btn = row.querySelector('.btn-save-device');

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

    try {
        const response = await fetch(`/api/devices/${deviceId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cos_tier: tier })
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
        console.error('Save Error:', err);
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

    tableContainer.addEventListener('change', (e) => {
        if (e.target && e.target.id === 'inventory-select-all') {
            const isChecked = e.target.checked;
            const checkboxes = document.querySelectorAll('.inventory-checkbox');
            checkboxes.forEach(cb => {
                cb.checked = isChecked;
            });
        }

        if (e.target && e.target.classList.contains('inventory-checkbox')) {
            updateSelectAllState();
        }
    });

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
                        location.reload();
                    }
                });
            return;
        }

        const row = e.target.closest('tr.inventory-row');
        if (
            row &&
            !e.target.closest('a') &&
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

    if (!selectAll) return;

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
