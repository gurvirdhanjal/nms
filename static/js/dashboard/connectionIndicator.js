/**
 * Connection Status Indicator Component
 *
 * Displays dashboard data transport mode:
 * - SSE live stream (primary)
 * - SSE connecting
 * - Polling fallback
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

    const configs = {
        connected: {
            dotClass: 'indicator-dot--connected',
            text: 'Live Stream',
            title: 'Real-time updates via SSE'
        },
        sse_connected: {
            dotClass: 'indicator-dot--connected',
            text: 'Live Stream',
            title: 'Real-time updates via SSE'
        },
        connecting: {
            dotClass: 'indicator-dot--connecting',
            text: 'Stream Connecting',
            title: 'Establishing SSE connection'
        },
        sse_connecting: {
            dotClass: 'indicator-dot--connecting',
            text: 'Stream Connecting',
            title: 'Establishing SSE connection'
        },
        disconnected: {
            dotClass: 'indicator-dot--fallback',
            text: 'Polling Fallback (30s)',
            title: 'SSE unavailable, using polling fallback'
        },
        polling_fallback: {
            dotClass: 'indicator-dot--fallback',
            text: 'Polling Fallback (30s)',
            title: 'SSE unavailable, using polling fallback'
        },
        polling: {
            dotClass: 'indicator-dot--fallback',
            text: 'Polling (30s)',
            title: 'Dashboard updates every 30 seconds'
        }
    };
    const config = configs[status] || configs.polling;

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
