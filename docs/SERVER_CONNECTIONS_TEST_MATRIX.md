# Server Connections Test Matrix

## API + UI Regression Cases

1. Server device returns `200` JSON and renders agent snapshot rows.
2. Server with no agent snapshot yet returns `200` JSON with empty rows and readable empty state.
3. Non-server device returns `400` JSON.
4. Missing device returns `404` JSON (no HTML response body).
5. Session-expired request returns `401` JSON and frontend does not throw JSON parse errors.
6. Agent snapshot values match between modal and detail page for the same device.
7. Device deleted while modal is open returns `404` JSON and modal handles it gracefully.
8. Connection response is summarized to top 20 remote IPs with hostname and connection count.

## Protection Cases

1. `meta.live_method` is `agent_snapshot`, with `snapshot_available` and `snapshot_age_seconds`.
2. `meta.total_connections` and `meta.total_unique_remote_ips` stay consistent with snapshot payload.
3. Backend failures return JSON error object (`error.code`, `error.message`) rather than HTML.
