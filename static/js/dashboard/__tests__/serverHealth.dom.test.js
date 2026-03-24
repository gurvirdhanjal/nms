/* @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../modals/serverDetailModal.js', () => ({
  openServerModal: vi.fn(),
}));

import {
  renderEnhancedServerTable,
  renderFleetOverview,
  renderServerHealthSummary,
  setServerHealthFilter,
} from '../servers/serverHealth.js';

describe('fleet command center renderer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-23T10:00:00Z'));
    setServerHealthFilter('all');
    document.body.innerHTML = `
      <div id="fleet-priority-banner" class="fleet-priority-banner initially-hidden"></div>
      <span id="fleet-banner-severity"></span>
      <div id="fleet-banner-message"></div>
      <div id="fleet-banner-subtext"></div>
      <button id="btn-fleet-banner-view" type="button"></button>
      <button id="btn-fleet-banner-ack" type="button"></button>
      <div id="fleet-impact-title"></div>
      <div id="fleet-impact-footprint"></div>
      <div id="fleet-impact-unaffected"></div>
      <div id="val-fleet-health-percent"></div>
      <div id="val-fleet-health-state"></div>
      <div id="val-fleet-health-counts"></div>
      <div id="card-fleet-health"></div>
      <div id="fleet-card-cpu"></div>
      <div id="fleet-card-memory"></div>
      <div id="fleet-card-disk"></div>
      <div id="val-fleet-avg-cpu"></div>
      <div id="val-fleet-avg-mem"></div>
      <div id="val-fleet-avg-disk"></div>
      <div id="val-fleet-cpu-state"></div>
      <div id="val-fleet-memory-state"></div>
      <div id="val-fleet-disk-state"></div>
      <div id="val-fleet-cpu-delta"></div>
      <div id="val-fleet-memory-delta"></div>
      <div id="val-fleet-disk-delta"></div>
      <div id="val-fleet-cpu-impact"></div>
      <div id="val-fleet-memory-impact"></div>
      <div id="val-fleet-disk-impact"></div>
      <div id="val-fleet-p95-cpu"></div>
      <div id="val-fleet-p95-mem"></div>
      <div id="val-fleet-p95-disk"></div>
      <div id="val-fleet-uptime"></div>
      <div id="fleet-active-issues-count"></div>
      <div id="fleet-active-issues-list"></div>
      <div id="fleet-trend-cpu-meta"></div>
      <div id="fleet-trend-memory-meta"></div>
      <div id="fleet-trend-disk-meta"></div>
      <button data-server-filter="all">All (0)</button>
      <button data-server-filter="problem">Problems (0)</button>
      <button data-server-filter="healthy">Healthy (0)</button>
      <canvas id="chart-fleet-trend-cpu"></canvas>
      <canvas id="chart-fleet-trend-memory"></canvas>
      <canvas id="chart-fleet-trend-disk"></canvas>
      <table>
        <tbody id="table-server-health-body"></tbody>
      </table>
    `;

    global.Chart = class ChartStub {
      constructor(canvas, config) {
        this.canvas = canvas;
        this.config = config;
      }

      destroy() {}
    };
  });

  afterEach(() => {
    vi.useRealTimers();
    delete global.Chart;
  });

  it('renders dominant incident banner, impact summary, and active issues', () => {
    renderFleetOverview({
      health: { healthy: 0, warning: 1, critical: 0, offline: 0 },
      impact_summary: {
        affected_servers: 1,
        healthy_servers: 0,
        total_servers: 1,
        fleet_pct: 100,
        primary_issue_label: 'Memory Pressure',
        primary_issue_severity: 'Warning',
        unaffected_domains: ['CPU', 'Disk'],
      },
      metric_cards: {
        cpu: { value: 22.7, severity: 'healthy', impacted_servers: 0, delta_24h: -1.2 },
        memory: { value: 82.8, severity: 'warning', impacted_servers: 1, delta_24h: 12.0 },
        disk: { value: 16.4, severity: 'healthy', impacted_servers: 0, delta_24h: 0.6 },
      },
      dominant_issue: {
        device_id: 42,
        hostname: 'apldeveloper',
        device_name: 'apldeveloper',
        severity: 'WARNING',
        metric_label: 'Memory Pressure',
        formatted_value: '82.8%',
        message: 'Memory Pressure 82.8% (Warning)',
        event_id: 'evt-1',
      },
      active_issues: [
        {
          device_id: 42,
          hostname: 'apldeveloper',
          device_name: 'apldeveloper',
          ip: '172.16.2.96',
          severity: 'WARNING',
          metric_label: 'Memory Pressure',
          formatted_value: '82.8%',
          metrics: { cpu: 22.7, memory: 82.8, disk: 16.4 },
        },
      ],
      trends: {
        cpu: { labels: ['2026-03-23T08:00:00Z'], values: [22.7], markers: [], delta: -1.2, bands: [], warning: 80, critical: 95 },
        memory: { labels: ['2026-03-23T08:00:00Z'], values: [82.8], markers: [{ index: 0, value: 82.8, state: 'warning' }], delta: 12.0, bands: [], warning: 85, critical: 95 },
        disk: { labels: ['2026-03-23T08:00:00Z'], values: [16.4], markers: [], delta: 0.6, bands: [], warning: 80, critical: 95 },
      },
      p95: { cpu: 22.7, memory: 82.8, disk: 16.4 },
      uptime: { current_24h_pct: 99.2, delta_pct: -3.0 },
      filters: { all: 1, problem: 1, healthy: 0 },
    });

    expect(document.getElementById('fleet-priority-banner').className).toContain('severity-warning');
    expect(document.getElementById('fleet-banner-message').textContent).toContain('MEMORY PRESSURE');
    expect(document.getElementById('fleet-impact-footprint').textContent).toContain('1 of 1 servers affected');
    expect(document.getElementById('val-fleet-memory-state').textContent).toBe('Warning');
    expect(document.getElementById('val-fleet-memory-impact').textContent).toContain('1 server impacted');
    expect(document.getElementById('fleet-active-issues-list').textContent).toContain('apldeveloper');
    expect(document.querySelector('[data-server-filter="problem"]').textContent).toBe('Problems (1)');
  });

  it('renders highlighted problem rows and keeps ten columns aligned', () => {
    renderEnhancedServerTable({
      servers: [
        {
          device_id: 42,
          device_name: 'apldeveloper',
          hostname: 'apldeveloper',
          ip: '172.16.2.96',
          health: 'Warning',
          cpu_usage: 22.7,
          memory_usage: 82.8,
          disk_usage: 16.4,
          latency: 8,
          packet_loss: 0,
          jitter: 0,
          last_seen: '2026-03-23T09:59:52Z',
          primary_issue: {
            severity: 'WARNING',
            metric_label: 'Memory Pressure',
            formatted_value: '82.8%',
          },
        },
      ],
    });

    const row = document.querySelector('#table-server-health-body tr');
    expect(row.children).toHaveLength(10);
    expect(row.className).toContain('row-warning');
    expect(row.children[1].textContent).toContain('Memory Pressure');
    expect(row.children[9].querySelectorAll('button, a')).toHaveLength(3);
  });

  it('updates filter labels from the server-health payload', () => {
    renderServerHealthSummary({
      counts: { total: 3, healthy: 1, warning: 1, critical: 1, offline: 0 },
      filters: { all: 3, problem: 2, healthy: 1 },
    });

    expect(document.querySelector('[data-server-filter="all"]').textContent).toBe('All (3)');
    expect(document.querySelector('[data-server-filter="problem"]').textContent).toBe('Problems (2)');
    expect(document.querySelector('[data-server-filter="healthy"]').textContent).toBe('Healthy (1)');
  });
});
