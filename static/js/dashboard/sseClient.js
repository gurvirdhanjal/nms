/**
 * SSE Client for Real-Time Dashboard Updates
 * 
 * Features:
 * - Automatic reconnection with exponential backoff
 * - Connection status tracking
 * - Event deduplication
 * - Heartbeat timeout detection
 * - Graceful fallback to polling
 */

// Connection states
export const ConnectionStatus = {
    CONNECTED: 'connected',
    CONNECTING: 'connecting',
    DISCONNECTED: 'disconnected'
};

// Configuration
const SSE_ENDPOINT = '/api/events/stream';
const INITIAL_RETRY_DELAY = 1000;  // 1 second
const MAX_RETRY_DELAY = 30000;     // 30 seconds
const HEARTBEAT_TIMEOUT = 45000;   // 45 seconds (server sends every 30s)
const EVENT_DEBOUNCE_MS = 100;     // Debounce rapid updates

// State
let eventSource = null;
let connectionStatus = ConnectionStatus.DISCONNECTED;
let retryDelay = INITIAL_RETRY_DELAY;
let retryTimeout = null;
let heartbeatTimeout = null;
let lastEventIds = new Set();
let eventBuffer = [];
let debounceTimer = null;

// Callbacks
let onConnectionChange = null;
let eventHandlers = {};

/**
 * Initialize SSE connection.
 * 
 * @param {Object} options Configuration options
 * @param {Function} options.onDeviceStatus Handler for device status changes
 * @param {Function} options.onAlertCreated Handler for new alerts
 * @param {Function} options.onLatencySpike Handler for latency spikes
 * @param {Function} options.onInterfaceThreshold Handler for interface threshold events
 * @param {Function} options.onConnectionChange Handler for connection status changes
 */
export function initSSE(options = {}) {
    // Store handlers
    eventHandlers = {
        device_status: options.onDeviceStatus,
        device_update: options.onDeviceUpdate || options.onDeviceStatus,
        alert_created: options.onAlertCreated,
        latency_spike: options.onLatencySpike,
        interface_threshold: options.onInterfaceThreshold,
        classification_update: options.onClassificationUpdate
    };
    onConnectionChange = options.onConnectionChange;

    // Start connection
    connect();
}

/**
 * Establish SSE connection.
 */
function connect() {
    // Clean up existing connection
    if (eventSource) {
        eventSource.close();
    }

    // Update status
    setConnectionStatus(ConnectionStatus.CONNECTING);
    console.log('[SSE] Connecting...');

    try {
        eventSource = new EventSource(SSE_ENDPOINT);

        // Connection opened
        eventSource.onopen = () => {
            console.log('[SSE] Connection established');
            setConnectionStatus(ConnectionStatus.CONNECTED);
            retryDelay = INITIAL_RETRY_DELAY;  // Reset backoff
            resetHeartbeatTimeout();
        };

        // Handle connection event from server
        eventSource.addEventListener('connected', (event) => {
            console.log('[SSE] Server confirmed connection:', event.data);
            resetHeartbeatTimeout();
        });

        // Handle heartbeat keep-alive
        eventSource.addEventListener('heartbeat', () => {
            resetHeartbeatTimeout();
        });

        // Handle device status events
        eventSource.addEventListener('device_status', (event) => {
            handleEvent('device_status', event);
        });

        // Legacy/compat event type used by device monitor worker
        eventSource.addEventListener('device_update', (event) => {
            handleEvent('device_update', event);
        });

        // Handle alert events
        eventSource.addEventListener('alert_created', (event) => {
            handleEvent('alert_created', event);
        });

        // Handle latency spike events
        eventSource.addEventListener('latency_spike', (event) => {
            handleEvent('latency_spike', event);
        });

        // Handle interface threshold events
        eventSource.addEventListener('interface_threshold', (event) => {
            handleEvent('interface_threshold', event);
        });

        // Handle classification updates
        eventSource.addEventListener('classification_update', (event) => {
            handleEvent('classification_update', event);
        });

        // Handle errors
        eventSource.onerror = (error) => {
            console.error('[SSE] Connection error:', error);
            handleDisconnect();
        };

    } catch (error) {
        console.error('[SSE] Failed to create EventSource:', error);
        handleDisconnect();
    }
}

