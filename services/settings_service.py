"""
settings_service — business logic for reading/writing application settings.

All reads go through AppSettings (DB-first, 60s cache, env fallback).
All writes validate input then delegate to AppSettings.set().
"""
import logging
import os
import smtplib
import ssl

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# SMTP                                                                 #
# ------------------------------------------------------------------ #

# DB key → (env_var_name, default_value)
_SMTP_KEYS: dict = {
    'smtp_server':     ('SMTP_SERVER',     'smtp.gmail.com'),
    'smtp_port':       ('SMTP_PORT',       '587'),
    'smtp_user':       ('SMTP_USERNAME',   ''),
    'smtp_password':   ('SMTP_PASSWORD',   ''),
    'smtp_from':       ('SMTP_FROM',       ''),
    'smtp_recipients': ('SMTP_RECIPIENTS', ''),
    'smtp_use_tls':    ('SMTP_USE_TLS',    'true'),
}

_MASKED_PASSWORD = '••••••'


def get_smtp_config() -> dict:
    """Return plain SMTP config dict: DB-first, env fallback per key."""
    from models.app_settings import AppSettings
    result: dict = {}
    for key, (env_var, default) in _SMTP_KEYS.items():
        db_val = AppSettings.get(key)
        if db_val is not None:
            result[key] = db_val
        else:
            result[key] = os.environ.get(env_var, default)
    return result


def get_smtp_config_with_source() -> dict:
    """Return SMTP config with per-key source annotation for the UI.

    Returns:
        {key: {'value': ..., 'source': 'database'|'environment'|'default'}}
    """
    from models.app_settings import AppSettings
    result: dict = {}
    for key, (env_var, default) in _SMTP_KEYS.items():
        db_val = AppSettings.get(key)
        if db_val is not None:
            result[key] = {'value': db_val, 'source': 'database'}
        elif os.environ.get(env_var):
            result[key] = {'value': os.environ.get(env_var), 'source': 'environment'}
        else:
            result[key] = {'value': default, 'source': 'default'}
    return result


def get_smtp_config_masked() -> dict:
    """Same as get_smtp_config but replaces the password with masked sentinel."""
    config = get_smtp_config()
    if config.get('smtp_password'):
        config['smtp_password'] = _MASKED_PASSWORD
    return config


def set_smtp_config(data: dict, actor_id=None) -> None:
    """Validate and persist SMTP settings.

    Skips smtp_password if the value equals the masked sentinel (no-change).
    """
    from models.app_settings import AppSettings
    for key in _SMTP_KEYS:
        if key not in data:
            continue
        val = data[key]
        if key == 'smtp_password' and val == _MASKED_PASSWORD:
            continue  # UI sent the mask — do not overwrite the stored password.
        AppSettings.set(key, val, actor_id=actor_id)


def is_smtp_configured() -> bool:
    """Return True if enough SMTP config is present to attempt sending."""
    cfg = get_smtp_config()
    return bool(cfg.get('smtp_server') and cfg.get('smtp_user'))


def test_smtp_connection(config: dict | None = None) -> tuple:
    """Open an SMTP connection and send a test message.

    Args:
        config: Optional override dict (uses get_smtp_config() if None).

    Returns:
        (success: bool, message: str)
    """
    if config is None:
        config = get_smtp_config()

    server = (config.get('smtp_server') or '').strip()
    try:
        port = int(config.get('smtp_port') or 587)
    except (TypeError, ValueError):
        port = 587
    user = (config.get('smtp_user') or '').strip()
    password = config.get('smtp_password') or ''
    use_tls = str(config.get('smtp_use_tls') or 'true').lower() in ('true', '1', 'yes')
    recipients_raw = config.get('smtp_recipients') or ''
    from_addr = (config.get('smtp_from') or user).strip()
    recipient_list = [r.strip() for r in recipients_raw.split(',') if r.strip()]

    if not server:
        return False, 'SMTP server not configured.'

    def _send(smtp: smtplib.SMTP) -> None:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if user and password and password != _MASKED_PASSWORD:
            smtp.login(user, password)
        if recipient_list and from_addr:
            msg = (
                f"From: {from_addr}\r\n"
                f"To: {recipient_list[0]}\r\n"
                f"Subject: [NMS] Test Email\r\n\r\n"
                f"This is a test email from your Network Monitoring System settings page."
            )
            smtp.sendmail(from_addr, recipient_list, msg)

    try:
        with smtplib.SMTP(server, port, timeout=5) as smtp:
            _send(smtp)
        suffix = f' Test email sent to {recipient_list[0]}.' if recipient_list else ''
        return True, f'SMTP connection successful.{suffix}'
    except smtplib.SMTPAuthenticationError:
        return False, 'SMTP authentication failed. Check username and password.'
    except smtplib.SMTPConnectError as exc:
        return False, f'Could not connect to {server}:{port} — {exc}'
    except TimeoutError:
        return False, f'Connection timed out connecting to {server}:{port}.'
    except Exception as exc:
        return False, f'SMTP test failed: {exc}'


