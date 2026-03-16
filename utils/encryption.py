"""
Fernet symmetric encryption for sensitive database fields.

Usage:
    from utils.encryption import encrypt, decrypt

    # Store encrypted
    device.snmp_community = encrypt("public")

    # Read plaintext
    plaintext = decrypt(device.snmp_community)

Key management:
    Generate once:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Set in .env:    FERNET_KEY=<generated-key>
"""

import os
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Prefix stored in DB so we can distinguish already-encrypted values from
# legacy plaintext values (safe migration without a one-shot data migration).
_PREFIX = b"enc:"


def _get_fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FERNET_KEY environment variable is not set. "
            "Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise RuntimeError(f"FERNET_KEY is invalid: {exc}") from exc


def encrypt(value: str | None) -> str | None:
    """Encrypt a plaintext string. Returns None if value is None/empty.

    Falls back to storing plaintext when FERNET_KEY is not configured so that
    saves succeed in development environments where the key has not been set.
    The decrypt() function handles both encrypted and plaintext values
    transparently, so no data migration is needed when the key is added later.
    """
    if not value:
        return value
    try:
        f = _get_fernet()
    except RuntimeError:
        logger.warning(
            "[encryption] FERNET_KEY not set — storing value as plaintext. "
            "Generate a key and set FERNET_KEY in .env to enable encryption."
        )
        return value
    token = f.encrypt(value.encode("utf-8"))
    return (_PREFIX + token).decode("utf-8")


def decrypt(value: str | None) -> str | None:
    """
    Decrypt a Fernet-encrypted string.

    Falls back transparently to returning the raw value when it is plaintext
    (no enc: prefix) — this allows a zero-downtime migration where old rows
    continue to work until they are re-saved.
    """
    if not value:
        return value
    raw = value.encode("utf-8")
    if not raw.startswith(_PREFIX):
        # Legacy plaintext row — return as-is, log a warning once
        logger.debug("[encryption] Encountered unencrypted value during decrypt; returning plaintext.")
        return value
    try:
        f = _get_fernet()
        return f.decrypt(raw[len(_PREFIX):]).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt field — key mismatch or corrupted data.") from exc
