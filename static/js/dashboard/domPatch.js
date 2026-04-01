/**
 * Keyed DOM patch helpers for high-frequency table/list updates.
 * Avoids full tbody rebuild and updates only changed rows.
 */

function normalizeText(value) {
    if (value === null || value === undefined) return '';
    return String(value);
}

function removeUnknownChildren(container) {
    Array.from(container.children).forEach((child) => {
        if (!(child instanceof HTMLTableRowElement)) {
            child.remove();
            return;
        }
        if (!child.dataset.rowKey && !child.dataset.placeholderRow) {
            child.remove();
        }
    });
}

export function setTableMessageRow(tbody, colSpan, message, className = 'text-center text-secondary p-3') {
    if (!tbody) return;

    const nextMessage = normalizeText(message);
    const existing = tbody.querySelector('tr[data-placeholder-row="1"]');

    if (
        existing &&
        tbody.children.length === 1 &&
        existing.dataset.placeholderCols === String(colSpan) &&
        existing.dataset.placeholderMessage === nextMessage
    ) {
        return;
    }

    tbody.textContent = '';
    const row = document.createElement('tr');
    row.dataset.placeholderRow = '1';
    row.dataset.placeholderCols = String(colSpan);
    row.dataset.placeholderMessage = nextMessage;

    const cell = document.createElement('td');
    cell.colSpan = colSpan;
    cell.className = className;
    cell.innerHTML = nextMessage;

    row.appendChild(cell);
    tbody.appendChild(row);
}

/**
 * Patch a table body by keyed rows.
 *
 * @param {HTMLTableSectionElement} tbody
 * @param {Array} items
 * @param {Object} options
 * @param {Function} options.getKey - (item, index) => stable key
 * @param {Function} options.renderCells - (item, index) => '<td>...</td>' HTML
 * @param {Function} [options.applyRow] - (row, item, index) => void for classes/dataset/listeners
 * @param {number} [options.emptyColSpan]
 * @param {string} [options.emptyMessage]
 * @param {string} [options.emptyClassName]
 */
export function patchKeyedTableRows(tbody, items, options) {
    if (!tbody) return;

    const rows = Array.isArray(items) ? items : [];
    const {
        getKey,
        renderCells,
        applyRow,
        emptyColSpan,
        emptyMessage,
        emptyClassName
    } = options || {};

    if (!rows.length) {
        if (typeof emptyColSpan === 'number' && typeof emptyMessage === 'string') {
            setTableMessageRow(tbody, emptyColSpan, emptyMessage, emptyClassName || 'text-center text-secondary p-3');
        } else {
            tbody.textContent = '';
        }
        return;
    }

    removeUnknownChildren(tbody);
    Array.from(tbody.querySelectorAll('tr[data-placeholder-row="1"]')).forEach((row) => row.remove());

    const existing = new Map();
    Array.from(tbody.querySelectorAll('tr[data-row-key]')).forEach((row) => {
        existing.set(row.dataset.rowKey, row);
    });

    const desiredRows = [];

    rows.forEach((item, index) => {
        const rawKey = getKey ? getKey(item, index) : index;
        const key = normalizeText(rawKey || index);

        let row = existing.get(key);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.rowKey = key;
        } else {
            existing.delete(key);
        }

        const nextCells = renderCells ? renderCells(item, index) : '';
        if (row.innerHTML !== nextCells) {
            row.innerHTML = nextCells;
        }

        if (applyRow) {
            applyRow(row, item, index);
        }

        delete row.dataset.placeholderRow;
        desiredRows.push(row);
    });

    existing.forEach((staleRow) => staleRow.remove());

    // Insert rows in order using insertBefore; only moves rows that are out of position.
    desiredRows.forEach((row, index) => {
        const currentAtIndex = tbody.children[index];
        if (currentAtIndex !== row) {
            tbody.insertBefore(row, currentAtIndex || null);
        }
    });
}
