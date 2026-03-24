/**
 * Pure utility functions for the reports page.
 * Exported as ES module so they can be unit-tested with Vitest.
 * Exposed to window via <script type="module"> in reports.html.
 */

/**
 * Compute a human-readable status badge HTML string for a device row.
 * Prioritises anomaly_flag (threshold breach) over uptime classification.
 *
 * STATUS DECISION TREE:
 *
 *   r = null / undefined ───────────────────────────────► "Unknown"  (grey)
 *   r.anomaly_flag = true ──────────────────────────────► "Critical" (red)
 *   r.uptime_pct < 90 ──────────────────────────────────► "Warning"  (yellow)
 *   otherwise ──────────────────────────────────────────► "Healthy"  (green)
 *
 * WHY NOT sla_tier: SLA is a classification label (Gold/Silver/Bronze/Critical).
 * Status must reflect the actual live condition — anomaly_flag signals a real
 * threshold breach detected by _detect_anomaly(); uptime_pct < 90 signals
 * poor availability. These are orthogonal to the SLA bucket the device sits in.
 *
 * @param {object|null} r  Canonical device row (core_metrics_service shape)
 * @returns {string}  HTML badge string (safe — no user data interpolated)
 */
export function statusBadge(r) {
    if (!r) return '<span class="badge bg-secondary">Unknown</span>';
    if (r.anomaly_flag) return '<span class="badge bg-danger">Critical</span>';
    if (r.uptime_pct != null && r.uptime_pct < 90) {
        return '<span class="badge bg-warning text-dark">Warning</span>';
    }
    return '<span class="badge bg-success">Healthy</span>';
}
