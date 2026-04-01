"""
settings_audit_log — records AppSettings changes to the AuditLog table.

Called automatically from AppSettings.set().  All operations are
exception-safe so a logging failure never blocks a settings write.

Secret values are masked with '••••••' before being stored.
"""
import logging

logger = logging.getLogger(__name__)

_MASKED = '••••••'


def log_settings_change(
    key: str,
    old_value,
    new_value,
    actor_id=None,
    is_secret: bool = False,
) -> None:
    """Write a settings-change entry to the AuditLog table.

    Silently no-ops on any exception so it never disrupts the caller.
    """
    try:
        from models.audit_log import AuditLog
        from extensions import db

        username = 'system'
        user_role = 'admin'
        if actor_id:
            try:
                from models.user import User
                actor = User.query.get(actor_id)
                if actor:
                    username = actor.username or 'unknown'
                    user_role = actor.role or 'admin'
            except Exception:
                pass

        masked_old = _MASKED if (is_secret and old_value) else old_value
        masked_new = _MASKED if (is_secret and new_value) else new_value

        entry = AuditLog(
            user_id=actor_id,
            username=username,
            user_role=user_role,
            action='settings_change',
            entity_type='app_settings',
            entity_id=None,
            entity_name=key,
            description=f"Setting '{key}' updated",
            changes={'key': key, 'old': masked_old, 'new': masked_new},
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        try:
            from extensions import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.exception("[settings_audit] Failed to log change for key %r", key)
