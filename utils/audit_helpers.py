"""Helpers for capturing before/after field-level diffs in audit logs."""

_SENSITIVE_FIELDS = frozenset({
    'device_password_hash',
    '_snmp_community',
    'snmp_auth_password',
    'snmp_priv_password',
    'wmi_password',
    'password_hash',
})

_NOISE_FIELDS = frozenset({'updated_at'})

_EXCLUDE = _SENSITIVE_FIELDS | _NOISE_FIELDS


def capture_model_diff(before_dict: dict, after_obj) -> dict:
    """Return field-level diff between a pre-save snapshot and a post-save model.

    Args:
        before_dict: dict snapshot captured via model.to_dict() *before* changes
                     were applied (and before db.session.commit).
        after_obj:   SQLAlchemy model instance *after* commit.

    Returns:
        Dict of {field: {'before': old_value, 'after': new_value}} for every
        field that changed.  Sensitive and noise fields are excluded.
    """
    after_dict = after_obj.to_dict() if hasattr(after_obj, 'to_dict') else {}
    return {
        k: {'before': before_dict.get(k), 'after': after_dict.get(k)}
        for k in after_dict
        if k not in _EXCLUDE and before_dict.get(k) != after_dict.get(k)
    }
