export const meta = {
  name: 'migration-sync',
  description: 'Verify every db.Index in SQLAlchemy models has a matching CREATE INDEX in startup_migrations.py — catches indexes that exist on fresh installs but are missing from the production DB',
  whenToUse: 'Run after adding any db.Index to a model, or before deploying to production',
  phases: [
    { title: 'Extract', detail: 'Read model indexes and startup_migrations entries' },
    { title: 'Diff', detail: 'Find gaps and generate the missing SQL statements' },
  ],
}

const DIFF_SCHEMA = {
  type: 'object',
  properties: {
    model_indexes: {
      type: 'array',
      items: { type: 'object', properties: { name: { type: 'string' }, table: { type: 'string' }, columns: { type: 'string' }, model_file: { type: 'string' } }, required: ['name', 'table', 'columns', 'model_file'] },
    },
    migration_indexes: {
      type: 'array',
      items: { type: 'object', properties: { name: { type: 'string' }, sql: { type: 'string' } }, required: ['name', 'sql'] },
    },
    missing_from_migrations: {
      type: 'array',
      items: { type: 'object', properties: { name: { type: 'string' }, table: { type: 'string' }, columns: { type: 'string' } }, required: ['name', 'table', 'columns'] },
    },
    only_in_migrations: {
      type: 'array',
      items: { type: 'string' },
    },
  },
  required: ['model_indexes', 'migration_indexes', 'missing_from_migrations', 'only_in_migrations'],
}

phase('Extract')

const diff = await agent(
  `You are auditing index coverage for the NMS production database.

The app uses db.create_all() only — no Alembic. Indexes added to models AFTER initial deploy
are NEVER applied to the production DB unless they also appear in services/startup_migrations.py.

Step 1: Read ALL model files in D:/nms_final/models/*.py
Extract every db.Index() call. For each, capture:
- name (first arg, e.g. 'idx_device_activity_logs_device_timestamp')
- table (__tablename__ of the containing class)
- columns (remaining args, comma-joined)
- model_file (the filename)

Step 2: Read D:/nms_final/services/startup_migrations.py
Extract every "CREATE INDEX ... IF NOT EXISTS <name>" statement. Capture the index name.

Step 3: Diff:
- missing_from_migrations: indexes in models that are NOT in startup_migrations (these are MISSING from prod DB)
- only_in_migrations: index names in startup_migrations that don't match any model index (stale)

Return structured JSON with all four lists.`,
  { label: 'extract-diff', phase: 'Extract', schema: DIFF_SCHEMA }
)

phase('Diff')

if (!diff) {
  return 'Migration sync check failed — could not read model files.'
}

const missing = diff.missing_from_migrations ?? []
const stale = diff.only_in_migrations ?? []

log(`${diff.model_indexes?.length ?? 0} model indexes, ${diff.migration_indexes?.length ?? 0} in migrations, ${missing.length} missing, ${stale.length} stale`)

const report = await agent(
  `You are generating a migration sync report for an NMS production database.

Diff results:
${JSON.stringify(diff, null, 2)}

Produce a markdown report with:

## Coverage: X/Y indexes synced

## Missing from startup_migrations.py (MUST ADD for production)
For each missing index, provide the exact Python snippet to add to run_startup_migrations():

\`\`\`python
# For PostgreSQL (CONCURRENTLY avoids table lock):
cursor.execute("""
    CREATE INDEX CONCURRENTLY IF NOT EXISTS <name>
    ON <table> (<columns>)
""")
# For SQLite:
cursor.execute("""
    CREATE INDEX IF NOT EXISTS <name>
    ON <table> (<columns>)
""")
\`\`\`

## Stale entries in startup_migrations.py (indexes with no model counterpart)
These are safe to remove if the table/column was dropped.

## Verdict
SYNCED — production DB matches models
GAPS FOUND — N indexes missing from startup_migrations, production DB is behind

Keep it copy-paste ready. The developer should be able to paste the snippets directly into startup_migrations.py.`,
  { label: 'generate-report', phase: 'Diff' }
)

return report
