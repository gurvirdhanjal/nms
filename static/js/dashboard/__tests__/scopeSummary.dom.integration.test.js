import { describe, expect, it } from 'vitest';

import { renderScopeSummary } from '../scopeSummary.js';


describe('scope summary DOM integration', () => {
  it('renders expected scope line per role', () => {
    document.body.innerHTML = '<div id="scopeLine"></div>';
    const el = document.getElementById('scopeLine');

    renderScopeSummary(el, { role: 'admin', scope_label: 'Global' });
    expect(el.textContent).toBe('Scope: Global');

    renderScopeSummary(el, { role: 'manager', scope_label: 'Site — Alpha Site' });
    expect(el.textContent).toBe('Scope: Site — Alpha Site');

    renderScopeSummary(el, { role: 'operator', scope_label: 'Department — SOC' });
    expect(el.textContent).toBe('Scope: Department — SOC');
  });
});
