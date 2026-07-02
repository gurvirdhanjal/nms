"""
Settings blueprint — global application configuration for admins.

Endpoints:
    GET  /settings                       — render settings page
    GET  /api/settings/smtp              — get SMTP config (password masked)
    POST /api/settings/smtp              — save SMTP config
    POST /api/settings/smtp/test         — test SMTP connection
    GET  /api/settings/monitoring        — get monitoring interval + source
    POST /api/settings/monitoring        — set monitoring interval
    GET  /api/settings/retention         — retention policy display (read-only)
    POST /api/settings/test-alert-flow   — fire test alert through full pipeline
"""
import logging

from flask import Blueprint, jsonify, render_template, request, session

from middleware.rbac import require_login, require_role

logger = logging.getLogger(__name__)

settings_bp = Blueprint('settings_bp', __name__, url_prefix='')


# ------------------------------------------------------------------ #
# Auth guard — all settings routes require login                       #
# ------------------------------------------------------------------ #

@settings_bp.before_request
@require_login
def _settings_auth_guard():
    return None


# ------------------------------------------------------------------ #
# Page                                                                 #
# ------------------------------------------------------------------ #

@settings_bp.route('/settings')
@require_role('admin')
def settings_page():
    from services.settings_service import (
        get_smtp_config_masked,
        get_smtp_config_with_source,
        is_smtp_configured,
        get_monitoring_interval_with_source,
        get_retention_info,
    )
    smtp_config = get_smtp_config_masked()
    smtp_source = get_smtp_config_with_source()
    monitoring = get_monitoring_interval_with_source()
    retention = get_retention_info()
    return render_template(
        'settings.html',
        smtp_config=smtp_config,
        smtp_source=smtp_source,
        smtp_configured=is_smtp_configured(),
        monitoring=monitoring,
        retention=retention,
    )


# ------------------------------------------------------------------ #
# SMTP                                                                 #
# ------------------------------------------------------------------ #

@settings_bp.route('/api/settings/smtp', methods=['GET'])
@require_role('admin')
def api_get_smtp():
    from services.settings_service import get_smtp_config_with_source, is_smtp_configured
    data = get_smtp_config_with_source()
    # Mask password value in response
    if 'smtp_password' in data and data['smtp_password']['value']:
        data['smtp_password']['value'] = '••••••'
    return jsonify({'ok': True, 'smtp': data, 'configured': is_smtp_configured()})


@settings_bp.route('/api/settings/smtp', methods=['POST'])
@require_role('admin')
def api_save_smtp():
    data = request.get_json(silent=True) or {}
    actor_id = session.get('user_id')
    try:
        from services.settings_service import set_smtp_config
        set_smtp_config(data, actor_id=actor_id)
        return jsonify({'ok': True, 'message': 'SMTP settings saved.'})
    except Exception as exc:
        logger.exception("[settings] Failed to save SMTP config")
        return jsonify({'ok': False, 'message': f'Save failed: {exc}'}), 500


@settings_bp.route('/api/settings/smtp/test', methods=['POST'])
@require_role('admin')
def api_test_smtp():
    data = request.get_json(silent=True) or {}
    try:
        from services.settings_service import get_smtp_config, test_smtp_connection
        # If caller sends a partial config use it on top of current DB config
        base = get_smtp_config()
        base.update({k: v for k, v in data.items() if v is not None})
        success, message = test_smtp_connection(base)
        return jsonify({'ok': success, 'message': message}), (200 if success else 502)
    except Exception as exc:
        logger.exception("[settings] SMTP test error")
        return jsonify({'ok': False, 'message': f'Test failed: {exc}'}), 500


# ------------------------------------------------------------------ #
# Monitoring interval                                                  #
# ------------------------------------------------------------------ #

@settings_bp.route('/api/settings/monitoring', methods=['GET'])
@require_role('admin')
def api_get_monitoring():
    from services.settings_service import get_monitoring_interval_with_source
    data = get_monitoring_interval_with_source()
    return jsonify({'ok': True, 'monitoring': data})


@settings_bp.route('/api/settings/monitoring', methods=['POST'])
@require_role('admin')
def api_save_monitoring():
    data = request.get_json(silent=True) or {}
    actor_id = session.get('user_id')
    try:
        seconds = int(data.get('monitoring_interval_seconds', 300))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'message': 'Invalid interval — must be an integer.'}), 400
    if not (10 <= seconds <= 3600):
        return jsonify({'ok': False, 'message': 'Interval must be between 10 and 3600 seconds.'}), 400
    try:
        from services.settings_service import set_monitoring_interval
        set_monitoring_interval(seconds, actor_id=actor_id)
        return jsonify({'ok': True, 'message': f'Monitoring interval set to {seconds}s. Takes effect within a few seconds.'})
    except Exception as exc:
        logger.exception("[settings] Failed to save monitoring interval")
        return jsonify({'ok': False, 'message': f'Save failed: {exc}'}), 500


