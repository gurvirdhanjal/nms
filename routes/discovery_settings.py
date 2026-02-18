from flask import Blueprint, render_template, request, jsonify, current_app
from extensions import db
from models.discovery_config import get_config
from middleware.rbac import require_login

discovery_settings_bp = Blueprint(
    "discovery_settings_bp", __name__, url_prefix=""
)


@discovery_settings_bp.before_request
@require_login
def _discovery_settings_auth_guard():
    return None


@discovery_settings_bp.route("/discovery-settings")
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
def update_settings():
    data = request.get_json(silent=True) or {}

    cfg = get_config()

    if "enabled" in data:
        cfg.enabled = bool(data["enabled"])
    if "subnets" in data:
        cfg.subnets = data["subnets"]  # expects a list
    if "heavy_interval_min" in data:
        cfg.heavy_interval_min = max(1, int(data["heavy_interval_min"]))
    if "max_concurrent_pings" in data:
        cfg.max_concurrent_pings = max(1, min(200, int(data["max_concurrent_pings"])))
    if "ping_timeout" in data:
        cfg.ping_timeout = max(1, min(10, int(data["ping_timeout"])))
    if "auto_add_policy" in data and data["auto_add_policy"] in ("auto", "approval"):
        cfg.auto_add_policy = data["auto_add_policy"]
    if "auto_add_after_n" in data:
        cfg.auto_add_after_n = max(1, int(data["auto_add_after_n"]))
    if "auto_monitor_new" in data:
        cfg.auto_monitor_new = bool(data["auto_monitor_new"])

    db.session.commit()

    return jsonify({"success": True, "config": cfg.to_dict()})


# ------------------------------------------------------------------ #
# Manual triggers
# ------------------------------------------------------------------ #

@discovery_settings_bp.route("/api/discovery-settings/trigger-heavy", methods=["POST"])
def trigger_heavy():
    from services.auto_discovery_service import get_auto_discovery_service
    app = current_app._get_current_object()
    svc = get_auto_discovery_service()
    svc.trigger_heavy_scan(app)
    return jsonify({"success": True, "message": "Auto scan triggered in background."})
