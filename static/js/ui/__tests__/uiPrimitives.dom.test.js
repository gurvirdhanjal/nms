// @vitest-environment jsdom

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

function loadScript(relativePath) {
  const script = readFileSync(resolve(process.cwd(), relativePath), 'utf8');
  window.eval(script);
}

describe('ui primitives', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    window.UI = {};
    vi.useRealTimers();
  });

  it('initializes toast helper and dedupes repeated messages', () => {
    loadScript('static/js/ui/toast.js');

    const first = window.UI.Toast.show('Device saved', 'success');
    const second = window.UI.Toast.show('Device saved', 'success');

    expect(first).toBeTruthy();
    expect(second).toBe(first);
    expect(document.querySelectorAll('.ui-toast')).toHaveLength(1);
  });

  it('applies and clears button busy state through the loading helper', () => {
    loadScript('static/js/ui/loading.js');

    const button = document.createElement('button');
    button.innerHTML = 'Refresh';
    document.body.appendChild(button);

    window.UI.Loading.setButtonBusy(button, {
      busy: true,
      labelBusy: 'Refreshing...',
    });

    expect(button.disabled).toBe(true);
    expect(button.classList.contains('ui-btn-busy')).toBe(true);
    expect(button.textContent).toContain('Refreshing...');

    window.UI.Loading.setButtonBusy(button, {
      busy: false,
      labelIdle: 'Refresh',
    });

    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain('Refresh');
  });

  it('ignores stale refresh results and applies only the latest payload', async () => {
    loadScript('static/js/ui/refresh.js');

    let resolverA;
    let resolverB;
    const applied = [];
    const controller = window.UI.Refresh.createController({
      fetcher: ({ reason }) =>
        new Promise((resolvePromise) => {
          if (reason === 'first') {
            resolverA = resolvePromise;
          } else {
            resolverB = resolvePromise;
          }
        }),
      applyData: (payload) => {
        applied.push(payload);
      },
    });

    const firstRun = controller.refresh({ reason: 'first' });
    await Promise.resolve();
    const secondRun = controller.refresh({ reason: 'second', manual: true });
    await Promise.resolve();

    resolverA({ id: 'old' });
    resolverB({ id: 'new' });

    await Promise.all([firstRun, secondRun]);

    expect(applied).toEqual([{ id: 'new' }]);
    expect(controller.getMeta().hasHydratedOnce).toBe(true);
  });
});
