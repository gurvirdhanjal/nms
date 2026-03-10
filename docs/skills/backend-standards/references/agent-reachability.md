# Agent Reachability Patterns

## Goal
Improve system resilience and diagnostic clarity when agents are intermittently unreachable or down.

## Pattern: Connect First, Short-Circuit Second

1.  **Direct Probing**: Attempt the primary agent identity endpoint first.
2.  **Immediate Short-Circuit**: If the primary endpoint fails with a `Connection Refused` or `Timeout`, skip all subsequent sub-probes (stats, health, logs) for that host in the current scan cycle.
3.  **Ping Fallback**:
    -   On HTTP failure, immediately perform an ICMP PING.
    -   **If PING succeeds**: Classify as `agent_missing_on_host`. This indicates the host is alive but the service is down or blocked.
    -   **If PING fails**: Classify as `offline`. This indicates the entire host is likely down.

## Benefits
-   **Reduced Latency**: No waiting for multiple timeouts on a dead connection.
-   **Reduced log noise**: Suppress cascading error logs from secondary probes.
-   **Diagnostic Accuracy**: Distinguishes between "App Broken" and "Network Down".

## Implementation Reference
See `routes/tracking.py` -> `check_tracking_service` and `scripts/tracking_worker.py`.
