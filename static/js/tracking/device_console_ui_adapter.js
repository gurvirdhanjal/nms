(function initDeviceConsoleUiAdapter(global) {
    try {
        const root = global || window;
        const surfaceFlags = root.__UI_SURFACE_FLAGS__ || {};
        const sharedToastApi = surfaceFlags.sharedToast !== false && root.UI?.Toast?.show ? root.UI.Toast : null;
        const sharedLoadingApi = surfaceFlags.sharedLoading !== false && root.UI?.Loading ? root.UI.Loading : null;

        function escapeHtml(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function createAdapter(options) {
            const opts = options || {};
            const toastRoot = opts.toastRoot || null;

            function legacyToast(message, level) {
                if (!toastRoot) {
                    return null;
                }
                const variant = String(level || 'info').toLowerCase();
                const toast = document.createElement('div');
                toast.className = `device-toast toast-${variant}`;
                toast.innerHTML = `
                    <span>${escapeHtml(String(message || 'Update'))}</span>
                    <button type="button" aria-label="Dismiss">&times;</button>
                `;
                toast.querySelector('button')?.addEventListener('click', () => toast.remove());
                toastRoot.appendChild(toast);
                window.setTimeout(() => {
                    toast.classList.add('fade-out');
                    window.setTimeout(() => toast.remove(), 220);
                }, 2600);
                return toast;
            }

            return {
                toast(message, level, toastOptions) {
                    if (sharedToastApi) {
                        return sharedToastApi.show(String(message || ''), level || 'info', {
                            durationMs: toastOptions?.durationMs || 2600,
                            container: toastRoot,
                        });
                    }
                    return legacyToast(message, level);
                },
                setBusy(button, isBusy, busyOptions) {
                    const node = button;
                    if (!node) return;
                    const opts = busyOptions || {};
                    if (sharedLoadingApi) {
                        sharedLoadingApi.setButtonBusy(node, {
                            busy: Boolean(isBusy),
                            labelBusy: opts.labelBusy || node.dataset.uiIdleLabel || node.innerHTML,
                            labelIdle: opts.labelIdle || node.dataset.uiIdleLabel || node.innerHTML,
                        });
                        node.classList.toggle('is-busy', Boolean(isBusy));
                        return;
                    }
                    node.disabled = Boolean(isBusy);
                    node.classList.toggle('is-busy', Boolean(isBusy));
                },
                setRegionState(container, regionOptions) {
                    const node = container;
                    const opts = regionOptions || {};
                    if (!node) return;
                    if (sharedLoadingApi) {
                        sharedLoadingApi.setRegionState(node, opts);
                        return;
                    }
                    node.innerHTML = `<div class="files-placeholder">${escapeHtml(opts.detail || opts.title || 'Loading...')}</div>`;
                }
            };
        }

        root.DeviceConsoleUiAdapter = {
            create: createAdapter
        };
    } catch (error) {
        console.error('[DeviceConsoleUiAdapter] initialization failed:', error);
        window.DeviceConsoleUiAdapter = null;
    }
}(typeof window !== 'undefined' ? window : this));
