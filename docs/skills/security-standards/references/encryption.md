# Encryption And Secret Handling

## Goal

Protect credentials, tokens, and sensitive transport paths across backend and agent communication.

## Password and Credential Storage

1. Store user passwords as bcrypt hashes:
- creation/update paths in `routes/auth.py` and `routes/user_management.py`
2. Never store or return plaintext passwords.
3. Exclude sensitive fields from model serialization:
- `device_password_hash` intentionally excluded in `models/device.py`

## Secret Management Rules

1. Read secrets from environment/config indirection (`config.py`), not hardcoded literals in new code.
2. Do not commit new default secrets.
3. Do not print or log secret values.

Note:
- Existing repository contains legacy hardcoded defaults; treat as technical debt.
- Do not add more. Prefer gradual migration to env-only values.

## Transport Security Rules

1. Use HTTPS/TLS for production agent and API traffic.
2. Keep bearer tokens/API keys in headers, not query strings.
3. For LDAP, use secure options (`LDAP_USE_SSL`/`LDAP_STARTTLS`, cert validation settings) in production.

## Logging Rules For Secrets

Never log:
- `Authorization` header
- API keys/tokens
- password fields
- full SMTP/LDAP credentials

If logging is required for debugging, log only whether credential is present (boolean) or masked metadata.

## Checklist

1. Any new credential path hashed/encrypted or securely externalized?
2. Any secret accidentally serialized in API responses?
3. Any secret leaked in logs or exceptions?
4. Is transport expected to run over TLS in production?
