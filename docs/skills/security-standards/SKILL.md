---
name: security-standards
description: Apply authentication, authorization, and secrets-handling standards for this Flask application. Use when changing login/session flows, protected APIs, RBAC decorators, LDAP/agent/mobile auth, or any handling of credentials, tokens, and encrypted/hashed data.
---

# Security Standards

## Overview

Use this skill to enforce security controls without breaking existing operational workflows. Focus on auth gates, least privilege RBAC, and secure handling of secrets/tokens/passwords.

## Workflow

1. Identify the security surface:
- authentication/session logic (`routes/auth.py`, `middleware/session_middleware.py`)
- permission checks (`middleware/rbac.py`, route decorators)
- token/key validation (`routes/agent.py`, `routes/api_v1/*`)
- secret/config handling (`config.py`, env usage)
2. Load relevant references from `references/`.
3. Apply rules while preserving compatibility for existing users/clients.
4. Verify:
- unauthorized access returns 401
- insufficient role/permission returns 403
- no sensitive values are logged or serialized

## Reference Map

- Auth rules: `references/auth-rules.md`
- RBAC rules: `references/rbac.md`
- Encryption and secret handling: `references/encryption.md`

## Hard Rules

1. Protect non-public endpoints with centralized middleware/decorators.
2. Keep authn/authz checks in middleware/helpers, not scattered inline route logic.
3. Never commit new hardcoded secrets, keys, passwords, or bearer tokens.
4. Never log tokens, passwords, API keys, or auth headers.
5. Store passwords as hashes only (bcrypt/werkzeug), never plaintext.
6. Preserve consistent 401 vs 403 semantics across API and page responses.

## Completion Checklist

1. Confirm each changed protected route has explicit auth + role/permission checks.
2. Confirm session/API-key/token paths return controlled JSON/page responses.
3. Confirm secrets are read from env/config indirection and not echoed in logs.
4. Confirm model/API serialization excludes sensitive fields.
