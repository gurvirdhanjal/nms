import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../modals/serverDetailModal.js', () => ({
  openServerModal: vi.fn(),
}));

import { renderEnhancedServerTable, setServerHealthFilter } from '../servers/serverHealth.js';


describe('fleet overview server table', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-07T18:00:00Z'));
    setServerHealthFilter('all');
    document.body.innerHTML = `
      <table>
        <tbody id="table-server-health-body"></tbody>
      </table>
    `;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders ten cells so xl headers stay aligned with row content', () => {
    renderEnhancedServerTable({
      servers: [
        {
          device_id: 42,
          device_name: 'apldeveloper',
          ip: '172.16.1.70',
          health: 'Critical',
          cpu_usage: 19.7,
          memory_usage: 90.6,
          disk_usage: 14.7,
          latency: 8,
          packet_loss: 0,
          jitter: 0,
          last_seen: '2026-03-07T17:59:52Z',
        },
      ],
    });

    const row = document.querySelector('#table-server-health-body tr');
    expect(row.children).toHaveLength(10);
    expect(row.children[5].className).toContain('d-none');
    expect(row.children[6].className).toContain('d-none');
    expect(row.children[7].className).toContain('d-none');
    expect(row.children[9].className).toContain('text-end');
    expect(row.children[9].textContent).toContain('View');
  });

  it('uses the full table width for the empty state row', () => {
    renderEnhancedServerTable({ servers: [] });

    const emptyCell = document.querySelector('#table-server-health-body td');
    expect(emptyCell.getAttribute('colspan')).toBe('10');
    expect(emptyCell.textContent).toContain('No servers match filter');
  });
});
