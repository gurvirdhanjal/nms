import logging

from flask import Blueprint, jsonify, render_template, request, session

from extensions import db
from middleware.rbac import require_login, require_role
from models.compliance_profile import ComplianceProfile

logger = logging.getLogger(__name__)

compliance_profiles_bp = Blueprint('compliance_profiles_bp', __name__)

_ALLOWED_RULE_KEYS = {
    'cpu_warning', 'cpu_critical',
    'memory_warning', 'memory_critical',
    'disk_warning', 'disk_critical',
}

_GLOBAL_FALLBACK = {
    'cpu_warning': 80.0, 'cpu_critical': 90.0,
    'mem_warning': 75.0, 'mem_critical': 95.0,
    'disk_warning': 90.0, 'disk_critical': 95.0,
}


# ── Admin page ────────────────────────────────────────────────────────────────

@compliance_profiles_bp.route('/admin/compliance-profiles')
@require_role('admin')
def compliance_profiles_page():
    profiles = ComplianceProfile.query.order_by(ComplianceProfile.name).all()

    try:
        from services.server_thresholds import get_merged_thresholds
        m = get_merged_thresholds().get('metrics', {})
        global_defaults = {
            'cpu_warning':  m.get('cpu_usage_pct',    {}).get('warning',  _GLOBAL_FALLBACK['cpu_warning']),
            'cpu_critical': m.get('cpu_usage_pct',    {}).get('critical', _GLOBAL_FALLBACK['cpu_critical']),
            'mem_warning':  m.get('memory_usage_pct', {}).get('warning',  _GLOBAL_FALLBACK['mem_warning']),
            'mem_critical': m.get('memory_usage_pct', {}).get('critical', _GLOBAL_FALLBACK['mem_critical']),
            'disk_warning': m.get('disk_usage_pct',   {}).get('warning',  _GLOBAL_FALLBACK['disk_warning']),
            'disk_critical':m.get('disk_usage_pct',   {}).get('critical', _GLOBAL_FALLBACK['disk_critical']),
        }
    except Exception:
        logger.warning('[ComplianceProfiles] Could not load global thresholds; using hardcoded fallback')
        global_defaults = dict(_GLOBAL_FALLBACK)

    return render_template('admin/compliance_profiles.html',
                           profiles=profiles,
                           global_defaults=global_defaults)


# ── API: list ─────────────────────────────────────────────────────────────────

@compliance_profiles_bp.route('/api/compliance-profiles/assigned-count')
@require_login
def api_assigned_count():
    from models.device import Device
    count = Device.query.filter(Device.compliance_profile_id.isnot(None)).count()
    return jsonify({'count': count})


@compliance_profiles_bp.route('/api/compliance-profiles')
@require_login
def api_list_profiles():
    from models.device import Device
    from sqlalchemy import func
    counts = dict(
        db.session.query(
            Device.compliance_profile_id,
            func.count(Device.device_id),
        ).filter(
            Device.compliance_profile_id.isnot(None)
        ).group_by(Device.compliance_profile_id).all()
    )
    profiles = ComplianceProfile.query.order_by(ComplianceProfile.name).all()
    result = []
    for p in profiles:
        d = p.to_dict()
        d['device_count'] = counts.get(p.id, 0)
        result.append(d)
    return jsonify(result)


@compliance_profiles_bp.route('/api/compliance-profiles/<int:profile_id>/devices')
@require_login
def api_profile_devices(profile_id):
    ComplianceProfile.query.get_or_404(profile_id)
    from models.device import Device
    query = Device.query.filter_by(compliance_profile_id=profile_id)
    user_role = str(session.get('role') or '').strip().lower()
    user_site_id = session.get('site_id')
    if user_role != 'admin' and user_site_id:
        query = query.filter(Device.site_id == user_site_id)
    devices = query.order_by(Device.device_name).all()
    return jsonify([
        {
            'device_id':   d.device_id,
            'device_name': d.device_name,
            'device_ip':   d.device_ip,
            'device_type': d.device_type,
        }
        for d in devices
    ])


# ── API: create ───────────────────────────────────────────────────────────────

@compliance_profiles_bp.route('/api/compliance-profiles', methods=['POST'])
@require_role('admin')
def api_create_profile():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    if ComplianceProfile.query.filter_by(name=name).first():
        return jsonify({'success': False, 'error': 'A profile with this name already exists'}), 409

    profile = ComplianceProfile(
        name=name,
        description=(data.get('description') or '').strip() or None,
        rules_json=_parse_rules(data.get('rules_json') or {}),
    )
    db.session.add(profile)
    db.session.commit()
    logger.info('[Compliance] Created profile id=%d name=%s', profile.id, name)
    return jsonify({'success': True, 'profile': profile.to_dict()}), 201


# ── API: update ───────────────────────────────────────────────────────────────

@compliance_profiles_bp.route('/api/compliance-profiles/<int:profile_id>', methods=['PUT'])
@require_role('admin')
def api_update_profile(profile_id):
    profile = ComplianceProfile.query.get_or_404(profile_id)
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    if name and name != profile.name:
        conflict = ComplianceProfile.query.filter(
            ComplianceProfile.name == name,
            ComplianceProfile.id != profile_id,
        ).first()
        if conflict:
            return jsonify({'success': False, 'error': 'A profile with this name already exists'}), 409
        profile.name = name

    if 'description' in data:
        profile.description = (data['description'] or '').strip() or None
    if 'rules_json' in data:
        profile.rules_json = _parse_rules(data.get('rules_json') or {})

    db.session.commit()
    logger.info('[Compliance] Updated profile id=%d', profile_id)
    return jsonify({'success': True, 'profile': profile.to_dict()})


# ── API: delete ───────────────────────────────────────────────────────────────

@compliance_profiles_bp.route('/api/compliance-profiles/<int:profile_id>', methods=['DELETE'])
@require_role('admin')
def api_delete_profile(profile_id):
    profile = ComplianceProfile.query.get_or_404(profile_id)

    # Unlink any devices assigned to this profile before removing it
    from models.device import Device
    Device.query.filter_by(compliance_profile_id=profile_id).update(
        {'compliance_profile_id': None}
    )

    db.session.delete(profile)
    db.session.commit()
    logger.info('[Compliance] Deleted profile id=%d', profile_id)
    return jsonify({'success': True})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_rules(raw):
    """Return only valid threshold rules with values in [0, 100]."""
    rules = {}
    for key in _ALLOWED_RULE_KEYS:
        if key in raw and raw[key] is not None:
            try:
                val = float(raw[key])
                if 0.0 <= val <= 100.0:
                    rules[key] = val
            except (TypeError, ValueError):
                pass
    return rules
