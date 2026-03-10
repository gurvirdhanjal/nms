import { describe, expect, it } from 'vitest';

import { buildMarkerDatasets, calculateSeriesStats } from '../servers/serverMetricsView.js';


describe('serverMetricsView utilities', () => {
  it('calculates current/min/avg/max stats', () => {
    const stats = calculateSeriesStats([10, null, 5, 20, 15]);
    expect(stats.current).toBe(15);
    expect(stats.min).toBe(5);
    expect(stats.minIndex).toBe(2);
    expect(stats.max).toBe(20);
    expect(stats.maxIndex).toBe(3);
    expect(stats.avg).toBe(12.5);
  });

  it('builds min and max marker datasets', () => {
    const datasets = buildMarkerDatasets({
      labels: ['a', 'b', 'c', 'd'],
      values: [10, 5, 15, 12],
      baseLabel: 'CPU',
      color: '#00ff00',
    });

    expect(datasets).toHaveLength(2);
    expect(datasets[0].label).toContain('Min');
    expect(datasets[1].label).toContain('Max');
    expect(datasets[0].data[1]).toBe(5);
    expect(datasets[1].data[2]).toBe(15);
  });
});
