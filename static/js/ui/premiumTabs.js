(function initPremiumTabs(global) {
    'use strict';

    try {
        const root = global || window;
        root.UI = root.UI || {};

        const registry = new WeakMap();
        let instanceCounter = 0;

        function toArray(items) {
            return Array.from(items || []);
        }

        function resolveElement(target, scope) {
            if (!target) return null;
            if (target.nodeType === 1) return target;
            if (typeof target === 'string') {
                return (scope || document).querySelector(target);
            }
            return null;
        }

        function normalizeKey(value) {
            return String(value || '').trim().toLowerCase();
        }

        function sanitizeKey(value) {
            return String(value || 'item')
                .trim()
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '') || 'item';
        }

        function prefersReducedMotion() {
            try {
                return Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
            } catch (_error) {
                return false;
            }
        }

        function getTabs(instance) {
            return toArray(instance.tablist.querySelectorAll(instance.options.tabSelector));
        }

        function getPanels(instance) {
            return toArray(instance.root.querySelectorAll(instance.options.panelSelector));
        }

        function getKeyFromTrigger(instance, trigger, fallbackIndex) {
            if (!trigger) return '';
            if (typeof instance.options.getKey === 'function') {
                return String(instance.options.getKey(trigger, fallbackIndex) || '');
            }
            return String(
                trigger.dataset.tab
                || trigger.dataset.target
                || trigger.getAttribute('aria-controls')
                || ''
            );
        }

        function getKeyFromPanel(instance, panel, fallbackIndex) {
            if (!panel) return '';
            if (typeof instance.options.getPanelKey === 'function') {
                return String(instance.options.getPanelKey(panel, fallbackIndex) || '');
            }
            return String(panel.dataset.panel || panel.id || '');
        }

        function findPanelByKey(instance, key) {
            const normalized = normalizeKey(key);
            return getPanels(instance).find((panel, index) => normalizeKey(getKeyFromPanel(instance, panel, index)) === normalized) || null;
        }

        function findTriggerByKey(instance, key) {
            const normalized = normalizeKey(key);
            return getTabs(instance).find((trigger, index) => normalizeKey(getKeyFromTrigger(instance, trigger, index)) === normalized) || null;
        }

        function getActiveKey(instance) {
            const activeTrigger = getTabs(instance).find((trigger) => trigger.classList.contains(instance.options.activeClass) || trigger.getAttribute('aria-selected') === 'true');
            if (!activeTrigger) return '';
            return getKeyFromTrigger(instance, activeTrigger);
        }

        function ensureIndicator(instance) {
            let indicator = instance.tablist.querySelector('.ui-premium-tab-indicator');
            if (indicator) return indicator;

            indicator = document.createElement('span');
            indicator.className = 'ui-premium-tab-indicator';
            indicator.setAttribute('aria-hidden', 'true');
            indicator.hidden = true;
            instance.tablist.appendChild(indicator);
            return indicator;
        }

        function updateIndicator(instance) {
            const indicator = ensureIndicator(instance);
            const activeTrigger = findTriggerByKey(instance, instance.activeKey);

            if (!activeTrigger) {
                indicator.hidden = true;
                return;
            }

            indicator.hidden = false;
            indicator.style.width = `${activeTrigger.offsetWidth}px`;
            indicator.style.transform = `translateX(${activeTrigger.offsetLeft}px)`;
        }

        function syncTabAttributes(instance, activeKey) {
            getTabs(instance).forEach((trigger, index) => {
                const isActive = normalizeKey(getKeyFromTrigger(instance, trigger, index)) === normalizeKey(activeKey);
                trigger.classList.toggle(instance.options.activeClass, isActive);
                trigger.setAttribute('aria-selected', isActive ? 'true' : 'false');
                trigger.setAttribute('tabindex', isActive ? '0' : '-1');
            });
        }

        function ensureAccessibleLinking(instance) {
            const tabs = getTabs(instance);
            const panels = getPanels(instance);

            tabs.forEach((trigger, index) => {
                const key = getKeyFromTrigger(instance, trigger, index) || `tab-${index}`;
                if (!trigger.id) {
                    trigger.id = `${instance.uid}-tab-${sanitizeKey(key)}`;
                }
                trigger.setAttribute('role', 'tab');
                trigger.setAttribute('type', trigger.tagName === 'BUTTON' ? 'button' : trigger.getAttribute('type') || 'button');
            });

            panels.forEach((panel, index) => {
                const key = getKeyFromPanel(instance, panel, index) || `panel-${index}`;
                if (!panel.id) {
                    panel.id = `${instance.uid}-panel-${sanitizeKey(key)}`;
                }
                panel.setAttribute('role', 'tabpanel');
                panel.setAttribute('tabindex', panel.getAttribute('tabindex') || '0');
            });

            tabs.forEach((trigger, index) => {
                const key = getKeyFromTrigger(instance, trigger, index);
                const panel = findPanelByKey(instance, key);
                if (!panel) return;

                trigger.setAttribute('aria-controls', panel.id);
                panel.setAttribute('aria-labelledby', trigger.id);
            });
        }

        function cleanupPanelState(panel, activeClass) {
            if (!panel) return;
            panel.hidden = true;
            panel.setAttribute('aria-hidden', 'true');
            panel.classList.remove(activeClass, 'is-entering', 'is-exiting');
            panel.removeAttribute('data-premium-floating');
        }

        function prepareHost(instance, host, currentPanel, nextPanel, reducedMotion) {
            if (!host) return;
            host.classList.add('ui-premium-panels-host');
            if (reducedMotion) {
                host.style.removeProperty('min-height');
                host.classList.remove('ui-premium-panels-animating');
                return;
            }

            const currentHeight = currentPanel ? currentPanel.offsetHeight : 0;
            const nextHeight = nextPanel ? nextPanel.offsetHeight : 0;
            host.style.minHeight = `${Math.max(currentHeight, nextHeight)}px`;
            host.classList.add('ui-premium-panels-animating');
        }

        function finalizeHost(host) {
            if (!host) return;
            host.style.removeProperty('min-height');
            host.classList.remove('ui-premium-panels-animating');
        }

        function activate(instance, key, options) {
            const opts = options || {};
            const normalizedKey = normalizeKey(key);
            const nextTrigger = findTriggerByKey(instance, normalizedKey);
            const nextPanel = findPanelByKey(instance, normalizedKey);
            const currentKey = normalizeKey(instance.activeKey || getActiveKey(instance));

            if (!nextTrigger || !nextPanel) {
                return false;
            }

            if (currentKey && currentKey === normalizedKey && opts.force !== true) {
                instance.activeKey = normalizedKey;
                syncTabAttributes(instance, normalizedKey);
                getPanels(instance).forEach((panel) => {
                    if (panel === nextPanel) {
                        panel.hidden = false;
                        panel.setAttribute('aria-hidden', 'false');
                        panel.classList.add(instance.options.activePanelClass);
                        panel.classList.remove('is-entering', 'is-exiting');
                        return;
                    }
                    cleanupPanelState(panel, instance.options.activePanelClass);
                });
                updateIndicator(instance);
                if (opts.focusTrigger) {
                    nextTrigger.focus({ preventScroll: true });
                }
                return true;
            }

            if (opts.emit !== false && typeof instance.options.onBeforeChange === 'function') {
                const shouldContinue = instance.options.onBeforeChange(normalizedKey, {
                    tab: nextTrigger,
                    panel: nextPanel,
                    source: opts.source || 'api',
                    activeKey: currentKey,
                    options: opts,
                });
                if (shouldContinue === false) {
                    return false;
                }
            }

            const reducedMotion = opts.immediate ? true : prefersReducedMotion();
            const panelDuration = reducedMotion ? 0 : instance.options.panelDuration;
            const currentPanel = findPanelByKey(instance, instance.activeKey);
            const panelHost = resolveElement(instance.options.panelHost, instance.root) || nextPanel.parentElement;

            if (instance.cleanupTimer) {
                window.clearTimeout(instance.cleanupTimer);
                instance.cleanupTimer = null;
            }

            syncTabAttributes(instance, normalizedKey);

            getPanels(instance).forEach((panel) => {
                if (panel !== currentPanel && panel !== nextPanel) {
                    cleanupPanelState(panel, instance.options.activePanelClass);
                }
            });

            nextPanel.hidden = false;
            nextPanel.setAttribute('aria-hidden', 'false');
            nextPanel.classList.remove('is-exiting');

            if (!currentPanel || currentPanel === nextPanel || reducedMotion) {
                if (currentPanel && currentPanel !== nextPanel) {
                    cleanupPanelState(currentPanel, instance.options.activePanelClass);
                }
                nextPanel.classList.remove('is-entering', 'is-exiting');
                nextPanel.classList.add(instance.options.activePanelClass);
                finalizeHost(panelHost);
            } else {
                prepareHost(instance, panelHost, currentPanel, nextPanel, reducedMotion);

                currentPanel.setAttribute('aria-hidden', 'true');
                currentPanel.classList.remove(instance.options.activePanelClass);
                currentPanel.classList.add('is-exiting');
                currentPanel.setAttribute('data-premium-floating', 'true');

                nextPanel.classList.remove(instance.options.activePanelClass);
                nextPanel.classList.add('is-entering');

                requestAnimationFrame(() => {
                    nextPanel.classList.add(instance.options.activePanelClass);
                    updateIndicator(instance);
                });

                instance.cleanupTimer = window.setTimeout(() => {
                    cleanupPanelState(currentPanel, instance.options.activePanelClass);
                    nextPanel.classList.remove('is-entering');
                    finalizeHost(panelHost);
                }, panelDuration + 24);
            }

            instance.activeKey = normalizedKey;
            updateIndicator(instance);

            if (opts.scrollIntoView !== false) {
                if (typeof nextTrigger.scrollIntoView === 'function') {
                    nextTrigger.scrollIntoView({
                        block: 'nearest',
                        inline: 'nearest',
                        behavior: reducedMotion ? 'auto' : 'smooth',
                    });
                }
            }

            if (opts.focusTrigger) {
                nextTrigger.focus({ preventScroll: true });
            }

            if (opts.emit !== false && typeof instance.options.onChange === 'function') {
                instance.options.onChange(normalizedKey, {
                    tab: nextTrigger,
                    panel: nextPanel,
                    source: opts.source || 'api',
                    options: opts,
                });
            }

            return true;
        }

        function bind(instance) {
            const clickHandler = (event) => {
                const trigger = event.target.closest(instance.options.tabSelector);
                if (!trigger || !instance.tablist.contains(trigger)) return;
                event.preventDefault();
                activate(instance, getKeyFromTrigger(instance, trigger), { source: 'click' });
            };

            const keydownHandler = (event) => {
                const trigger = event.target.closest(instance.options.tabSelector);
                if (!trigger || !instance.tablist.contains(trigger)) return;

                const tabs = getTabs(instance);
                const currentIndex = tabs.indexOf(trigger);
                if (currentIndex === -1) return;

                let nextIndex = currentIndex;
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                    nextIndex = (currentIndex + 1) % tabs.length;
                } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                    nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
                } else if (event.key === 'Home') {
                    nextIndex = 0;
                } else if (event.key === 'End') {
                    nextIndex = tabs.length - 1;
                } else if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    activate(instance, getKeyFromTrigger(instance, trigger), {
                        focusTrigger: true,
                        source: 'keyboard',
                    });
                    return;
                } else {
                    return;
                }

                event.preventDefault();
                const nextTrigger = tabs[nextIndex];
                if (!nextTrigger) return;

                activate(instance, getKeyFromTrigger(instance, nextTrigger), {
                    focusTrigger: true,
                    source: 'keyboard',
                });
            };

            const resizeHandler = () => updateIndicator(instance);

            instance.tablist.addEventListener('click', clickHandler);
            instance.tablist.addEventListener('keydown', keydownHandler);
            window.addEventListener('resize', resizeHandler, { passive: true });

            instance.cleanup.push(() => instance.tablist.removeEventListener('click', clickHandler));
            instance.cleanup.push(() => instance.tablist.removeEventListener('keydown', keydownHandler));
            instance.cleanup.push(() => window.removeEventListener('resize', resizeHandler));
        }

        function createHandle(instance) {
            return {
                root: instance.root,
                activate(key, options) {
                    return activate(instance, key, options);
                },
                sync() {
                    const initialKey = normalizeKey(
                        instance.options.initialKey
                        || getActiveKey(instance)
                        || getKeyFromTrigger(instance, getTabs(instance)[0], 0)
                    );

                    if (!initialKey) return false;
                    return activate(instance, initialKey, {
                        emit: false,
                        immediate: true,
                        scrollIntoView: false,
                    });
                },
                destroy() {
                    if (instance.cleanupTimer) {
                        window.clearTimeout(instance.cleanupTimer);
                        instance.cleanupTimer = null;
                    }
                    instance.cleanup.forEach((fn) => fn());
                    instance.cleanup = [];
                    registry.delete(instance.root);
                },
            };
        }

        const api = {
            init(options) {
                const opts = options || {};
                const tablist = resolveElement(opts.tablist || opts.tabs || '[data-premium-tabs]', opts.root ? resolveElement(opts.root) : document);
                if (!tablist) return null;

                const tabRoot = resolveElement(opts.root, document) || tablist.closest('[data-premium-tabs-root]') || tablist.parentElement;
                if (!tabRoot) return null;

                if (registry.has(tabRoot)) {
                    return createHandle(registry.get(tabRoot));
                }

                const instance = {
                    uid: `ui-premium-tabs-${++instanceCounter}`,
                    root: tabRoot,
                    tablist,
                    activeKey: '',
                    cleanup: [],
                    cleanupTimer: null,
                    options: {
                        tabSelector: opts.tabSelector || '[data-premium-tab]',
                        panelSelector: opts.panelSelector || '[data-premium-panel]',
                        activeClass: opts.activeClass || 'active',
                        activePanelClass: opts.activePanelClass || 'active',
                        panelHost: opts.panelHost || '[data-premium-panels-host]',
                        panelDuration: Number.isFinite(Number(opts.panelDuration)) ? Number(opts.panelDuration) : 220,
                        getKey: opts.getKey,
                        getPanelKey: opts.getPanelKey,
                        onBeforeChange: typeof opts.onBeforeChange === 'function' ? opts.onBeforeChange : null,
                        onChange: typeof opts.onChange === 'function' ? opts.onChange : null,
                        initialKey: opts.initialKey || '',
                    },
                };

                ensureAccessibleLinking(instance);
                bind(instance);
                registry.set(tabRoot, instance);

                const handle = createHandle(instance);
                handle.sync();
                return handle;
            },
        };

        root.UI.PremiumTabs = api;
    } catch (error) {
        console.error('[UI.PremiumTabs] Failed to initialize helper', error);
    }
})(window);