/**
 * Handle incoming SSE event.
 */
function handleEvent(eventType, event) {
    resetHeartbeatTimeout();

    try {
        const data = JSON.parse(event.data);

        // Deduplicate events
        if (data.event_id && lastEventIds.has(data.event_id)) {
            console.log('[SSE] Duplicate event ignored:', data.event_id);
            return;
        }

        // Track event ID for deduplication
        if (data.event_id) {
            lastEventIds.add(data.event_id);
            // Keep last 100 event IDs
            if (lastEventIds.size > 100) {
                const firstId = lastEventIds.values().next().value;
                lastEventIds.delete(firstId);
            }
        }

        // Buffer event for debouncing
        eventBuffer.push({ eventType, data });
        scheduleEventFlush();

    } catch (error) {
        console.error('[SSE] Failed to parse event:', error, event.data);
    }
}

/**
 * Debounce and batch event processing.
 */
function scheduleEventFlush() {
    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }

    debounceTimer = setTimeout(() => {
        flushEventBuffer();
    }, EVENT_DEBOUNCE_MS);
}

/**
 * Process buffered events.
 */
function flushEventBuffer() {
    if (eventBuffer.length === 0) return;

    // Group events by type
    const eventsByType = {};
    eventBuffer.forEach(({ eventType, data }) => {
        if (!eventsByType[eventType]) {
            eventsByType[eventType] = [];
        }
        eventsByType[eventType].push(data);
    });

    // Call handlers for each event type
    Object.entries(eventsByType).forEach(([eventType, events]) => {
        const handler = eventHandlers[eventType];
        if (handler) {
            // For simplicity, call handler with the latest event
            // (or you could pass all events for batch processing)
            events.forEach(event => {
                try {
                    handler(event.payload || event);
                } catch (error) {
                    console.error(`[SSE] Handler error for ${eventType}:`, error);
                }
            });
        }
    });

    // Clear buffer
    eventBuffer = [];
}

/**
 * Handle disconnection and schedule reconnect.
 */
function handleDisconnect() {
    clearTimeout(retryTimeout);

    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    setConnectionStatus(ConnectionStatus.DISCONNECTED);
    clearTimeout(heartbeatTimeout);

    // Schedule reconnect with exponential backoff
    console.log(`[SSE] Reconnecting in ${retryDelay / 1000}s...`);

    retryTimeout = setTimeout(() => {
        connect();
    }, retryDelay);

    // Increase delay for next attempt (exponential backoff)
    retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY);
}

/**
 * Reset heartbeat timeout.
 */
function resetHeartbeatTimeout() {
    clearTimeout(heartbeatTimeout);

    heartbeatTimeout = setTimeout(() => {
        console.warn('[SSE] Heartbeat timeout - connection may be stale');
        handleDisconnect();
    }, HEARTBEAT_TIMEOUT);
}

/**
 * Update and notify connection status.
 */
function setConnectionStatus(status) {
    if (connectionStatus === status) return;

    connectionStatus = status;
    console.log(`[SSE] Status: ${status}`);

    if (onConnectionChange) {
        onConnectionChange(status);
    }
}

/**
 * Get current connection status.
 */
export function getConnectionStatus() {
    return connectionStatus;
}

/**
 * Manually disconnect SSE.
 */
export function disconnect() {
    clearTimeout(retryTimeout);
    clearTimeout(heartbeatTimeout);
    clearTimeout(debounceTimer);

    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    setConnectionStatus(ConnectionStatus.DISCONNECTED);
}

/**
 * Manually reconnect SSE.
 */
export function reconnect() {
    disconnect();
    retryDelay = INITIAL_RETRY_DELAY;
    connect();
}
