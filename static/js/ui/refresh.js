(function initUiRefresh(global) {
    try {
        const root = global || window;
        root.UI = root.UI || {};

        function createController(config) {
            const opts = config || {};
            let requestSeq = 0;
            let activeController = null;
            let destroyed = false;
            let deferredOptions = null;
            let visibilityHandler = null;
            const state = {
                hasHydratedOnce: false,
                isRefreshing: false,
                lastSuccessAt: 0,
                deferred: false,
                lastReason: '',
                error: null,
            };

            function emitState() {
                if (typeof opts.onStateChange === 'function') {
                    opts.onStateChange({
                        hasHydratedOnce: state.hasHydratedOnce,
                        isRefreshing: state.isRefreshing,
                        lastSuccessAt: state.lastSuccessAt,
                        deferred: state.deferred,
                        lastReason: state.lastReason,
                        error: state.error,
                    });
                }
            }

            function refresh(refreshOptions) {
                if (destroyed) return Promise.resolve(null);

                const runOptions = refreshOptions || {};
                const manual = Boolean(runOptions.manual);
                const reason = String(runOptions.reason || (manual ? 'manual' : 'auto'));

                if (!manual && document.visibilityState === 'hidden') {
                    deferredOptions = runOptions;
                    state.deferred = true;
                    emitState();
                    return Promise.resolve(null);
                }

                if (typeof opts.shouldDefer === 'function' && opts.shouldDefer(runOptions)) {
                    deferredOptions = runOptions;
                    state.deferred = true;
                    emitState();
                    return Promise.resolve(null);
                }

                state.isRefreshing = true;
                state.deferred = false;
                state.lastReason = reason;
                state.error = null;
                emitState();

                if (activeController && typeof activeController.abort === 'function') {
                    activeController.abort();
                }

                const seq = ++requestSeq;
                activeController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                const signal = activeController ? activeController.signal : undefined;

                return Promise.resolve()
                    .then(function runFetcher() {
                        return opts.fetcher({
                            signal: signal,
                            manual: manual,
                            reason: reason,
                            hasHydratedOnce: state.hasHydratedOnce,
                        });
                    })
                    .then(function applyResult(payload) {
                        if (destroyed || seq !== requestSeq) {
                            return null;
                        }
                        state.hasHydratedOnce = true;
                        state.isRefreshing = false;
                        state.lastSuccessAt = Date.now();
                        state.error = null;
                        if (typeof opts.applyData === 'function') {
                            opts.applyData(payload, {
                                signal: signal,
                                manual: manual,
                                reason: reason,
                                hasHydratedOnce: state.hasHydratedOnce,
                            });
                        }
                        emitState();
                        return payload;
                    })
                    .catch(function handleError(error) {
                        if (error && error.name === 'AbortError') {
                            return null;
                        }
                        if (destroyed || seq !== requestSeq) {
                            return null;
                        }
                        state.isRefreshing = false;
                        state.error = error;
                        if (typeof opts.onError === 'function') {
                            opts.onError(error, {
                                manual: manual,
                                reason: reason,
                                hasHydratedOnce: state.hasHydratedOnce,
                            });
                        }
                        emitState();
                        return null;
                    })
                    .finally(function finalize() {
                        if (activeController && activeController.signal === signal) {
                            activeController = null;
                        }
                    });
            }

            function flushDeferred() {
                if (!deferredOptions) {
                    return Promise.resolve(null);
                }
                const pending = deferredOptions;
                deferredOptions = null;
                state.deferred = false;
                emitState();
                return refresh(pending);
            }

            function getMeta() {
                return {
                    hasHydratedOnce: state.hasHydratedOnce,
                    isRefreshing: state.isRefreshing,
                    lastSuccessAt: state.lastSuccessAt,
                    deferred: state.deferred,
                    lastReason: state.lastReason,
                    error: state.error,
                };
            }

            function destroy() {
                destroyed = true;
                if (activeController && typeof activeController.abort === 'function') {
                    activeController.abort();
                }
                activeController = null;
                if (visibilityHandler) {
                    document.removeEventListener('visibilitychange', visibilityHandler);
                }
            }

            if (Number.isFinite(opts.resumeStaleMs) && opts.resumeStaleMs > 0) {
                visibilityHandler = function onVisibilityChange() {
                    if (destroyed || document.visibilityState !== 'visible') {
                        return;
                    }
                    const age = state.lastSuccessAt ? Date.now() - state.lastSuccessAt : Number.MAX_SAFE_INTEGER;
                    if (!state.hasHydratedOnce || age >= opts.resumeStaleMs) {
                        void flushDeferred().then(function maybeRefresh() {
                            if (!deferredOptions) {
                                return refresh({ reason: 'visibility-resume' });
                            }
                            return null;
                        });
                    }
                };
                document.addEventListener('visibilitychange', visibilityHandler);
            }

            return {
                refresh: refresh,
                flushDeferred: flushDeferred,
                destroy: destroy,
                getMeta: getMeta,
            };
        }

        root.UI.Refresh = {
            createController: createController,
        };
    } catch (error) {
        console.error('[UI.Refresh] initialization failed:', error);
        try {
            window.UI = window.UI || {};
            window.UI.Refresh = null;
        } catch (_ignored) {
            // Ignore secondary failures.
        }
    }
}(typeof window !== 'undefined' ? window : this));
