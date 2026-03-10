# Scaling Guidelines

## Goal

Scale ingestion and monitoring safely without duplicate work, lock storms, or unstable UI behavior.

## Queue and Worker Model

1. Scheduler should enqueue work, not perform network I/O.
2. Workers execute network-bound polling/discovery tasks.
3. Use claim semantics to avoid duplicate execution across workers:
- `SELECT ... FOR UPDATE SKIP LOCKED` pattern in `workers/snmp_worker.py`
4. Reclaim stale running tasks to recover from worker crashes.

## Endpoint Scalability Rules

1. Bound expensive queries (time range, `LIMIT`, pagination).
2. Avoid loading full datasets into memory for list endpoints.
3. Keep serialization small; return only fields required by current UI.
4. Use caching for repeated report filters where safe.

## Monitoring and Metrics Growth

1. Use raw logs only for short windows.
2. Use hourly/daily rollups for larger windows.
3. Keep retention jobs idempotent and scheduled.
4. Keep report queries read-only.

## Concurrency Safety

1. Use lock-aware patterns for task queues.
2. Use app context boundaries correctly for background threads/workers.
3. Remove/cleanup DB session in long-running loops (`db.session.remove()`).
4. Handle stale/deleted rows gracefully in concurrent flows.

## Backpressure and Limits

1. Enforce report API caps (`MAX_REPORT_RANGE_DAYS`, row limits, rate limits).
2. Enforce export job concurrency caps.
3. Keep network discovery worker counts bounded.
4. Use timeouts for remote operations and external HTTP/SNMP calls.

## PR Checklist

1. Does this change increase request fan-out or DB load?
2. Is there a queue/worker boundary where needed?
3. Are retries/timeouts/backoff explicit?
4. Are limits/rate caps documented and enforced?
