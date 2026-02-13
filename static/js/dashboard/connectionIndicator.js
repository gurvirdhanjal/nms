/**
 * Connection Status Indicator Component
 *
 * Displays polling status with visual feedback:
 * - Green dot + "Polling (30s)"
 */

/**
 * Render the connection status indicator.
 *
 * @param {string} status Current connection status
 */
export function renderConnectionIndicator(status = 'polling') {
    const container = document.getElementById('connection-indicator');
    if (!container) {
        console.warn('[ConnectionIndicator] Container #connection-indicator not found');
        return;
    }

    const config = {
        dotClass: 'indicator-dot--connected',
        text: 'Polling (30s)',
        title: 'Dashboard updates every 30 seconds'
    };

    container.innerHTML = `
        <div class="connection-indicator" title="${config.title}">
            <span class="indicator-dot ${config.dotClass}"></span>
            <span class="indicator-text">${config.text}</span>
        </div>
    `;
}

/**
 * Initialize the connection indicator with polling state.
 */
export function initConnectionIndicator() {
    renderConnectionIndicator('polling');
}
