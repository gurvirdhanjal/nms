/**
 * Utility functions for Dashboard
 */

// Format timestamp to "X mins ago"
export function timeAgo(dateString) {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return 'Invalid Date';

    const seconds = Math.floor((new Date() - date) / 1000);

    let interval = seconds / 31536000;
    if (interval > 1) return Math.floor(interval) + " years ago";

    interval = seconds / 2592000;
    if (interval > 1) return Math.floor(interval) + " months ago";

    interval = seconds / 86400;
    if (interval > 1) return Math.floor(interval) + " days ago";

    interval = seconds / 3600;
    if (interval > 1) return Math.floor(interval) + " hours ago";

    interval = seconds / 60;
    if (interval > 1) return Math.floor(interval) + " mins ago";

    return Math.floor(seconds) + " seconds ago";
}

// Format numbers (e.g. 1200 -> 1.2k)
export function formatNumber(num) {
    if (num === null || num === undefined) return '-';
    return new Intl.NumberFormat('en-US', { notation: "compact", compactDisplay: "short" }).format(num);
}

// Format percentage
export function formatPercent(num) {
    if (num === null || num === undefined) return '-';
    return `${Number(num).toFixed(1)}%`;
}

// Add Stale indicator to a card
export function checkStale(lastUpdatedStr, elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const diffMs = new Date() - new Date(lastUpdatedStr);
    const fiveMinutes = 5 * 60 * 1000;

    if (diffMs > fiveMinutes) {
        el.classList.add('card-stale');
        el.setAttribute('title', `Data stale. Last updated: ${timeAgo(lastUpdatedStr)}`);
    } else {
        el.classList.remove('card-stale');
        el.removeAttribute('title');
    }
}

/**
 * Animate a numeric value change
 * @param {HTMLElement} element - The element to update
 * @param {number} start - Starting value
 * @param {number} end - Ending value
 * @param {number} duration - Animation duration in ms
 */
export function animateValue(element, start, end, duration = 500) {
    if (!element) return;
    // Ensure numbers
    start = parseInt(start) || 0;
    end = parseInt(end) || 0;

    if (start === end) {
        element.textContent = end;
        return;
    }

    let startTimestamp = null;
    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);

        // Easing function (easeOutQuad)
        const easeProgress = 1 - (1 - progress) * (1 - progress);

        const current = Math.floor(easeProgress * (end - start) + start);
        element.textContent = current;

        if (progress < 1) {
            window.requestAnimationFrame(step);
        } else {
            element.textContent = end;
        }
    };
    window.requestAnimationFrame(step);
}

/**
 * Setup a tactical dropdown (Bootstrap structure acting as select)
 * @param {string} containerId - ID of the container .dropdown
 * @param {function} onChange - Callback (value) => {}
 * @param {Array} initialOptions - (Optional) [{value, label}, ...]
 * @returns {Object} - { getValue, updateOptions, setValue }
 */
export function setupTacticalDropdown(containerId, onChange, initialOptions = []) {
    const container = document.getElementById(containerId);
    if (!container) return null;

    const btn = container.querySelector('.dropdown-toggle');
    const menu = container.querySelector('.dropdown-menu');
    if (!btn || !menu) return null;

    let currentValue = btn.dataset.value || initialOptions[0]?.value || 'all';

    // Helper to render options
    const renderOptions = (options) => {
        menu.innerHTML = options.map(opt =>
            `<li><a class="dropdown-item" href="#" data-value="${opt.value}">${opt.label}</a></li>`
        ).join('');
        attachListeners();
    };

    // Helper to update state
    const setValue = (val, label) => {
        currentValue = val;
        btn.textContent = label;
        btn.dataset.value = val;
        // Visual selection state
        menu.querySelectorAll('.dropdown-item').forEach(item => {
            if (item.dataset.value === val) item.classList.add('active');
            else item.classList.remove('active');
        });
    };

    const attachListeners = () => {
        menu.querySelectorAll('.dropdown-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const newVal = item.dataset.value;
                const newLabel = item.textContent;
                setValue(newVal, newLabel);
                if (onChange) onChange(newVal);
            });
        });
    };

    // Initial render if options provided
    if (initialOptions && initialOptions.length > 0) {
        renderOptions(initialOptions);
        // Set initial if matches
        const initial = initialOptions.find(o => o.value === currentValue);
        if (initial) setValue(initial.value, initial.label);
    } else {
        // Just attach to existing static options
        attachListeners();
        // Set initial value from DOM if present
        const activeItem = menu.querySelector('.dropdown-item.active') || menu.querySelector('.dropdown-item');
        if (activeItem && !btn.dataset.value) {
            setValue(activeItem.dataset.value, activeItem.textContent);
        }
    }

    return {
        getValue: () => currentValue,
        setValue: (val) => {
            // Find label
            const item = Array.from(menu.querySelectorAll('.dropdown-item')).find(i => i.dataset.value === val);
            if (item) setValue(val, item.textContent);
        },
        updateOptions: (newOptions) => {
            renderOptions(newOptions);
            // If current value no longer exists, reset to first or all
            const exists = newOptions.find(o => o.value === currentValue);
            if (!exists && newOptions.length > 0) {
                setValue(newOptions[0].value, newOptions[0].label);
            }
        }
    };
}
