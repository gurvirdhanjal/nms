export const meta = {
  name: 'db-audit',
  description: 'Audit the NMS codebase for DB health issues: missing indexes, N+1 queries, session leaks, unbounded tables, missing retention jobs',
  whenToUse: 'Run before any deployment, after adding new models, or when the reports tab or containers seem slow',
  phases: [
    { title: 'Scan models', detail: 'Find tables missing indexes and check startup_migrations coverage' },
    { title: 'Scan queries', detail: 'Find N+1 patterns, unbounded .all(), missing session cleanup' },
    { title: 'Scan retention', detail: 'Find tables with no scheduler purge job' },
    { title: 'Synthesize', detail: 'Produce prioritized fix list' },
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

phase('Scan models')

const [modelFindings, migrationFindings] = await parallel([
  () => agent(
    `You are auditing the NMS Flask/SQLAlchemy codebase at D:/nms_final.

Read ALL model files in models/*.py (there are ~35 of them).

For each SQLAlchemy model class, check:
1. Does it have __table_args__ with at least one index on its most-queried columns?
   High-growth tables that MUST have composite indexes: any table with a device_id/device_ip + timestamp pattern.
   Tables known to be safe (already fixed): network_scan, port_scan_result, device_activity_logs, device_resource_logs, device_application_logs.
2. Does the table have a foreign key column used in WHERE clauses but no index on it?
3. Is there a timestamp column used in range queries but no index?

Report each gap as a finding. Severity CRITICAL if the table has millions of rows (log/history/metric tables), HIGH otherwise.
Return structured JSON matching the schema.`,
    { label: 'model-index-scan', phase: 'Scan models', schema: FINDINGS_SCHEMA }
  ),
  () => agent(
    `You are auditing the NMS Flask/SQLAlchemy codebase at D:/nms_final.

Read services/startup_migrations.py (the idempotent index backfill runner).
Read all model files in models/*.py.

Extract every db.Index name from the models (lines matching db.Index('idx_...', ...)).
Extract every CREATE INDEX name from startup_migrations.py.

Find indexes that are defined in models but NOT in startup_migrations.py — these indexes
exist on fresh installs but are MISSING from the production database (because db.create_all
never alters existing tables).

Report each gap as a CRITICAL finding. Include the exact index name and which model it's in.
Return structured JSON matching the schema.`,
    { label: 'migration-coverage', phase: 'Scan models', schema: FINDINGS_SCHEMA }
  ),
])

phase('Scan queries')

const SERVICE_FILES = [
  'services/enterprise_report_service.py',
  'services/maintenance_service.py',
  'services/device_monitor.py',
  'services/scheduler.py',
  'services/dashboard_cache_service.py',
  'services/core_metrics_service.py',
  'services/tracking_workstation.py',
  'services/device_enrichment_service.py',
  'services/reporting_service.py',
]

const ROUTE_FILES = [
  'routes/monitoring.py',
  'routes/reports.py',
  'routes/devices.py',
  'routes/tracking.py',
  'routes/dashboard.py',
  'routes/scanning.py',
]

const [serviceQueryFindings, routeQueryFindings] = await parallel([
  () => agent(
    `You are auditing these NMS service files for DB query problems.
Files to read (all in D:/nms_final): ${SERVICE_FILES.join(', ')}

Look for:
1. N+1 QUERIES: A loop over a list of devices/items that fires a DB query per item
   (e.g. for device in devices: db.session.query(...).filter_by(device_id=device.id).all())
   These should be replaced with a single bulk query + group-by-in-Python.
2. UNBOUNDED .all(): .query(...).all() with no .limit() on a high-growth table
   (device_activity_logs, scan_history, resource_logs, etc.)
3. SESSION LEAKS: db.session.query() in a try block with no db.session.remove() in finally
4. MISSING INDEXES: WHERE clause using a column that has no index (check against models)

Report severity CRITICAL for N+1 on large tables, HIGH for unbounded .all() on log tables,
MEDIUM for session leaks, LOW for missing indexes on small tables.
Return structured JSON.`,
    { label: 'service-query-scan', phase: 'Scan queries', schema: FINDINGS_SCHEMA }
  ),
  () => agent(
    `You are auditing these NMS route files for DB query problems.
Files to read (all in D:/nms_final): ${ROUTE_FILES.join(', ')}

Look for:
1. UNBOUNDED .all() on Device or log tables at request time (blocks the HTTP thread)
2. N+1 queries inside request handlers
3. Missing db.session.remove() in exception handlers
4. Queries inside loops that fire per device/per item

Severity: CRITICAL if it can stall a health check or block the main thread,
HIGH if it causes slow page loads, MEDIUM otherwise.
Return structured JSON.`,
    { label: 'route-query-scan', phase: 'Scan queries', schema: FINDINGS_SCHEMA }
  ),
])

phase('Scan retention')

const retentionFindings = await agent(
  `You are auditing the NMS codebase for tables that grow unbounded because they have no cleanup job.

Read services/scheduler.py — find every purge/cleanup/delete job and note which table it cleans.
Read models/*.py — find every model class and note its table name.

Known tables already covered by cleanup jobs (skip these):
network_scan, port_scan_result, device_activity_logs, device_resource_logs,
device_application_logs, poll_tasks, alert_fanout_tasks, dashboard_events (alerts).

For every OTHER table that:
- has a timestamp/created_at column (grows over time)
- is written to frequently (has insert calls in services/ or routes/)
- is NOT cleaned by any scheduler job

Report as HIGH severity findings. Include estimated growth rate if inferable from the write patterns.
Return structured JSON.`,
  { label: 'retention-scan', phase: 'Scan retention', schema: FINDINGS_SCHEMA }
)

phase('Synthesize')

const allFindings = [
  ...(modelFindings?.findings ?? []),
  ...(migrationFindings?.findings ?? []),
  ...(serviceQueryFindings?.findings ?? []),
  ...(routeQueryFindings?.findings ?? []),
  ...(retentionFindings?.findings ?? []),
].filter(Boolean)

const critical = allFindings.filter(f => f.severity === 'CRITICAL')
const high = allFindings.filter(f => f.severity === 'HIGH')
const medium = allFindings.filter(f => f.severity === 'MEDIUM')
const low = allFindings.filter(f => f.severity === 'LOW')

log(`DB Audit complete: ${critical.length} CRITICAL, ${high.length} HIGH, ${medium.length} MEDIUM, ${low.length} LOW`)

const report = await agent(
  `You are synthesizing a DB health audit report for a production NMS Flask application.

Here are all findings (JSON):
${JSON.stringify(allFindings, null, 2)}

Produce a clean markdown report with:
1. Executive summary (2-3 sentences, focus on production risk)
2. CRITICAL issues section — each with file, description, and the exact fix to apply
3. HIGH issues section — same format
4. MEDIUM/LOW as a brief table
5. A "Safe to deploy?" verdict: YES / NO / WITH_CAVEATS

Keep it actionable. Every finding should have a concrete next step.`,
  { label: 'synthesize-report', phase: 'Synthesize' }
)

return report