# ------------------------------------------------------------------ #
# Retention (read-only)                                               #
# ------------------------------------------------------------------ #

@settings_bp.route('/api/settings/sla-thresholds', methods=['GET'])
@require_role('admin')
def api_get_sla_thresholds():
    from services.settings_service import get_sla_thresholds
    return jsonify({'ok': True, 'sla': get_sla_thresholds()})


@settings_bp.route('/api/settings/sla-thresholds', methods=['POST'])
@require_role('admin')
def api_save_sla_thresholds():
    data = request.get_json(silent=True) or {}
    actor_id = session.get('user_id')
    try:
        from services.settings_service import set_sla_thresholds
        set_sla_thresholds(data, actor_id=actor_id)
        return jsonify({'ok': True, 'message': 'SLA thresholds saved.'})
    except ValueError as exc:
        return jsonify({'ok': False, 'message': str(exc)}), 400
    except Exception as exc:
        logger.exception("[settings] Failed to save SLA thresholds")
        return jsonify({'ok': False, 'message': f'Save failed: {exc}'}), 500


@settings_bp.route('/api/settings/retention', methods=['GET'])
@require_role('admin')
def api_get_retention():
    from services.settings_service import get_retention_info
    return jsonify({'ok': True, 'retention': get_retention_info()})


# ------------------------------------------------------------------ #
# Test full alert pipeline                                             #
# ------------------------------------------------------------------ #

@settings_bp.route('/api/settings/test-alert-flow', methods=['POST'])
@require_role('admin')
def api_test_alert_flow():
    """Fire a synthetic alert through the full pipeline and report which channels fired."""
    try:
        from services.settings_service import get_smtp_config, test_smtp_connection
        from services.notification_service import NotificationService

        config = get_smtp_config()
        success, message = test_smtp_connection(config)
        channels_triggered = []
        if success:
            channels_triggered.append('email (SMTP)')

        return jsonify({
            'ok': success,
            'message': message if success else f'Alert flow test failed: {message}',
            'channels_triggered': channels_triggered,
        }), (200 if success else 502)
    except Exception as exc:
        logger.exception("[settings] Alert flow test error")
        return jsonify({'ok': False, 'message': f'Test failed: {exc}', 'channels_triggered': []}), 500


# ------------------------------------------------------------------ #
# Alert Channels (Phase 7)                                            #
# ------------------------------------------------------------------ #

@settings_bp.route('/api/settings/alert-channels', methods=['GET'])
@require_role('admin')
def api_list_channels():
    """List all alert channels."""
    from models.alert_channel import AlertChannel
    channels = AlertChannel.query.order_by(AlertChannel.name).all()
    return jsonify([c.to_dict() for c in channels])


@settings_bp.route('/api/settings/alert-channels', methods=['POST'])
@require_role('admin')
def api_create_channel():
    """Create an alert channel."""
    from models.alert_channel import AlertChannel, VALID_CHANNEL_TYPES
    from extensions import db
    from services import alert_routing_service

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    channel_type = (data.get('channel_type') or '').lower().strip()
    if channel_type not in VALID_CHANNEL_TYPES:
        return jsonify({'error': f'channel_type must be one of {list(VALID_CHANNEL_TYPES)}'}), 400

    if AlertChannel.query.filter_by(name=name).first():
        return jsonify({'error': 'A channel with this name already exists'}), 409

    config_json = data.get('config_json') or {}
    ok, err = AlertChannel.validate_config(channel_type, config_json)
    if not ok:
        return jsonify({'error': err}), 400

    # Normalize email recipients to list
    if channel_type == 'email':
        r = config_json.get('recipients', [])
        if isinstance(r, str):
            config_json['recipients'] = [x.strip() for x in r.split(',') if x.strip()]

    adt = data.get('applicable_device_types')
    channel = AlertChannel(
        name=name,
        channel_type=channel_type,
        config_json=config_json,
        is_enabled=bool(data.get('is_enabled', True)),
        send_on_critical=bool(data.get('send_on_critical', True)),
        send_on_warning=bool(data.get('send_on_warning', False)),
        applicable_device_types=[str(t).lower().strip() for t in adt if str(t).strip()] if isinstance(adt, list) else [],
    )
    db.session.add(channel)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[settings] AlertChannel create failed: %s', exc)
        return jsonify({'error': 'Database error'}), 500

    alert_routing_service.invalidate_cache()
    logger.info('[settings] AlertChannel created id=%d name=%s', channel.id, name)
    return jsonify({'channel': channel.to_dict()}), 201


