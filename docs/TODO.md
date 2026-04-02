# TODO — Deferred Performance Work (Option C)

These items were identified during the 2026-04-02 SSE + Performance brainstorm session
but deferred from Option B scope. Pick these up as a dedicated performance sprint once
Option B is shipped.

---

## Background Insert Queue for Scan History

**What:** Decouple `DeviceScanHistory` writes from the scan cycle entirely. Instead of
writing scan records inline (even with the batched commit from Option B), push them to
an in-memory queue and drain it in a separate background thread.

**Why:** The scan cycle's job is to probe devices and evaluate alerts. Writing history is
a side-effect — it shouldn't block the next scan from starting.

**Complexity:** Medium. Need a thread-safe queue, a drain worker, and graceful shutdown.

---

## Dashboard JS — Lazy-Load Charts

**What:** Charts on the dashboard (Chart.js) are initialized and data-fetched even for
tabs the user never opens. Use `IntersectionObserver` to defer chart init until the
container scrolls into view.

**Why:** Reduces initial page load time and unnecessary API calls on load.

**Complexity:** Low–Medium. Pure frontend change.

---

## Dashboard JS — Debounce Rapid SSE Updates

**What:** When a scan cycle completes, `device_update_batch` may push 20–50 device
updates in quick succession. The dashboard re-renders on each one. Add a 200ms debounce
so the DOM is updated once after the burst settles.

**Why:** Prevents visual jitter and reduces layout thrash during scan cycles.

**Complexity:** Low. A few lines in `sseClient.js` / dashboard JS.

---

## EXPLAIN ANALYZE Audit — 5 Slowest Report Queries

**What:** Run `EXPLAIN (ANALYZE, BUFFERS)` on the 5 slowest report queries in production.
Candidates: executive summary, device health (90-day), alert synthesis, SLA report,
security compliance report.

**Why:** TimescaleDB aggregate routing (Option B) addresses known routing gaps, but there
may be additional seq-scans or missing indexes only visible under real data.

**Complexity:** Low to identify, variable to fix.

---

## SSE Progress Events for Async Export Jobs

**What:** The `report_export_job` model and async export flow already exist. Wire SSE
`export_progress` events so the UI shows a real-time progress bar instead of polling
`GET /api/reports/export-status/<job_id>` every 2 seconds.

**Why:** Reduces polling overhead and improves perceived performance on large exports.

**Complexity:** Low. `broadcast_event('export_progress', {...})` in `services/export_service.py`
at key milestones (query complete, formatting, writing file).

---

## Rate Limiting on Write Endpoints

**What:** Add Flask-Limiter rate limits to `bulk_add`, `bulk_delete`, `scan_network`,
`save_user`. Currently deferred from Sprint 2.

**Why:** Protection against accidental or malicious bulk operations.

**Complexity:** Low.

---

## CSRF Protection

**What:** Install Flask-WTF, add `{{ csrf_token() }}` meta tag to base template, wire
JS to send `X-CSRFToken` header on all POST/PUT/DELETE/PATCH requests.

**Why:** All POST routes currently unprotected. Noted as open gap since Sprint 2.

**Complexity:** Low–Medium.
