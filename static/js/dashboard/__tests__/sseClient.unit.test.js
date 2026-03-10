import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __test__,
  ConnectionStatus,
  disconnect,
  getConnectionStatus,
  initSSE,
  reconnect,
} from '../sseClient.js';

let instances = [];

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    this.onopen = null;
    this.onerror = null;
    instances.push(this);
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }

  emit(name, payload) {
    if (this.listeners[name]) {
      this.listeners[name]({ data: JSON.stringify(payload) });
    }
  }

  close() {
    this.closed = true;
  }
}

describe('sseClient dedupe', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    __test__.resetDeduper();
    instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    disconnect();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('deduplicates repeated delivery keys', () => {
    const alerts = [];
    initSSE({ onAlertCreated: (payload) => alerts.push(payload) });

    __test__.handleEvent('alert_created', { data: JSON.stringify({ delivery_key: 'dup-1', payload: { id: 1 } }) });
    __test__.handleEvent('alert_created', { data: JSON.stringify({ delivery_key: 'dup-1', payload: { id: 2 } }) });
    vi.advanceTimersByTime(150);

    expect(alerts).toEqual([{ id: 1 }]);
    expect(__test__.getDeduperSize()).toBe(1);
  });

  it('falls back to event_id when delivery_key is absent and ignores invalid json', () => {
    const alerts = [];
    initSSE({ onAlertCreated: (payload) => alerts.push(payload) });

    __test__.handleEvent('alert_created', { data: '{' });
    __test__.handleEvent('alert_created', { data: JSON.stringify({ event_id: 'evt-1', payload: { ok: true } }) });
    __test__.handleEvent('alert_created', { data: JSON.stringify({ event_id: 'evt-1', payload: { ok: false } }) });
    vi.advanceTimersByTime(150);

    expect(alerts).toEqual([{ ok: true }]);
    expect(__test__.getDeduperSize()).toBe(1);
  });

  it('routes registered SSE event types through their handlers and updates connection status', () => {
    const statuses = [];
    const seen = {
      deviceStatus: [],
      batch: [],
      alerts: [],
      latency: [],
      interfaces: [],
      classification: [],
    };

    initSSE({
      onConnectionChange: (status) => statuses.push(status),
      onDeviceStatus: (payload) => seen.deviceStatus.push(payload),
      onDeviceUpdateBatch: (payload) => seen.batch.push(payload),
      onAlertCreated: (payload) => seen.alerts.push(payload),
      onLatencySpike: (payload) => seen.latency.push(payload),
      onInterfaceThreshold: (payload) => seen.interfaces.push(payload),
      onClassificationUpdate: (payload) => {
        seen.classification.push(payload);
        throw new Error('handler failed');
      },
    });

    const source = instances[0];
    expect(getConnectionStatus()).toBe(ConnectionStatus.CONNECTING);
    source.onopen();
    expect(getConnectionStatus()).toBe(ConnectionStatus.CONNECTED);

    source.emit('connected', { ok: true });
    source.emit('heartbeat', {});
    source.emit('device_status', { payload: { id: 'status-1' } });
    source.emit('device_update', { payload: { id: 'update-1' } });
    source.emit('device_update_batch', { payload: { id: 'batch-1' } });
    source.emit('alert_created', { delivery_key: 'alert-1', payload: { id: 'alert-1' } });
    source.emit('latency_spike', { payload: { id: 'latency-1' } });
    source.emit('interface_threshold', { payload: { id: 'interface-1' } });
    source.emit('classification_update', { payload: { id: 'class-1' } });
    vi.advanceTimersByTime(150);

    expect(statuses).toEqual([ConnectionStatus.CONNECTING, ConnectionStatus.CONNECTED]);
    expect(seen.deviceStatus).toEqual([{ id: 'status-1' }, { id: 'update-1' }]);
    expect(seen.batch).toEqual([{ id: 'batch-1' }]);
    expect(seen.alerts).toEqual([{ id: 'alert-1' }]);
    expect(seen.latency).toEqual([{ id: 'latency-1' }]);
    expect(seen.interfaces).toEqual([{ id: 'interface-1' }]);
    expect(seen.classification).toEqual([{ id: 'class-1' }]);
    expect(console.error).toHaveBeenCalled();
  });

  it('reconnects on error and heartbeat timeout, and manual reconnect creates a new source', () => {
    const statuses = [];
    initSSE({ onConnectionChange: (status) => statuses.push(status) });

    const first = instances[0];
    first.onopen();
    first.onerror(new Error('boom'));

    expect(first.closed).toBe(true);
    expect(getConnectionStatus()).toBe(ConnectionStatus.DISCONNECTED);

    vi.advanceTimersByTime(1000);
    const second = instances[1];
    second.onopen();
    expect(getConnectionStatus()).toBe(ConnectionStatus.CONNECTED);

    vi.advanceTimersByTime(45000);
    expect(second.closed).toBe(true);
    expect(getConnectionStatus()).toBe(ConnectionStatus.DISCONNECTED);

    vi.advanceTimersByTime(1000);
    expect(instances).toHaveLength(3);

    reconnect();
    expect(instances[2].closed).toBe(true);
    expect(instances).toHaveLength(4);
    expect(getConnectionStatus()).toBe(ConnectionStatus.CONNECTING);

    disconnect();
    expect(instances[3].closed).toBe(true);
    expect(getConnectionStatus()).toBe(ConnectionStatus.DISCONNECTED);
    expect(statuses).toContain(ConnectionStatus.DISCONNECTED);
  });

  it('handles EventSource construction failures and trims the dedupe window to 100 entries', () => {
    vi.stubGlobal('EventSource', class BrokenEventSource {
      constructor() {
        throw new Error('no sse available');
      }
    });

    initSSE();
    expect(getConnectionStatus()).toBe(ConnectionStatus.DISCONNECTED);

    vi.stubGlobal('EventSource', FakeEventSource);
    initSSE({ onAlertCreated: () => {} });
    for (let index = 0; index < 101; index += 1) {
      __test__.handleEvent('alert_created', {
        data: JSON.stringify({ delivery_key: `key-${index}`, payload: { index } }),
      });
    }
    vi.advanceTimersByTime(150);

    expect(__test__.getDeduperSize()).toBe(100);
  });
});
