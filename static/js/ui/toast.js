(function initUiToast(global) {
    try {
        const root = global || window;
        root.UI = root.UI || {};

        function escapeHtml(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function resolveContainer(container) {
            if (container && container.nodeType === 1) {
                return container;
            }
            if (typeof container === 'string') {
                const fromSelector = document.querySelector(container);
                if (fromSelector) return fromSelector;
            }

            let existing = document.getElementById('ui-toast-root');
            if (existing) {
                return existing;
            }

            existing = document.createElement('div');
            existing.id = 'ui-toast-root';
            existing.className = 'ui-toast-root';
            document.body.appendChild(existing);
            return existing;
        }

        function levelClass(level) {
            const normalized = String(level || 'info').toLowerCase();
            if (normalized === 'success') return 'ui-toast-success';
            if (normalized === 'warning') return 'ui-toast-warning';
            if (normalized === 'danger' || normalized === 'error') return 'ui-toast-danger';
            return 'ui-toast-info';
        }

        function levelIcon(level) {
            const normalized = String(level || 'info').toLowerCase();
            if (normalized === 'success') return '✓';
            if (normalized === 'warning') return '⚠';
            if (normalized === 'danger' || normalized === 'error') return '✕';
            return 'ℹ';
        }

        function closeToast(toast) {
            if (!toast || toast.dataset.uiToastClosing === '1') return;
            toast.dataset.uiToastClosing = '1';
            toast.classList.add('is-closing');
            window.setTimeout(function removeToast() {
                if (toast.parentNode) {
                    toast.remove();
                }
            }, 180);
        }

        const api = {
            show: function show(message, level, options) {
                try {
                    const opts = options || {};
                    const text = String(message || '').trim();
                    if (!text) return null;

                    const container = resolveContainer(opts.container);
                    const variant = String(level || 'info').toLowerCase();
                    const dedupeKey = String(opts.dedupeKey || (variant + '|' + text));
                    const existing = Array.from(container.querySelectorAll('.ui-toast')).find(function matchToast(node) {
                        return node.dataset.uiToastKey === dedupeKey;
                    });
                    if (existing) {
                        return existing;
                    }

                    const toast = document.createElement('div');
                    toast.className = 'ui-toast ' + levelClass(variant);
                    toast.dataset.uiToastKey = dedupeKey;
                    toast.innerHTML = [
                        '<div class="ui-toast-icon" aria-hidden="true">' + escapeHtml(levelIcon(variant)) + '</div>',
                        '<div class="ui-toast-body">',
                        '<p class="ui-toast-message">' + (opts.allowHtml ? text : escapeHtml(text).replace(/\n/g, '<br>')) + '</p>',
                        '<div class="ui-toast-actions"></div>',
                        '</div>',
                        '<button type="button" class="ui-toast-close" aria-label="Dismiss">&times;</button>'
                    ].join('');

                    const actionHost = toast.querySelector('.ui-toast-actions');
                    if (opts.actionLabel && typeof opts.onAction === 'function' && actionHost) {
                        const actionBtn = document.createElement('button');
                        actionBtn.type = 'button';
                        actionBtn.className = 'ui-toast-action';
                        actionBtn.textContent = String(opts.actionLabel);
                        actionBtn.addEventListener('click', function onActionClick() {
                            try {
                                opts.onAction();
                            } finally {
                                closeToast(toast);
                            }
                        });
                        actionHost.appendChild(actionBtn);
                    } else if (actionHost) {
                        actionHost.remove();
                    }

                    const closeBtn = toast.querySelector('.ui-toast-close');
                    if (closeBtn) {
                        closeBtn.addEventListener('click', function onCloseClick() {
                            closeToast(toast);
                        });
                    }

                    container.appendChild(toast);
                    while (container.children.length > 4) {
                        closeToast(container.firstElementChild);
                    }

                    if (!opts.persistent) {
                        const durationMs = Math.max(1400, Number(opts.durationMs || 4000));
                        window.setTimeout(function autoCloseToast() {
                            closeToast(toast);
                        }, durationMs);
                    }

                    return toast;
                } catch (error) {
                    console.error('[UI.Toast] show failed:', error);
                    return null;
                }
            }
        };

        root.UI.Toast = api;
    } catch (error) {
        console.error('[UI.Toast] initialization failed:', error);
        try {
            window.UI = window.UI || {};
            window.UI.Toast = null;
        } catch (_ignored) {
            // Ignore secondary failures.
        }
    }
}(typeof window !== 'undefined' ? window : this));
