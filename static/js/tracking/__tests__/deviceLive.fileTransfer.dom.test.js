import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';


describe('device-live file transfer workspace', () => {
  it('renders a workstation files tab and upload workspace in the template', () => {
    const templatePath = path.resolve('templates/tracking/device_live.html');
    const html = fs.readFileSync(templatePath, 'utf8');

    expect(html).toContain('data-tab="files"');
    expect(html).toContain('data-panel="files"');
    expect(html).toContain('id="filesUploadInput"');
    expect(html).toContain('id="filesUploadDropzone"');
    expect(html).toContain('id="filesList"');
  });

  it('binds device-scoped file transfer endpoints in the live console script', () => {
    const scriptPath = path.resolve('static/js/tracking/device_live.js');
    const script = fs.readFileSync(scriptPath, 'utf8');

    expect(script).toContain('/api/tracking/devices/${encodeURIComponent(deviceId)}/files/');
    expect(script).toContain("getFilesApiUrl('upload')");
    expect(script).toContain("getFilesApiUrl('download')");
    expect(script).toContain('handleFilesUploadSelection');
    expect(script).toContain('renderWorkstationFiles');
  });
});
