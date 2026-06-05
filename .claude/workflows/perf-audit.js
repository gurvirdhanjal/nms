export const meta = {
  name: 'perf-audit',
  description: 'Audit NMS for performance and container-safety issues: memory leaks, unbounded concurrency, missing timeouts, connection pool exhaustion',
  whenToUse: 'Run when containers go unhealthy, memory grows unbounded, or response times degrade over time',
  phases: [
    { title: 'Memory scan', detail: 'Find unbounded in-memory caches and growing collections' },
    { title: 'Concurrency scan', detail: 'Find async gather() calls without semaphore limits' },
    { title: 'Connection scan', detail: 'Find DB session/connection leaks' },
    { title: 'Synthesize', detail: 'Prioritized fix list with container-safety verdict' },
  ],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
          category: { type: 'string' },
          file: { type: 'string' },
          line_hint: { type: 'string' },
          description: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'category', 'file', 'description', 'fix'],
      },
    },
  },
  required: ['findings'],
}

phase('Memory scan')

const [serviceMemFindings, modelCacheFindings] = await parallel([
  () => agent(
    `Audit the NMS codebase (D:/nms_final) for unbounded in-memory collections that grow over time.

Read these files: services/network_scanner.py, services/device_monitor.py,
services/dashboard_cache_service.py, services/tracking_discovery_cache.py,
services/app_classifier.py, services/device_enrichment_service.py,
services/gemini_classifier.py, services/snmp_service.py,
client_modules/system_core.py (if it exists).

Look for:
1. Instance variables (self._X = {}) or class-level dicts/lists that:
   - Are written to inside loops or per-request handlers
   - Have no eviction, no size cap, no TTL
   Known safe: network_scanner._vendor_cache (capped at 5000 with FIFO eviction — skip it)
2. Module-level globals that accumulate data (e.g. EVENT_STORE = [], CACHE = {})
3. Lists/sets that only grow (append but never prune)

Severity: CRITICAL if it can OOMkill the container (grows proportional to # devices * uptime),
HIGH if it grows slowly (bounded by unique keys but never cleared),
MEDIUM for bounded but inefficient (e.g. unbounded LRU candidate).

Return structured JSON findings.`,
    { label: 'service-memory', phase: 'Memory scan', schema: FINDINGS_SCHEMA }
  ),
  () => agent(
    `Audit the NMS codebase (D:/nms_final) for in-process caches that can grow unbounded.

Read these files: services/dashboard_cache_service.py, services/core_metrics_service.py,
services/alert_manager.py, services/sse_broadcaster.py, services/notification_service.py,
services/report_intelligence_rules.py.

Look for:
1. Dicts keyed by device_id or IP that accumulate entries and are never evicted
2. List-type caches (subscribers, listeners, watchers) that grow with subscriptions but shrink only on explicit unsubscribe
3. Event queues or message buffers with no max-size
4. Background threads that hold references to growing collections

Return structured JSON findings.`,
    { label: 'cache-memory', phase: 'Memory scan', schema: FINDINGS_SCHEMA }
  ),
])

phase('Concurrency scan')

const concurrencyFindings = await agent(
  `Audit the NMS codebase (D:/nms_final) for unsafe async concurrency patterns.

Read these files: routes/monitoring.py, routes/scanning.py, routes/devices.py,
services/network_scanner.py, services/device_monitor.py, services/scheduler.py,
services/auto_discovery_service.py, services/snmp_discovery_service.py.

Look for:
1. asyncio.gather(*tasks) where tasks is proportional to number of devices and there is NO
   asyncio.Semaphore limiting concurrency. These can open thousands of sockets simultaneously.
   Known safe: routes/monitoring.py get_monitoring_statistics (Semaphore(50) — skip it).
2. ThreadPoolExecutor or concurrent.futures without a max_workers cap
3. Network I/O calls (ping, socket, requests, httpx) in a loop without rate limiting
4. Background threads spawned per request (creates unbounded threads under load)
5. Missing timeouts on socket/ping/http operations (can hang indefinitely)

Severity: CRITICAL if it fires one socket/thread per device and devices > 100,
HIGH for missing timeouts on production-path network calls,
MEDIUM for thread pools with no cap.

Return structured JSON findings.`,
  { label: 'concurrency-scan', phase: 'Concurrency scan', schema: FINDINGS_SCHEMA }
)

phase('Connection scan')

const connectionFindings = await agent(
  `Audit the NMS codebase (D:/nms_final) for database connection and session leaks.

Read these files: services/device_monitor.py, services/scheduler.py,
services/maintenance_service.py, services/dashboard_availability.py,
services/core_metrics_service.py, routes/devices.py, routes/monitoring.py,
routes/tracking.py, routes/reports.py, services/tracking_workstation.py.

Look for:
1. db.session.query() or db.session.execute() inside a try block where the except/finally
   does NOT call db.session.remove() — this leaks connections back to the pool in a dirty state.
   Known safe: device_monitor.py (try/finally with remove — skip it),
               routes/devices.py exception handlers (remove after rollback — skip it).
2. Long-running background threads that hold a DB session open across multiple operations
   without calling remove() between them
3. db.session.rollback() without a subsequent db.session.remove() (rollback alone keeps the
   session open and dirty in the pool)
4. Code that creates a new db.session.query() inside a loop without cleanup between iterations

Severity: CRITICAL for leaks in hot paths (monitoring loop, scheduler jobs),
HIGH for leaks in routes that are called frequently,
MEDIUM for leaks in background tasks called infrequently.

Also check config.py for pool_size and max_overflow settings. If pool_size + max_overflow < 30
and the app has many concurrent background threads, flag it HIGH.

Return structured JSON findings.`,
  { label: 'connection-scan', phase: 'Connection scan', schema: FINDINGS_SCHEMA }
)

phase('Synthesize')

const allFindings = [
  ...(serviceMemFindings?.findings ?? []),
  ...(modelCacheFindings?.findings ?? []),
  ...(concurrencyFindings?.findings ?? []),
  ...(connectionFindings?.findings ?? []),
].filter(Boolean)

const critical = allFindings.filter(f => f.severity === 'CRITICAL')
const high = allFindings.filter(f => f.severity === 'HIGH')

log(`Perf Audit: ${critical.length} CRITICAL, ${high.length} HIGH, ${allFindings.length - critical.length - high.length} other`)

const report = await agent(
  `You are synthesizing a performance and container-safety audit for a production NMS Flask app running in Docker.

Findings (JSON):
${JSON.stringify(allFindings, null, 2)}

Produce a markdown report:
1. Container safety verdict: SAFE / AT_RISK / UNSAFE — with a one-line reason
2. CRITICAL findings — file, description, exact fix
3. HIGH findings — same format
4. MEDIUM/LOW as a table
5. Quick wins section: fixes that take < 10 lines of code and have the highest impact

Focus on what will actually cause the container to go unhealthy or OOMkill.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return report
