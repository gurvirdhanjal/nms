const REFRESH_GUARD_KEY = 'dashboard:rbac-refresh-once';

function normalizeText(value) {
    return String(value || '').trim().toLowerCase();
}

export function getRbacContext(windowObj = window) {
    const context = (windowObj && windowObj.__RBAC_CONTEXT__) || {};
    return {
        role: normalizeText(context.role),
        scope_key: String(context.scope_key || '').trim(),
        scope_label: String(context.scope_label || '').trim(),
    };
}

export function hasSnapshotMetaMismatch(snapshotMeta, rbacContext) {
    const meta = snapshotMeta && typeof snapshotMeta === 'object' ? snapshotMeta : null;
    if (!meta) {
        return false;
    }

    const context = rbacContext || getRbacContext();
    const metaRole = normalizeText(meta.role);
    const contextRole = normalizeText(context.role);
    const metaScopeKey = String(meta.scope_key || '').trim();
    const contextScopeKey = String(context.scope_key || '').trim();

    if (!metaRole || !metaScopeKey || !contextRole || !contextScopeKey) {
        return false;
    }

    return metaRole !== contextRole || metaScopeKey !== contextScopeKey;
}

export function shouldForceRefresh(snapshotMeta, options = {}) {
    const windowObj = options.windowObj || window;
    const storage = options.storage || (windowObj ? windowObj.sessionStorage : null);
    const guardKey = String(options.guardKey || REFRESH_GUARD_KEY);
    const context = options.rbacContext || getRbacContext(windowObj);

    const mismatch = hasSnapshotMetaMismatch(snapshotMeta, context);
    if (!mismatch) {
        storage && storage.removeItem(guardKey);
        return false;
    }

    if (!storage) {
        return false;
    }

    if (storage.getItem(guardKey) === '1') {
        return false;
    }

    storage.setItem(guardKey, '1');
    return true;
}

export function enforceSnapshotMeta(snapshotMeta, options = {}) {
    const windowObj = options.windowObj || window;
    const locationObj = options.locationObj || (windowObj ? windowObj.location : null);

    if (!shouldForceRefresh(snapshotMeta, options)) {
        return false;
    }

    try {
        if (locationObj && typeof locationObj.reload === 'function') {
            locationObj.reload();
            return true;
        }
    } catch (error) {
        console.warn('[Dashboard] Failed to force refresh on RBAC mismatch:', error);
    }

    return false;
}
