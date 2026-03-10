import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';


describe('device-live remote view modal', () => {
  it('uses adaptive single-frame snapshots and modal fullscreen expansion', () => {
    const templatePath = path.resolve('templates/tracking/device_live.html');
    const scriptPath = path.resolve('static/js/tracking/device_live.js');

    const html = fs.readFileSync(templatePath, 'utf8');
    const script = fs.readFileSync(scriptPath, 'utf8');

    expect(html).toContain('class="modal fade remote-view-modal"');
    expect(html).toContain('id="remoteViewStatus"');
    expect(script).toContain('?single=1');
    expect(script).toContain('modal-fullscreen');
    expect(script).toContain("hidden.bs.modal");
  });
});
