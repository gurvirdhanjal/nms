import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('live fleet page contract', () => {
  it('renders telemetry bars and a working add-device action in the fleet template', () => {
    const templatePath = path.resolve('templates/tracking/live_tracking.html');
    const html = fs.readFileSync(templatePath, 'utf8');

    expect(html).toContain('fleet-cpu-bar');
    expect(html).toContain('fleet-mem-bar');
    expect(html).toContain('fleet-disk-bar');
    expect(html).toContain("url_for('tracking_bp.device_tracking', open_add_device=1)");
  });

  it('updates telemetry bars and supports add-device deep linking in the page scripts', () => {
    const liveFleetScript = fs.readFileSync(path.resolve('static/js/tracking/live_fleet.js'), 'utf8');
    const deviceTrackingScript = fs.readFileSync(path.resolve('static/js/tracking/device_tracking.js'), 'utf8');

    expect(liveFleetScript).toContain('function updateDeviceTelemetry(deviceId, cpu, mem, disk)');
    expect(liveFleetScript).toContain('updateDeviceTelemetry(row.dataset.deviceId, cpu, memory, disk);');
    expect(deviceTrackingScript).toContain('function maybeOpenAddDeviceModalFromQuery()');
    expect(deviceTrackingScript).toContain("params.get('open_add_device')");
  });
});
