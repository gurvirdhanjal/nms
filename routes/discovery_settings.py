from flask import Blueprint, render_template, request, jsonify, current_app, session
from extensions import db
from models.discovery_config import get_config
from middleware.rbac import require_login, require_role

discovery_settings_bp = Blueprint(
    "discovery_settings_bp", __name__, url_prefix=""
)


@discovery_settings_bp.before_request
@require_login
def _discovery_settings_auth_guard():
    return None


@discovery_settings_bp.route("/discovery-settings")
@require_role('admin')
def settings_page():
    return render_template("discovery_settings.html")


# ------------------------------------------------------------------ #
# REST API
# ------------------------------------------------------------------ #

@discovery_settings_bp.route("/api/discovery-settings", methods=["GET"])
def get_settings():
    cfg = get_config()
    return jsonify(cfg.to_dict())


@discovery_settings_bp.route("/api/discovery-settings", methods=["POST"])
@require_role('admin')
def update_settings():
    data = request.get_json(silent=True) or {}

    cfg = get_config()
    
    # Track changes for audit log
    changes = {}
    
    if "enabled" in data:
        old_value = cfg.enabled
        new_value = bool(data["enabled"])
        if old_value != new_value:
            changes['enabled'] = {'before': old_value, 'after': new_value}
        cfg.enabled = new_value
    if "subnets" in data:
        old_value = cfg.subnets
        new_value = data["subnets"]
        if old_value != new_value:
            changes['subnets'] = {'before': old_value, 'after': new_value}
        cfg.subnets = new_value  # expects a list
    if "heavy_interval_min" in data:
        old_value = cfg.heavy_interval_min
        new_value = max(1, int(data["heavy_interval_min"]))
        if old_value != new_value:
            changes['heavy_interval_min'] = {'before': old_value, 'after': new_value}
        cfg.heavy_interval_min = new_value
    if "max_concurrent_pings" in data:
        old_value = cfg.max_concurrent_pings
        new_value = max(1, min(200, int(data["max_concurrent_pings"])))
        if old_value != new_value:
            changes['max_concurrent_pings'] = {'before': old_value, 'after': new_value}
        cfg.max_concurrent_pings = new_value
    if "ping_timeout" in data:
        old_value = cfg.ping_timeout
        new_value = max(1, min(10, int(data["ping_timeout"])))
        if old_value != new_value:
            changes['ping_timeout'] = {'before': old_value, 'after': new_value}
        cfg.ping_timeout = new_value
    if "auto_add_policy" in data and data["auto_add_policy"] in ("auto", "approval"):
        old_value = cfg.auto_add_policy
        new_value = data["auto_add_policy"]
        if old_value != new_value:
            changes['auto_add_policy'] = {'before': old_value, 'after': new_value}
        cfg.auto_add_policy = new_value
    if "auto_add_after_n" in data:
        old_value = cfg.auto_add_after_n
        new_value = max(1, int(data["auto_add_after_n"]))
        if old_value != new_value:
            changes['auto_add_after_n'] = {'before': old_value, 'after': new_value}
        cfg.auto_add_after_n = new_value
    if "auto_monitor_new" in data:
        old_value = cfg.auto_monitor_new
        new_value = bool(data["auto_monitor_new"])
        if old_value != new_value:
            changes['auto_monitor_new'] = {'before': old_value, 'after': new_value}
        cfg.auto_monitor_new = new_value

    db.session.commit()
    
    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='update',
        entity_type='discovery_settings',
        entity_id=cfg.id,
        entity_name='Discovery Settings',
        description='Updated discovery settings configuration',
        changes=changes if changes else None
    )

    # Auto-run discovery scan on save when enabled so subnet devices are classified and persisted.
    auto_scan_triggered = False
    queued_subnets = 0
    if cfg.enabled and isinstance(cfg.subnets, list) and cfg.subnets:
        from services.discovery_service import get_discovery_service

        svc = get_discovery_service()
        app = current_app._get_current_object()
        username = str(session.get('username') or 'system')
        queued_subnets = svc.trigger_settings_subnet_scan(cfg.subnets, username=username, app=app)
        auto_scan_triggered = queued_subnets > 0

    return jsonify({
        "success": True,
        "config": cfg.to_dict(),
        "auto_scan_triggered": auto_scan_triggered,
        "queued_subnets": queued_subnets,
    })


# ------------------------------------------------------------------ #
# Manual triggers
# ------------------------------------------------------------------ #

@discovery_settings_bp.route("/api/discovery-settings/trigger-heavy", methods=["POST"])
@require_role('admin')
def trigger_heavy():
    from services.auto_discovery_service import get_auto_discovery_service
    app = current_app._get_current_object()
    svc = get_auto_discovery_service()
    svc.trigger_heavy_scan(app)
    return jsonify({"success": True, "message": "Enrichment scan triggered in background."})
