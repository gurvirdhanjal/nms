import { describe, expect, it, vi } from 'vitest';

vi.mock('../modals/serverDetailModal.js', () => ({
  initServerModal: vi.fn(),
  openServerModal: vi.fn(),
}));

vi.mock('../servers/serverHealth.js', () => ({
  initServerHealthTable: vi.fn(),
  renderEnhancedServerTable: vi.fn(),
  renderFleetOverview: vi.fn(),
  renderServerHealthSummary: vi.fn(),
  setServerHealthFilter: vi.fn(),
}));

import { buildCoverageSummary, sortAlerts } from '../servers/serverDashboard.js';

describe('server dashboard summary helpers', () => {
  it('builds reporting coverage from backend summary fields', () => {
    expect(buildCoverageSummary({
      scoped_total: 8,
      reporting_total: 6,
    })).toEqual({
      scopedTotal: 8,
      reportingTotal: 6,
      coveragePercent: 75,
      coverageLabel: '75%',
      countLabel: '6 of 8 scoped servers reporting',
    });
  });

  it('sorts alerts by severity before breadth of impact', () => {
    const sorted = sortAlerts([
      { server_name: 'Bravo', severity: 'warning', metrics: [{}, {}] },
      { server_name: 'Alpha', severity: 'critical', metrics: [{}] },
      { server_name: 'Charlie', severity: 'warning', metrics: [{}, {}, {}] },
    ]);

    expect(sorted.map((alert) => alert.server_name)).toEqual(['Alpha', 'Charlie', 'Bravo']);
  });
});
