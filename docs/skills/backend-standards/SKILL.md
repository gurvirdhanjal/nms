---
name: backend-standards
description: Apply backend engineering standards for this Flask + SQLAlchemy codebase. Use when changing `routes/*.py`, `services/*.py`, `models/*.py`, `middleware/*.py`, `utils/db_migrations.py`, or backend APIs that affect contracts, error handling, database performance, logging, or scaling behavior.
---

# Backend Standards

## Overview

Use this skill to keep backend changes consistent with existing architecture and operational rules in this repository. Favor contract safety, explicit error paths, efficient DB access, and queue-based scaling patterns.

## Workflow

1. Identify the touched backend surface:
- API endpoint (`routes/`)
- business logic (`services/`)
- schema/migration (`models/`, `utils/db_migrations.py`)
- middleware/auth (`middleware/`)
2. Load only the relevant reference files from `references/`.
3. Apply standards without breaking existing response shapes already used by frontend pages.
4. Validate:
- run targeted tests for changed endpoints/services
- verify DB writes include rollback-safe handling
- verify logs contain context but no secrets

## Reference Map

- API contracts: `references/api-contracts.md`
- Error handling: `references/error-handling.md`
- DB performance: `references/database-performance.md`
- Logging: `references/logging-standards.md`
- Scaling/queue model: `references/scaling-guidelines.md`

## Hard Rules

1. Preserve endpoint contract compatibility unless the change is explicitly versioned.
2. Return JSON for API errors; avoid HTML error payloads on `/api/*`.
3. Use DB transactions with explicit rollback on write failures.
4. Keep scheduler paths free of direct network I/O; enqueue tasks for workers.
5. Use module loggers; do not add new `print()` calls in request/worker paths.
6. Never log credentials, auth headers, API keys, passwords, or full tokens.

## Completion Checklist

1. Confirm endpoint auth (`require_login`, `require_role`, or API key gate) is enforced.
2. Confirm input validation returns deterministic `4xx` errors.
3. Confirm expensive queries are bounded (time range, limit, pagination, rollups).
4. Confirm writes use one transaction boundary and rollback on exception.
5. Confirm logs are structured and sanitized.
