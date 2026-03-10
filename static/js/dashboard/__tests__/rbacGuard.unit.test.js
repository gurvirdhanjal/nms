import { describe, expect, it, vi, beforeEach } from 'vitest';

import { enforceSnapshotMeta, hasSnapshotMetaMismatch, shouldForceRefresh } from '../rbacGuard.js';


describe('rbacGuard', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.__RBAC_CONTEXT__ = {
      role: 'manager',
      scope_key: 'site:1',
      scope_label: 'Site Alpha',
    };
  });

  it('detects role/scope mismatch', () => {
    expect(
      hasSnapshotMetaMismatch({ role: 'manager', scope_key: 'site:2' }, window.__RBAC_CONTEXT__)
    ).toBe(true);

    expect(
      hasSnapshotMetaMismatch({ role: 'manager', scope_key: 'site:1' }, window.__RBAC_CONTEXT__)
    ).toBe(false);

    expect(
      hasSnapshotMetaMismatch({ role: '', scope_key: '' }, window.__RBAC_CONTEXT__)
    ).toBe(false);
  });

  it('forces a single refresh on mismatch and then stabilizes', () => {
    const first = shouldForceRefresh({ role: 'viewer', scope_key: 'department:1' });
    const second = shouldForceRefresh({ role: 'viewer', scope_key: 'department:1' });

    expect(first).toBe(true);
    expect(second).toBe(false);
  });

  it('calls location.reload exactly once when mismatch is detected', () => {
    const storage = {
      _values: new Map(),
      getItem(key) { return this._values.has(key) ? this._values.get(key) : null; },
      setItem(key, value) { this._values.set(key, String(value)); },
      removeItem(key) { this._values.delete(key); },
    };

    const reload = vi.fn();
    const locationObj = { reload };

    const first = enforceSnapshotMeta(
      { role: 'admin', scope_key: 'global' },
      { storage, locationObj, windowObj: window }
    );
    const second = enforceSnapshotMeta(
      { role: 'admin', scope_key: 'global' },
      { storage, locationObj, windowObj: window }
    );

    expect(first).toBe(true);
    expect(second).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('returns false when refresh is not needed or storage is unavailable', () => {
    const storage = {
      _values: new Map([['dashboard:rbac-refresh-once', '1']]),
      getItem(key) { return this._values.has(key) ? this._values.get(key) : null; },
      setItem(key, value) { this._values.set(key, String(value)); },
      removeItem(key) { this._values.delete(key); },
    };

    const noMismatch = shouldForceRefresh({ role: 'manager', scope_key: 'site:1' }, { storage });
    expect(noMismatch).toBe(false);
    expect(storage.getItem('dashboard:rbac-refresh-once')).toBe(null);

    const noStorage = shouldForceRefresh(
      { role: 'admin', scope_key: 'global' },
      { storage: null, windowObj: {}, rbacContext: { role: 'manager', scope_key: 'site:1' } }
    );
    expect(noStorage).toBe(false);
  });

  it('returns false when location reload is missing or throws', () => {
    const storage = {
      _values: new Map(),
      getItem(key) { return this._values.has(key) ? this._values.get(key) : null; },
      setItem(key, value) { this._values.set(key, String(value)); },
      removeItem(key) { this._values.delete(key); },
    };

    const missingReload = enforceSnapshotMeta(
      { role: 'admin', scope_key: 'global' },
      { storage, locationObj: {}, windowObj: window }
    );
    expect(missingReload).toBe(false);

    storage._values.clear();
    const throwingReload = enforceSnapshotMeta(
      { role: 'admin', scope_key: 'global' },
      {
        storage,
        locationObj: { reload: () => { throw new Error('boom'); } },
        windowObj: window,
      }
    );
    expect(throwingReload).toBe(false);
  });
});

