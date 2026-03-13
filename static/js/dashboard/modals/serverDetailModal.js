import { createServerMetricsView } from '../servers/serverMetricsView.js';

let modalInstance = null;
let modalElement = null;
let currentDeviceId = null;
let refreshTimer = null;
let rangeButtons = null;
let view = null;

function getActiveRange() {
    const active = rangeButtons?.find((button) => button.classList.contains('active'));
    return active?.dataset.range || '24h';
}

function setActiveRange(range) {
    if (!rangeButtons || rangeButtons.length === 0) return;
    rangeButtons.forEach((button) => {
        button.classList.toggle('active', button.dataset.range === range);
        button.classList.toggle('tactical-btn-primary', button.dataset.range === range);
        button.classList.toggle('tactical-btn-ghost', button.dataset.range !== range);
    });
}

async function loadCurrentMetrics() {
    if (!view || !currentDeviceId) return;
    try {
        await view.load(currentDeviceId, getActiveRange(), { preferCache: true });
        view.prefetch?.(currentDeviceId, getActiveRange());
        
        // Update modal header with device info after metrics load
        const deviceNameEl = modalElement?.querySelector('#server-modal-device-name');
        const deviceIpEl = modalElement?.querySelector('#server-modal-device-ip');
        const deviceStatusEl = modalElement?.querySelector('#server-modal-device-status');
        
        if (deviceNameEl) {
            const titleEl = modalElement?.querySelector('#server-modal-title');
            if (titleEl && titleEl.textContent && titleEl.textContent !== '-') {
                deviceNameEl.textContent = titleEl.textContent;
            }
        }
        
        if (deviceIpEl) {
            const ipEl = modalElement?.querySelector('#server-modal-ip');
            if (ipEl && ipEl.textContent && ipEl.textContent !== '-') {
                deviceIpEl.textContent = ipEl.textContent;
            }
        }
        
        if (deviceStatusEl) {
            const statusEl = modalElement?.querySelector('#server-modal-status');
            if (statusEl && statusEl.textContent && statusEl.textContent !== '-') {
                deviceStatusEl.textContent = statusEl.textContent;
                deviceStatusEl.className = statusEl.className;
            }
        }
    } catch (error) {
        if (error?.name === 'AbortError') {
            return;
        }
        const statusEl = modalElement?.querySelector('#server-modal-status');
        if (statusEl) {
            statusEl.textContent = error.message || 'Error loading data';
            statusEl.className = 'fw-bold text-danger';
        }
    }
}

export function initServerModal() {
    modalElement = document.getElementById('serverDetailsModal');
    if (!modalElement || !window.bootstrap) return;

    if (!modalInstance) {
        modalInstance = new window.bootstrap.Modal(modalElement);
    }
    if (!view) {
        view = createServerMetricsView({ root: modalElement, prefix: 'server-modal' });
    }

    rangeButtons = Array.from(modalElement.querySelectorAll('.server-range-toggle [data-range]'));
    rangeButtons.forEach((button) => {
        if (button.dataset.bound === 'true') return;
        button.dataset.bound = 'true';
        button.addEventListener('click', () => {
            setActiveRange(button.dataset.range || '24h');
            loadCurrentMetrics();
        });
    });

    const snapshotButton = modalElement.querySelector('#server-modal-refresh-snapshot');
    if (snapshotButton && snapshotButton.dataset.bound !== 'true') {
        snapshotButton.dataset.bound = 'true';
        snapshotButton.addEventListener('click', async () => {
            if (!currentDeviceId) return;
            await view.fetchConnectionSnapshot(currentDeviceId, { showLoadingState: true });
        });
    }

    if (modalElement.dataset.bound !== 'true') {
        modalElement.dataset.bound = 'true';
        modalElement.addEventListener('hidden.bs.modal', () => {
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = null;
            }
            currentDeviceId = null;
            view?.destroy();
        });
    }
}

export function openServerModal(deviceId) {
    if (!modalInstance) initServerModal();
    if (!modalInstance || !view) return;

    currentDeviceId = deviceId;
    
    // Update the "Open Full Page" link to the dedicated server monitoring page
    const openPageLink = modalElement?.querySelector('#server-modal-open-page');
    if (openPageLink) {
        openPageLink.href = `/devices/${deviceId}/server-monitoring`;
    }
    
    modalInstance.show();
    loadCurrentMetrics();
    view.prefetch?.(currentDeviceId, getActiveRange());

    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
        loadCurrentMetrics();
    }, 30000);
}
