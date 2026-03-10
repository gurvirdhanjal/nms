# Auth Rules

## Goal

Ensure every protected surface has explicit authentication with correct failure semantics.

## Current Auth Layers

1. Session middleware:
- `setup_auth_middleware(...)` in `middleware/session_middleware.py`
- guards protected blueprints and enforces session timeout
2. Route-level decorators:
- `require_login`, `require_role`, `require_permission` in `middleware/rbac.py`
3. Token/API key auth:
- agent ingestion token check in `routes/agent.py`
- API v1 key check in `routes/api_v1/__init__.py`

## Required Rules

1. Protect non-public route handlers with centralized middleware/decorators.
2. Return `401` for unauthenticated requests.
3. Return `403` for authenticated requests lacking authorization.
4. Keep API errors JSON on `/api/*`; keep page flows redirect/flash where expected.
5. Never leak auth internals in responses (no key/token comparisons in payload).

## Session Rules

1. Keep session lifetime and refresh behavior aligned with config (`PERMANENT_SESSION_LIFETIME`, cookie flags).
2. Do not store secrets in session.
3. Update activity timestamps only after successful auth checks.

## Token Rules

1. Read expected keys/tokens from config/env.
2. Compare provided token/key exactly and fail closed.
3. Do not log provided token/key values.

## Checklist

1. Is auth enforced before business logic?
2. Is 401 vs 403 behavior correct?
3. Are auth failures returned in the expected contract shape?