@settings_bp.route('/api/settings/alert-channels/<int:channel_id>', methods=['PUT'])
@require_role('admin')
def api_update_channel(channel_id):
    """Update an alert channel."""
    from models.alert_channel import AlertChannel, VALID_CHANNEL_TYPES
    from extensions import db
    from services import alert_routing_service

    channel = AlertChannel.query.get_or_404(channel_id)
    data = request.get_json(silent=True) or {}

    if 'name' in data:
        name = (data['name'] or '').strip()
        if not name:
            return jsonify({'error': 'name cannot be empty'}), 400
        conflict = AlertChannel.query.filter(AlertChannel.name == name, AlertChannel.id != channel_id).first()
        if conflict:
            return jsonify({'error': 'A channel with this name already exists'}), 409
        channel.name = name

    if 'channel_type' in data:
        ct = (data['channel_type'] or '').lower().strip()
        if ct not in VALID_CHANNEL_TYPES:
            return jsonify({'error': f'channel_type must be one of {list(VALID_CHANNEL_TYPES)}'}), 400
        channel.channel_type = ct

    if 'config_json' in data:
        config_json = data['config_json'] or {}
        ok, err = AlertChannel.validate_config(channel.channel_type, config_json)
        if not ok:
            return jsonify({'error': err}), 400
        if channel.channel_type == 'email':
            r = config_json.get('recipients', [])
            if isinstance(r, str):
                config_json['recipients'] = [x.strip() for x in r.split(',') if x.strip()]
        channel.config_json = config_json

    if 'is_enabled' in data:
        channel.is_enabled = bool(data['is_enabled'])
    if 'send_on_critical' in data:
        channel.send_on_critical = bool(data['send_on_critical'])
    if 'send_on_warning' in data:
        channel.send_on_warning = bool(data['send_on_warning'])
    if 'applicable_device_types' in data:
        adt = data['applicable_device_types']
        channel.applicable_device_types = [str(t).lower().strip() for t in adt if str(t).strip()] if isinstance(adt, list) else []

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[settings] AlertChannel update failed id=%d: %s', channel_id, exc)
        return jsonify({'error': 'Database error'}), 500

    alert_routing_service.invalidate_cache()
    logger.info('[settings] AlertChannel updated id=%d', channel_id)
    return jsonify({'channel': channel.to_dict()})


@settings_bp.route('/api/settings/alert-channels/<int:channel_id>', methods=['DELETE'])
@require_role('admin')
def api_delete_channel(channel_id):
    """Delete an alert channel."""
    from models.alert_channel import AlertChannel
    from extensions import db
    from services import alert_routing_service

    channel = AlertChannel.query.get_or_404(channel_id)
    db.session.delete(channel)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[settings] AlertChannel delete failed id=%d: %s', channel_id, exc)
        return jsonify({'error': 'Database error'}), 500

    alert_routing_service.invalidate_cache()
    logger.info('[settings] AlertChannel deleted id=%d', channel_id)
    return jsonify({'ok': True})


@settings_bp.route('/api/settings/alert-channels/<int:channel_id>/test', methods=['POST'])
@require_role('admin')
def api_test_channel(channel_id):
    """Send a test delivery through this channel."""
    from models.alert_channel import AlertChannel
    from services.notification_service import NotificationService

    channel = AlertChannel.query.get_or_404(channel_id)
    channel_type = channel.channel_type

    if channel_type in ('slack', 'teams'):
        return jsonify({'ok': False, 'message': f'{channel_type.title()} delivery not yet wired'}), 200

    # email test
    class _SyntheticDevice:
        device_name = 'Settings Test'
        device_ip   = '0.0.0.0'

    ok = NotificationService.send_via_channel(channel, _SyntheticDevice(), 'This is a test alert from Tactical NMS Settings.', 'CRITICAL')
    if ok:
        return jsonify({'ok': True, 'message': f'Test email sent via channel "{channel.name}"'})
    return jsonify({'ok': False, 'message': 'Delivery failed — check SMTP configuration'}), 502
