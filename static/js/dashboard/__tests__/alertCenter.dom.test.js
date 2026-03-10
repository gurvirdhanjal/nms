import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../modals/serverDetailModal.js', () => ({
  openServerModal: vi.fn(),
}));

import { initAlertCenter, renderAlertCenter } from '../alerts/alertCenter.js';


function buildAlert(index, overrides = {}) {
  return {
    id: `alert-${index}`,
    device_id: index,
    device_ip: `172.16.1.${index}`,
    device_name: `Device-${index}`,
    device_type: index % 2 === 0 ? 'server' : 'switch',
    severity: index % 3 === 0 ? 'CRITICAL' : index % 3 === 1 ? 'WARNING' : 'INFO',
    message: `Alert message ${index}`,
    timestamp: `2026-03-09T0${index % 10}:00:00Z`,
    ...overrides,
  };
}


async function flushTimers() {
  await vi.runAllTimersAsync();
}


function renderShell() {
  document.body.innerHTML = `
    <div id="filter-severity-container" class="dropdown">
      <button class="dropdown-toggle" data-value="all">All Severities</button>
      <ul class="dropdown-menu">
        <li><a class="dropdown-item" href="#" data-value="all">All Severities</a></li>
        <li><a class="dropdown-item" href="#" data-value="Critical">Critical</a></li>
        <li><a class="dropdown-item" href="#" data-value="Warning">Warning</a></li>
        <li><a class="dropdown-item" href="#" data-value="Informational">Informational</a></li>
      </ul>
    </div>
    <div id="filter-scope-container" class="dropdown">
      <button class="dropdown-toggle" data-value="all">All Types</button>
      <ul class="dropdown-menu">
        <li><a class="dropdown-item" href="#" data-value="all">All Types</a></li>
        <li><a class="dropdown-item" href="#" data-value="Network">Network</a></li>
        <li><a class="dropdown-item" href="#" data-value="Device">Device</a></li>
        <li><a class="dropdown-item" href="#" data-value="Server">Server</a></li>
      </ul>
    </div>
    <div id="filter-device-type-container" class="dropdown">
      <button class="dropdown-toggle" data-value="all">All Device Types</button>
      <ul class="dropdown-menu">
        <li><a class="dropdown-item" href="#" data-value="all">All Device Types</a></li>
      </ul>
    </div>
    <input id="filter-alert-device" />
    <div id="val-alerts-total"></div>
    <div id="val-alerts-critical"></div>
    <div id="val-alerts-warning"></div>
    <div id="val-alerts-info"></div>
    <div id="sub-alerts-scope"></div>
    <table><tbody id="table-alerts-body"></tbody></table>
  `;
}


describe('alert center renderer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    renderShell();
    window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
    window.cancelAnimationFrame = (id) => clearTimeout(id);
    window.requestIdleCallback = (cb) => setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 16 }), 0);
    initAlertCenter();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
  });

  it('renders summary totals and progressively paints large alert lists', async () => {
    const alerts = Array.from({ length: 95 }, (_, index) => buildAlert(index + 1));

    renderAlertCenter(alerts);
    await flushTimers();

    expect(document.getElementById('val-alerts-total').textContent).toBe('95');
    expect(document.querySelectorAll('#table-alerts-body tr')).toHaveLength(95);
    expect(document.getElementById('sub-alerts-scope').textContent).toContain('Server');
  });

  it('debounces text filters and narrows the rendered rows', async () => {
    renderAlertCenter([
      buildAlert(1, { device_name: 'Core-DB', message: 'Disk pressure', severity: 'CRITICAL' }),
      buildAlert(2, { device_name: 'Edge-Switch', message: 'Port flap', severity: 'WARNING' }),
      buildAlert(3, { device_name: 'App-Node', message: 'Recovered', severity: 'INFO' }),
    ]);
    await flushTimers();

    const input = document.getElementById('filter-alert-device');
    input.value = 'core-db';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await vi.advanceTimersByTimeAsync(140);
    await flushTimers();

    const rows = document.querySelectorAll('#table-alerts-body tr');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('Core-DB');
  });
});
