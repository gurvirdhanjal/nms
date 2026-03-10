import { describe, expect, it, vi } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const cacheStore = require('../cacheStore.js');

describe('cacheStore', () => {
  it('sets and gets cached values', () => {
    const cache = cacheStore.createCacheStore(1000);
    cache.set('policy:1', { ok: true });
    expect(cache.get('policy:1')).toEqual({ ok: true });
    expect(cache.has('policy:1')).toBe(true);
  });

  it('invalidates keys and key sets', () => {
    const cache = cacheStore.createCacheStore(1000);
    cache.set('a', 1);
    cache.set('b', 2);
    cache.invalidate('a');
    expect(cache.get('a')).toBeNull();
    cache.invalidateMany(['b']);
    expect(cache.get('b')).toBeNull();
    cache.invalidate('');
    cache.clear();
    expect(cache.get('b')).toBeNull();
  });

  it('deduplicates inflight fetches', async () => {
    const cache = cacheStore.createCacheStore(1000);
    let count = 0;
    const fetcher = async () => {
      count += 1;
      return { value: 42 };
    };

    const [a, b] = await Promise.all([
      cache.getOrSet('k', fetcher),
      cache.getOrSet('k', fetcher),
    ]);

    expect(count).toBe(1);
    expect(a).toEqual({ value: 42 });
    expect(b).toEqual({ value: 42 });
  });

  it('returns cached value immediately in getOrSet', async () => {
    const cache = cacheStore.createCacheStore(1000);
    cache.set('warm', 'ok');
    const fetcher = vi.fn(async () => 'miss');
    const value = await cache.getOrSet('warm', fetcher);
    expect(value).toBe('ok');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('drops expired entries and clears inflight on rejection', async () => {
    vi.useFakeTimers();
    const cache = cacheStore.createCacheStore(5);
    cache.set('expiring', 1);
    vi.advanceTimersByTime(6);
    expect(cache.get('expiring')).toBeNull();

    const failFetcher = vi.fn(async () => {
      throw new Error('boom');
    });
    await expect(cache.getOrSet('err', failFetcher)).rejects.toThrow('boom');

    const okFetcher = vi.fn(async () => 7);
    const recovered = await cache.getOrSet('err', okFetcher);
    expect(recovered).toBe(7);
    expect(okFetcher).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
