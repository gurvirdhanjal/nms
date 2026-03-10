/**
 * API Wrapper for Dashboard
 */

// Simple fetch wrapper with error handling
async function fetchAPI(endpoint) {
    try {
        const response = await fetch(endpoint, { credentials: 'same-origin' });
        if (!response.ok) {
            throw new Error(`API Error: ${response.status} ${response.statusText}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`Failed to fetch ${endpoint}:`, error);
        throw error;
    }
}

export async function fetchFullSnapshot({
    range = '24h',
    forceFreshTopProblems = false,
    alertsStatus = 'active',
    alertsLimit = 200
} = {}) {
    const params = new URLSearchParams({
        range: String(range || '24h'),
        status: String(alertsStatus || 'active'),
        limit: String(alertsLimit || 200)
    });
    if (forceFreshTopProblems) {
        params.set('fresh', '1');
    }
    return fetchAPI(`/api/dashboard/full_snapshot?${params.toString()}`);
}

export async function fetchSummary() {
    return fetchAPI('/api/dashboard/summary');
}

export async function fetchTopProblems(forceFresh = false) {
    const suffix = forceFresh ? '?fresh=1' : '';
    return fetchAPI(`/api/dashboard/top-problems${suffix}`);
}

export async function fetchTrends(range = '24h') {
    return fetchAPI(`/api/dashboard/trends?range=${range}`);
}

export async function fetchAlerts(status = 'active', limit = 100) {
    return fetchAPI(`/api/dashboard/alerts?status=${status}&limit=${limit}`);
}

export async function fetchInventory() {
    return fetchAPI('/api/dashboard/inventory');
}

export async function fetchRealTimeInterfaces() {
    return fetchAPI('/api/dashboard/realtime/interfaces');
}

export async function fetchRealTimeIO() {
    return fetchAPI('/api/dashboard/realtime/network-io');
}

export async function fetchServerHealth() {
    return fetchAPI('/api/server/health');
}

export async function fetchFleetMetrics() {
    return fetchAPI('/api/server/fleet-metrics');
}

export async function fetchAvailabilityDetails(range = '24h', forceFresh = false) {
    const params = new URLSearchParams({
        range: String(range || '24h')
    });
    if (forceFresh) {
        params.set('fresh', '1');
    }
    return fetchAPI(`/api/dashboard/availability-details?${params.toString()}`);
}

export async function fetchSubnetDetails(subnet, limit = 500) {
    const params = new URLSearchParams({
        subnet: String(subnet || ''),
        limit: String(limit || 500)
    });
    return fetchAPI(`/api/dashboard/subnet-details?${params.toString()}`);
}
