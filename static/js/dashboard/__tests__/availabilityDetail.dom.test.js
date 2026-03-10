import { describe, expect, it } from 'vitest';

import {
  buildAvailabilitySummary,
  getAvailabilityClass,
  renderAvailabilityHeatmap,
  renderAvailabilityRows,
} from '../modals/availabilityDetail.js';


describe('availability detail modal renderer', () => {
  it('builds compact summary data from observed buckets', () => {
    const summary = buildAvailabilitySummary([
      { time: '2026-03-07T00:00:00Z', value: 100, online: 10, total: 10 },
      { time: '2026-03-07T01:00:00Z', value: 96, online: 24, total: 25 },
      { time: '2026-03-07T02:00:00Z', value: 88, online: 22, total: 25 },
      { time: '2026-03-07T03:00:00Z', value: 0, online: 0, total: 0 },
    ]);

    expect(summary.observedCount).toBe(3);
    expect(summary.criticalCount).toBe(1);
    expect(summary.stableCount).toBe(1);
    expect(summary.averagePct).toBeCloseTo(94.67, 1);
    expect(summary.worst.value).toBe(88);
  });

  it('renders dense heatmap cells, summary chips, and compact table rows', () => {
    document.body.innerHTML = `
      <div id="summary"></div>
      <div id="axis"></div>
      <div id="heatmap"></div>
      <table><tbody id="downtime"></tbody></table>
      <table><tbody id="worst"></tbody></table>
    `;

    renderAvailabilityHeatmap(
      [
        { time: '2026-03-07T00:00:00Z', value: 100, online: 10, total: 10 },
        { time: '2026-03-07T01:00:00Z', value: 96, online: 24, total: 25 },
        { time: '2026-03-07T02:00:00Z', value: 92, online: 23, total: 25 },
        { time: '2026-03-07T03:00:00Z', value: 81, online: 20, total: 25 },
        { time: '2026-03-07T04:00:00Z', value: 0, online: 0, total: 0 },
      ],
      document.getElementById('heatmap'),
      {
        axisEl: document.getElementById('axis'),
        summaryEl: document.getElementById('summary'),
      },
    );

    renderAvailabilityRows(
      [
        {
          device_name: 'Device-172.16.1.226',
          device_type: 'switch',
          ip: '172.16.1.226',
          offline_scans: 66,
          downtime_pct: 88,
        },
      ],
      document.getElementById('downtime'),
      'downtime',
    );

    renderAvailabilityRows(
      [
        {
          device_name: 'Device-172.16.2.234',
          device_type: 'server',
          ip: '172.16.2.234',
          offline_scans: 62,
          uptime_pct: 0,
        },
      ],
      document.getElementById('worst'),
      'worst',
    );

    const cells = document.querySelectorAll('#heatmap .availability-cell');
    expect(cells).toHaveLength(5);
    expect(cells[0].className).toContain(getAvailabilityClass(100));
    expect(cells[1].className).toContain(getAvailabilityClass(96));
    expect(cells[2].className).toContain(getAvailabilityClass(92));
    expect(cells[3].className).toContain(getAvailabilityClass(81));
    expect(cells[4].className).toContain('avail-unknown');

    expect(document.querySelectorAll('#summary .availability-summary-kpi')).toHaveLength(4);
    expect(document.querySelectorAll('#axis .availability-axis-label')).toHaveLength(5);

    const downtimeRow = document.querySelector('#downtime tr');
    expect(downtimeRow.textContent).toContain('Device-172.16.1.226');
    expect(downtimeRow.querySelector('.availability-inline-pill.tone-bad')).not.toBeNull();

    const worstRow = document.querySelector('#worst tr');
    expect(worstRow.textContent).toContain('Device-172.16.2.234');
    expect(worstRow.querySelector('.availability-inline-pill.tone-bad')).not.toBeNull();
  });
});
