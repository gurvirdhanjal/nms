import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('premium tab adoption', () => {
  it('wires the dashboard breakdown surface to the shared premium tab helper', () => {
    const template = fs.readFileSync(path.resolve('templates/dashboard.html'), 'utf8');
    const script = fs.readFileSync(path.resolve('static/js/dashboard/dashboard.js'), 'utf8');

    expect(template).toContain('data-premium-tabs-root="device-breakdown"');
    expect(template).toContain('data-premium-tabs');
    expect(template).toContain('data-premium-tab');
    expect(template).toContain('data-premium-panel');
    expect(script).toContain("window.UI?.PremiumTabs");
    expect(script).toContain("data-premium-tabs-root=\"device-breakdown\"");
  });

  it('wires the server dashboard tabs to the shared premium tab helper', () => {
    const template = fs.readFileSync(path.resolve('templates/server_dashboard.html'), 'utf8');
    const script = fs.readFileSync(path.resolve('static/js/dashboard/servers/serverDashboard.js'), 'utf8');

    expect(template).toContain('data-premium-tabs-root="server-dashboard"');
    expect(template).toContain('data-premium-panels-host');
    expect(template).toContain('data-premium-panel');
    expect(script).toContain("window.UI?.PremiumTabs");
    expect(script).toContain("panelSelector: '[data-premium-panel]'");
  });
});
