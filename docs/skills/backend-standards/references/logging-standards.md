# Logging Standards

## Goal

Make production incidents diagnosable without exposing secrets or flooding logs with noise.

## Current State

- Good patterns exist:
  - module logger usage in `routes/reports.py`, `routes/tracking.py`, `services/device_identity.py`
- Legacy noise exists:
  - many `print(...)` debug statements in routes/services

## Required Rules For New/Edited Code

1. Use module-level logger:
- `logger = logging.getLogger(__name__)`
2. Prefer structured message patterns with stable keys:
- `logger.info("Scan started: id=%s user=%s range=%s", scan_id, username, ip_range)`
3. Pick level by intent:
- `debug`: noisy diagnostics, disabled in normal ops
- `info`: lifecycle milestones
- `warning`: recoverable anomalies
- `error`: operation failed
- `exception`: failed with traceback
4. Never log sensitive data:
- passwords
- API keys/tokens
- auth headers
- raw credential payloads

## Message Design

Use short event prefixes and machine-scannable context:
- `[Report] type=%s range=%s rows=%s duration_ms=%s`
- `[AgentHTTP] host=%s path=%s result=%s latency_ms=%s`
- `[Scheduler] task=%s enqueued=%s skipped=%s`

## Migration Guidance

When touching a file that already uses `print(...)`:
1. Replace touched print statements with logger calls.
2. Keep message intent identical.
3. Preserve important identifiers (`device_id`, `scan_id`, etc.).

Do not do repository-wide log rewrites as part of unrelated features.

## Error Logging Rule

For exceptions in API handlers/services:
1. log with context (`logger.exception(...)` or `logger.error(...)`)
2. return controlled JSON/page response
3. rollback DB session when write transaction was active
