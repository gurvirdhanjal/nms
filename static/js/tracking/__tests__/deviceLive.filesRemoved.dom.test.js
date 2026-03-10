import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';


describe('device-live files UI removal', () => {
  it('does not render Files tab or files panel in template', () => {
    const templatePath = path.resolve('templates/tracking/device_live.html');
    const html = fs.readFileSync(templatePath, 'utf8');

    expect(html).not.toContain('data-tab="files"');
    expect(html).not.toContain('data-panel="files"');
    expect(html).not.toContain('filesUploadBtn');
    expect(html).not.toContain('filesDownloadBtn');
    expect(html).not.toContain('fileUploadModal');
  });

  it('does not bind device-live file-transfer handlers', () => {
    const scriptPath = path.resolve('static/js/tracking/device_live.js');
    const script = fs.readFileSync(scriptPath, 'utf8');

    expect(script).not.toContain('/api/files/client/upload');
    expect(script).not.toContain('/api/files/client/download');
    expect(script).not.toContain('loadFilesPanelData');
    expect(script).not.toContain('ensureFileClientConnection');
  });
});
