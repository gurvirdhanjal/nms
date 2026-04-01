"""
AppSettings — DB-backed key-value configuration store.

Provides a thread-safe in-memory cache (60s TTL) over a simple key-value
table, with Fernet encryption for secret values and audit logging on writes.

Usage:
    from models.app_settings import AppSettings

    # Read (cache-first)
    smtp_server = AppSettings.get('smtp_server', default='localhost')

    # Write (cache-invalidated, audit-logged)
    AppSettings.set('smtp_server', 'mail.company.com', actor_id=user_id)

    # Seed non-destructively from env on startup
    AppSettings.seed_from_env('smtp_server', 'SMTP_SERVER', 'smtp', 'SMTP server host')

site_id column is reserved for future multi-tenant scoping — currently always NULL.
"""
import logging
import os
import threading
import time
from datetime import datetime

from extensions import db

logger = logging.getLogger(__name__)


class AppSettings(db.Model):
    __tablename__ = 'app_settings'

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key         = db.Column(db.String(100), nullable=False, unique=True)
    value       = db.Column(db.Text, nullable=True)
    category    = db.Column(db.String(50), nullable=True)   # smtp | monitoring | retention | alerts
    description = db.Column(db.Text, nullable=True)
    is_secret   = db.Column(db.Boolean, nullable=False, default=False)
    updated_at  = db.Column(db.DateTime, nullable=True)
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )
    # Reserved for multi-tenant future use; always NULL for now.
    site_id     = db.Column(db.Integer, nullable=True)

    # ------------------------------------------------------------------ #
    # In-memory cache — key → (plaintext_value, expiry_epoch)             #
    # ------------------------------------------------------------------ #
    _cache: dict = {}
    _cache_lock = threading.Lock()
    _CACHE_TTL: float = 60.0

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls, key: str, default=None):
        """Return the plaintext setting value, using cache when fresh."""
        now = time.time()
        with cls._cache_lock:
            cached = cls._cache.get(key)
            if cached is not None:
                val, expiry = cached
                if now < expiry:
                    return val
                del cls._cache[key]

        try:
            row = cls.query.filter_by(key=key, site_id=None).first()
            if row is None:
                return default
            val = cls._decrypt_if_secret(row)
            with cls._cache_lock:
                cls._cache[key] = (val, now + cls._CACHE_TTL)
            return val
        except Exception as exc:
            if isinstance(exc, RuntimeError) and 'application context' in str(exc).lower():
                logger.debug(
                    "[AppSettings] key %r not accessible (no app context) — using fallback", key
                )
            else:
                logger.exception("[AppSettings] Error reading key %r", key)
            return default

    @classmethod
    def set(cls, key: str, value, actor_id=None) -> None:
        """Upsert a setting value, invalidate cache, and write an audit entry."""
        try:
            row = cls.query.filter_by(key=key, site_id=None).first()
            if row is None:
                # Key was never seeded — create it with minimal metadata.
                logger.warning("[AppSettings] key %r not pre-seeded; auto-creating row", key)
                row = cls(key=key, site_id=None, is_secret=False)
                db.session.add(row)

            old_plaintext = cls._decrypt_if_secret(row)
            raw_value = str(value) if value is not None else None
            row.value = cls._encrypt_if_secret(row, raw_value)
            row.updated_at = datetime.utcnow()
            row.updated_by_user_id = actor_id
            db.session.commit()

            with cls._cache_lock:
                cls._cache.pop(key, None)

            # Audit — fire-and-forget; never let failure break the write.
            try:
                from services.settings_audit_log import log_settings_change
                log_settings_change(key, old_plaintext, raw_value, actor_id, row.is_secret)
            except Exception:
                pass

        except Exception:
            db.session.rollback()
            logger.exception("[AppSettings] Error setting key %r", key)
            raise

    @classmethod
    def get_category(cls, category: str) -> dict:
        """Return {key: plaintext_value} for all keys in a category."""
        try:
            rows = cls.query.filter_by(category=category, site_id=None).all()
            return {row.key: cls._decrypt_if_secret(row) for row in rows}
        except Exception:
            logger.exception("[AppSettings] Error reading category %r", category)
            return {}

    @classmethod
    def seed_from_env(
        cls,
        key: str,
        env_var: str,
        category: str,
        description: str,
        is_secret: bool = False,
    ) -> None:
        """Non-destructive: only inserts if the row is absent.

        Reads the initial value from the named environment variable.
        Safe to call on every startup — skips silently if already seeded.
        """
        try:
            if cls.query.filter_by(key=key, site_id=None).first() is not None:
                return  # Already seeded — do not overwrite.

            raw_value = os.environ.get(env_var)
            stored_value: str | None
            if raw_value is not None and is_secret:
                from utils.encryption import encrypt as _enc
                stored_value = _enc(raw_value)
            else:
                stored_value = raw_value

            row = cls(
                key=key,
                value=stored_value,
                category=category,
                description=description,
                is_secret=is_secret,
                updated_at=datetime.utcnow(),
                site_id=None,
            )
            db.session.add(row)
            db.session.commit()
            logger.debug("[AppSettings] Seeded key %r from env %r", key, env_var)
        except Exception:
            db.session.rollback()
            logger.exception("[AppSettings] Error seeding key %r from env %r", key, env_var)

    @classmethod
    def invalidate_cache(cls, key: str | None = None) -> None:
        """Invalidate one key or the entire cache (when key is None)."""
        with cls._cache_lock:
            if key is None:
                cls._cache.clear()
            else:
                cls._cache.pop(key, None)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @classmethod
    def _decrypt_if_secret(cls, row: 'AppSettings'):
        """Return the plaintext value for a row, decrypting if is_secret."""
        if row.value is None:
            return None
        if row.is_secret:
            try:
                from utils.encryption import decrypt as _dec
                return _dec(row.value)
            except Exception:
                logger.warning("[AppSettings] Failed to decrypt key %r", row.key)
                return None
        return row.value

    @classmethod
    def _encrypt_if_secret(cls, row: 'AppSettings', raw_value: str | None) -> str | None:
        if raw_value is None:
            return None
        if row.is_secret:
            try:
                from utils.encryption import encrypt as _enc
                return _enc(raw_value)
            except Exception:
                logger.warning("[AppSettings] Encryption failed for key %r — storing plaintext", row.key)
        return raw_value

    def __repr__(self):
        return f'<AppSettings key={self.key!r} category={self.category!r}>'
