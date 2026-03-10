(function (global) {
    'use strict';

    function createEventFeedStore(options) {
        const config = options || {};
        const key = String(config.key || 'device_console_events');
        const maxItems = Math.max(10, Number(config.maxItems) || 60);
        const dedupeWindow = Math.max(5, Number(config.dedupeWindow) || 50);
        const storage = config.storage || (typeof window !== 'undefined' ? window.sessionStorage : null);

        function read() {
            if (!storage) return [];
            try {
                const raw = storage.getItem(key);
                if (!raw) return [];
                const parsed = JSON.parse(raw);
                return Array.isArray(parsed) ? parsed : [];
            } catch (error) {
                return [];
            }
        }

        function write(rows) {
            if (!storage) return;
            try {
                storage.setItem(key, JSON.stringify(rows));
            } catch (error) {
                // best effort
            }
        }

        return {
            list: function () {
                return read();
            },
            push: function (event) {
                const item = {
                    id: String(event && event.id || `${Date.now()}`),
                    deliveryKey: String(event && (event.deliveryKey || event.delivery_key) || ''),
                    time: String(event && event.time || ''),
                    text: String(event && event.text || 'Event'),
                    level: String(event && event.level || 'info'),
                };
                const rows = read();
                if (item.deliveryKey) {
                    const duplicate = rows.slice(0, dedupeWindow).some(function (row) {
                        return String(row && row.deliveryKey || '') === item.deliveryKey;
                    });
                    if (duplicate) {
                        return rows;
                    }
                }
                rows.unshift(item);
                const trimmed = rows.slice(0, maxItems);
                write(trimmed);
                return trimmed;
            },
            clear: function () {
                write([]);
            },
        };
    }

    const api = {
        createEventFeedStore: createEventFeedStore,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleEventFeedStore = api;
})(typeof window !== 'undefined' ? window : globalThis);
