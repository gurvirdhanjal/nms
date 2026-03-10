(function (global) {
    'use strict';

    function createMutationLocks() {
        const locks = new Map();

        function isLocked(key) {
            return locks.get(String(key)) === true;
        }

        function setLock(key, value) {
            locks.set(String(key), Boolean(value));
        }

        async function withLock(key, task) {
            const normalized = String(key || '').trim();
            if (!normalized) {
                return task();
            }
            if (isLocked(normalized)) {
                const error = new Error('Action already in progress');
                error.code = 'LOCKED';
                throw error;
            }
            setLock(normalized, true);
            try {
                return await task();
            } finally {
                setLock(normalized, false);
            }
        }

        return {
            isLocked: isLocked,
            lock: function (key) { setLock(key, true); },
            unlock: function (key) { setLock(key, false); },
            withLock: withLock,
        };
    }

    const api = {
        createMutationLocks: createMutationLocks,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    global.DeviceConsoleMutationLocks = api;
})(typeof window !== 'undefined' ? window : globalThis);