# ------------------------------------------------------------------ #
# Monitoring interval                                                  #
# ------------------------------------------------------------------ #

_INTERVAL_KEY = 'monitoring_interval_seconds'
_INTERVAL_MIN = 10        # 10s absolute floor — below this, probe overhead dominates
_INTERVAL_MAX = 3600
_INTERVAL_DEFAULT = 15    # 15s: ±15s downtime timestamp accuracy, 3-probe RTT quality


def format_monitoring_interval_label(seconds: int | None) -> str:
    """Return a compact human-readable monitoring interval label."""
    try:
        interval = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    if interval <= 0:
        return "—"
    if interval < 60:
        return f"{interval} sec"
    if interval % 3600 == 0:
        hours = interval // 3600
        return f"{hours} hr" if hours == 1 else f"{hours} hrs"
    if interval % 60 == 0:
        minutes = interval // 60
        return f"{minutes} min"
    minutes = interval / 60.0
    return f"{minutes:.1f} min"


def get_monitoring_interval() -> int:
    """Return monitoring interval in seconds (clamped 10–3600). DB-first."""
    from models.app_settings import AppSettings
    db_val = AppSettings.get(_INTERVAL_KEY)
    if db_val is not None:
        try:
            return max(_INTERVAL_MIN, min(_INTERVAL_MAX, int(db_val)))
        except (TypeError, ValueError):
            pass
    env_val = os.environ.get('MONITORING_INTERVAL', str(_INTERVAL_DEFAULT))
    try:
        return max(_INTERVAL_MIN, min(_INTERVAL_MAX, int(env_val)))
    except (TypeError, ValueError):
        return _INTERVAL_DEFAULT


def get_monitoring_interval_with_source() -> dict:
    """Return monitoring interval with source annotation for the UI."""
    from models.app_settings import AppSettings
    db_val = AppSettings.get(_INTERVAL_KEY)
    if db_val is not None:
        try:
            return {'value': max(_INTERVAL_MIN, min(_INTERVAL_MAX, int(db_val))), 'source': 'database'}
        except (TypeError, ValueError):
            pass
    env_val = os.environ.get('MONITORING_INTERVAL')
    if env_val is not None:
        try:
            return {'value': max(_INTERVAL_MIN, min(_INTERVAL_MAX, int(env_val))), 'source': 'environment'}
        except (TypeError, ValueError):
            pass
    return {'value': _INTERVAL_DEFAULT, 'source': 'default'}


def set_monitoring_interval(seconds: int, actor_id=None) -> None:
    """Validate and persist monitoring interval. Clamps to 10–3600."""
    from models.app_settings import AppSettings
    clamped = max(_INTERVAL_MIN, min(_INTERVAL_MAX, int(seconds)))
    AppSettings.set(_INTERVAL_KEY, str(clamped), actor_id=actor_id)


# ------------------------------------------------------------------ #
# Retention (read-only display — configured via env only)             #
# ------------------------------------------------------------------ #

def get_retention_info() -> dict:
    """Return retention policy values from environment (display only)."""
    return {
        'server_health_raw_days':    int(os.environ.get('SERVER_HEALTH_RAW_RETENTION_DAYS', 7)),
        'server_health_hourly_days': int(os.environ.get('SERVER_HEALTH_HOURLY_RETENTION_DAYS', 30)),
        'server_health_daily_days':  int(os.environ.get('SERVER_HEALTH_DAILY_RETENTION_DAYS', 365)),
    }
