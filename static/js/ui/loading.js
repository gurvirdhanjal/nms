(function initUiLoading(global) {
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

        function setButtonBusy(button, config) {
            try {
                const node = button;
                if (!node) return null;
                const opts = config || {};
                const busy = Boolean(opts.busy);

                if (!node.dataset.uiButtonIdleLabel) {
                    node.dataset.uiButtonIdleLabel = opts.labelIdle || node.innerHTML;
                }

                if (busy) {
                    if (opts.preserveWidth !== false && !node.dataset.uiButtonMinWidth) {
                        node.dataset.uiButtonMinWidth = String(node.offsetWidth || 0);
                        if (node.offsetWidth) {
                            node.style.minWidth = node.offsetWidth + 'px';
                        }
                    }
                    node.disabled = true;
                    node.classList.add('ui-btn-busy');
                    node.innerHTML = '<span class="ui-btn-busy-indicator">' +
                        escapeHtml(String(opts.labelBusy || 'Working...')) +
                        '</span>';
                    return node;
                }

                node.disabled = false;
                node.classList.remove('ui-btn-busy');
                node.innerHTML = opts.labelIdle || node.dataset.uiButtonIdleLabel || node.innerHTML;
                if (node.dataset.uiButtonMinWidth) {
                    node.style.minWidth = '';
                    delete node.dataset.uiButtonMinWidth;
                }
                return node;
            } catch (error) {
                console.error('[UI.Loading] setButtonBusy failed:', error);
                return null;
            }
        }

        function buildStatusMarkup(state, title, detail, actionLabel) {
            const variant = state === 'error' ? 'error' : state === 'empty' ? 'warning' : 'loading';
            return [
                '<div class="ui-status-card ' + variant + ' is-centered">',
                '<div class="ui-status-title">' + escapeHtml(title) + '</div>',
                detail ? '<div class="ui-status-detail">' + escapeHtml(detail) + '</div>' : '',
                actionLabel ? '<button type="button" class="ui-status-action">' + escapeHtml(actionLabel) + '</button>' : '',
                '</div>'
            ].join('');
        }

        function setTableState(tbody, config) {
            try {
                const node = tbody;
                if (!node) return null;
                const opts = config || {};
                const state = String(opts.state || 'loading');
                const title = opts.title || (state === 'empty' ? 'Nothing to show' : state === 'error' ? 'Unable to load data' : 'Loading data');
                const detail = opts.detail || '';
                const colspan = Math.max(1, Number(opts.colspan || 1));

                node.innerHTML = '<tr class="ui-status-row"><td colspan="' + colspan + '">' +
                    buildStatusMarkup(state, title, detail, opts.actionLabel) +
                    '</td></tr>';

                if (opts.preserveHeight && node.parentElement) {
                    node.parentElement.style.minHeight = Math.max(140, Number(opts.preserveHeight)) + 'px';
                }

                if (opts.actionLabel && typeof opts.actionHandler === 'function') {
                    const action = node.querySelector('.ui-status-action');
                    if (action) {
                        action.addEventListener('click', opts.actionHandler);
                    }
                }
                return node;
            } catch (error) {
                console.error('[UI.Loading] setTableState failed:', error);
                return null;
            }
        }

        function setRegionState(container, config) {
            try {
                const node = container;
                if (!node) return null;
                const opts = config || {};
                const state = String(opts.state || 'loading');
                const title = opts.title || (state === 'empty' ? 'Nothing to show' : state === 'error' ? 'Unable to load data' : 'Loading data');
                const detail = opts.detail || '';
                const compact = opts.compact ? ' compact' : '';
                const klass = state === 'error' ? ' error' : state === 'empty' ? ' warning' : '';

                node.innerHTML = [
                    '<div class="ui-region-state' + compact + klass + '">',
                    '<div class="ui-region-state-title">' + escapeHtml(title) + '</div>',
                    detail ? '<div class="ui-region-state-detail">' + escapeHtml(detail) + '</div>' : '',
                    '</div>'
                ].join('');

                if (opts.preserveHeight) {
                    node.style.minHeight = Math.max(84, Number(opts.preserveHeight)) + 'px';
                }
                return node;
            } catch (error) {
                console.error('[UI.Loading] setRegionState failed:', error);
                return null;
            }
        }

        root.UI.Loading = {
            setButtonBusy: setButtonBusy,
            setTableState: setTableState,
            setRegionState: setRegionState
        };
    } catch (error) {
        console.error('[UI.Loading] initialization failed:', error);
        try {
            window.UI = window.UI || {};
            window.UI.Loading = null;
        } catch (_ignored) {
            // Ignore secondary failures.
        }
    }
}(typeof window !== 'undefined' ? window : this));
