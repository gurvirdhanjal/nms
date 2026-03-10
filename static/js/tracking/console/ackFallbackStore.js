(function (global) {
    'use strict';

    function createAckFallbackStore(options) {
        const config = options || {};
        const key = String(config.key || 'device_console_ack_fallback');
        const storage = config.storage || (typeof window !== 'undefined' ? window.localStorage : null);

        function read() {
            if (!storage) return {};
            try {
                const raw = storage.getItem(key);
                if (!raw) return {};
                const parsed = JSON.parse(raw);
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (error) {
                return {};
            }
        }

        function write(data) {
            if (!storage) return;
            try {
                storage.setItem(key, JSON.stringify(data || {}));
            } catch (error) {
                // best effort
            }
        }

        return {
            isAcked: function (eventId) {
                const map = read();
                return Boolean(map[String(eventId || '')]);
            },
            markAcked: function (eventId) {
                const map = read();
                map[String(eventId || '')] = new Date().toISOString();
                write(map);
                return map;
            },
            clear: function () {
                write({});
            },
        };
    }

    const api = {
        createAckFallbackStore: createAckFallbackStore,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleAckFallbackStore = api;
})(typeof window !== 'undefined' ? window : globalThis);
