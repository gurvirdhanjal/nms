function normalizeRole(value) {
    return String(value || '').trim().toLowerCase();
}

export function formatScopeSummary(context) {
    const role = normalizeRole(context && context.role);
    const label = String((context && context.scope_label) || '').trim();

    if (role === 'admin') {
        return 'Scope: Global';
    }
    if (role === 'manager') {
        return label ? `Scope: ${label}` : 'Scope: Site — Unassigned';
    }
    if (role === 'viewer' || role === 'operator' || role === 'user') {
        return label ? `Scope: ${label}` : 'Scope: Department — Unassigned';
    }
    return label ? `Scope: ${label}` : 'Scope: Global';
}

export function renderScopeSummary(element, context) {
    const text = formatScopeSummary(context);
    if (element) {
        element.textContent = text;
    }
    return text;
}
