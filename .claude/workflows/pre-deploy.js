export const meta = {
  name: 'pre-deploy',
  description: 'Full pre-deployment gate: runs db-audit + perf-audit + migration-sync in parallel and produces a single GO / NO-GO verdict',
  whenToUse: 'Run before every production deployment',
  phases: [
    { title: 'Audit', detail: 'DB health, performance safety, and migration sync — all in parallel' },
    { title: 'Gate', detail: 'Combine results into a GO / NO-GO deployment decision' },
  ],
}

phase('Audit')

log('Running db-audit, perf-audit, and migration-sync in parallel...')

const [dbReport, perfReport, migReport] = await parallel([
  () => workflow('db-audit'),
  () => workflow('perf-audit'),
  () => workflow('migration-sync'),
])

phase('Gate')

const gate = await agent(
  `You are the deployment gate for a production NMS application.

Three audit reports were just run. Summarize them and give a single deployment verdict.

--- DB AUDIT ---
${dbReport ?? 'failed to run'}

--- PERF AUDIT ---
${perfReport ?? 'failed to run'}

--- MIGRATION SYNC ---
${migReport ?? 'failed to run'}

Produce:
1. GO / NO-GO verdict in large bold text
2. Blockers (anything CRITICAL) — must fix before deploy
3. Warnings (HIGH severity) — should fix soon but not blocking
4. Green lights — what passed cleanly
5. Estimated risk if deployed NOW with current findings

Be direct. If there are CRITICAL issues, it is NO-GO. If only HIGH, it is GO WITH CAVEATS.`,
  { label: 'deployment-gate', phase: 'Gate' }
)

return gate
