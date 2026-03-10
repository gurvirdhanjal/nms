import { beforeEach, describe, expect, it } from 'vitest';

import { renderDeviceStatusCards } from '../cards/deviceStatus.js';


describe('dashboard device status cards', () => {
  beforeEach(() => {
    let rafTimestamp = 0;
    window.requestAnimationFrame = (callback) => {
      rafTimestamp += 600;
      callback(rafTimestamp);
      return rafTimestamp;
    };

    document.body.innerHTML = `
      <div id="card-devices-healthy"></div>
      <div id="card-devices-offline"></div>
      <div id="card-devices-maintenance"></div>
      <div id="val-devices-healthy">0</div>
      <div id="sub-devices-healthy"></div>
      <div id="val-devices-offline">0</div>
      <div id="sub-devices-offline"></div>
      <div id="val-devices-maintenance">0</div>
    `;
  });

  it('shows reachable devices and degraded breakdown on the middle KPI card', () => {
    renderDeviceStatusCards(
      {
        devices: {
          total: 221,
          online: 166,
          healthy: 0,
          degraded: 166,
          offline: 55,
          maintenance: 1,
        },
      },
      new Date().toISOString(),
    );

    expect(document.getElementById('val-devices-healthy').textContent).toBe('166');
    expect(document.getElementById('sub-devices-healthy').textContent).toBe('0 healthy, 166 degraded');
  });

  it('keeps the healthy summary explicit when no degraded devices are present', () => {
    renderDeviceStatusCards(
      {
        devices: {
          total: 8,
          online: 8,
          healthy: 8,
          degraded: 0,
          offline: 0,
          maintenance: 0,
        },
      },
      new Date().toISOString(),
    );

    expect(document.getElementById('val-devices-healthy').textContent).toBe('8');
    expect(document.getElementById('sub-devices-healthy').textContent).toBe('All reachable devices are healthy');
  });
});
