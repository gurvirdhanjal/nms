import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const eventFeedStore = require('../eventFeedStore.js');

describe('eventFeedStore', () => {
  it('persists ring buffer entries in storage', () => {
    const store = eventFeedStore.createEventFeedStore({
      key: 'event-feed-test',
      maxItems: 3,
      storage: window.sessionStorage,
    });
    store.clear();
    for (let i = 1; i <= 12; i += 1) {
      store.push({ id: String(i), time: `10:${i}`, text: `Event ${i}` });
    }

    const rows = store.list();
    expect(rows).toHaveLength(10);
    expect(rows[0].id).toBe('12');
    expect(rows[9].id).toBe('3');
  });

  it('falls back safely for invalid JSON, write failures, and no storage', () => {
    const badJsonStorage = {
      getItem: () => '{',
      setItem: () => undefined,
    };
    const storeFromBadJson = eventFeedStore.createEventFeedStore({
      key: 'event-feed-bad-json',
      storage: badJsonStorage,
    });
    expect(storeFromBadJson.list()).toEqual([]);

    const writeFailStorage = {
      getItem: () => '[]',
      setItem: () => {
        throw new Error('no write');
      },
    };
    const storeWithWriteFailure = eventFeedStore.createEventFeedStore({
      key: 'event-feed-write-fail',
      storage: writeFailStorage,
    });
    expect(() => storeWithWriteFailure.push({ text: 'x' })).not.toThrow();
    expect(() => storeWithWriteFailure.clear()).not.toThrow();

    const memoryOnlyStore = eventFeedStore.createEventFeedStore({
      key: 'event-feed-memory',
      storage: null,
    });
    expect(memoryOnlyStore.list()).toEqual([]);
    expect(memoryOnlyStore.push({ text: 'ephemeral' })).toHaveLength(1);
  });

  it('deduplicates by delivery key within the configured window', () => {
    const store = eventFeedStore.createEventFeedStore({
      key: 'event-feed-dedupe',
      maxItems: 20,
      dedupeWindow: 10,
      storage: window.sessionStorage,
    });
    store.clear();

    store.push({ id: '1', delivery_key: 'dup-1', text: 'First' });
    store.push({ id: '2', deliveryKey: 'dup-1', text: 'Duplicate' });
    store.push({ id: '3', deliveryKey: 'dup-2', text: 'Second' });

    const rows = store.list();
    expect(rows).toHaveLength(2);
    expect(rows[0].deliveryKey).toBe('dup-2');
    expect(rows[1].deliveryKey).toBe('dup-1');
  });
});
