import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const ackFallbackStore = require('../ackFallbackStore.js');

describe('ackFallbackStore', () => {
  it('marks and clears acknowledge state', () => {
    const store = ackFallbackStore.createAckFallbackStore({ key: 'ack-unit', storage: window.localStorage });
    store.clear();
    expect(store.isAcked('abc')).toBe(false);
    store.markAcked('abc');
    expect(store.isAcked('abc')).toBe(true);
    store.clear();
    expect(store.isAcked('abc')).toBe(false);
  });

  it('handles invalid storage payload and write failure gracefully', () => {
    const storage = {
      getItem: () => '{',
      setItem: () => {
        throw new Error('write failed');
      },
    };
    const store = ackFallbackStore.createAckFallbackStore({ key: 'ack-fail', storage });
    expect(store.isAcked('evt-1')).toBe(false);
    expect(() => store.markAcked('evt-1')).not.toThrow();
    expect(() => store.clear()).not.toThrow();
  });

  it('supports missing storage without throwing', () => {
    const store = ackFallbackStore.createAckFallbackStore({ key: 'ack-none', storage: null });
    expect(store.isAcked('evt-2')).toBe(false);
    expect(() => store.markAcked('evt-2')).not.toThrow();
  });
});
