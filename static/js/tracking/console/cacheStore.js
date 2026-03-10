(function (global) {
    'use strict';

    function nowMs() {
        return Date.now();
    }

    function createCacheStore(defaultTtlMs) {
        const ttl = Math.max(0, Number(defaultTtlMs) || 10000);
        const store = new Map();
        const inflight = new Map();

        function getEntry(key) {
            const row = store.get(String(key));
            if (!row) return null;
            if (row.expiresAt > 0 && row.expiresAt <= nowMs()) {
                store.delete(String(key));
                return null;
            }
            return row;
        }

        return {
            get: function (key) {
                const row = getEntry(key);
                return row ? row.value : null;
            },
            set: function (key, value, ttlMs) {
                const effectiveTtl = Number.isFinite(Number(ttlMs)) ? Math.max(0, Number(ttlMs)) : ttl;
                const expiresAt = effectiveTtl > 0 ? nowMs() + effectiveTtl : 0;
                store.set(String(key), { value: value, expiresAt: expiresAt });
                return value;
            },
            has: function (key) {
                return Boolean(getEntry(key));
            },
            invalidate: function (key) {
                const normalized = String(key || '').trim();
                if (!normalized) return;
                store.delete(normalized);
            },
            invalidateMany: function (keys) {
                (Array.isArray(keys) ? keys : []).forEach(function (key) {
                    store.delete(String(key));
                });
            },
            clear: function () {
                store.clear();
                inflight.clear();
            },
            getOrSet: function (key, fetcher, ttlMs) {
                const cached = getEntry(key);
                if (cached) {
                    return Promise.resolve(cached.value);
                }

                const cacheKey = String(key);
                if (inflight.has(cacheKey)) {
                    return inflight.get(cacheKey);
                }

                const promise = Promise.resolve()
                    .then(function () { return fetcher(); })
                    .then(function (value) {
                        inflight.delete(cacheKey);
                        return this.set(cacheKey, value, ttlMs);
                    }.bind(this))
                    .catch(function (error) {
                        inflight.delete(cacheKey);
                        throw error;
                    });

                inflight.set(cacheKey, promise);
                return promise;
            },
        };
    }

    const api = {
        createCacheStore: createCacheStore,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleCacheStore = api;
})(typeof window !== 'undefined' ? window : globalThis);
