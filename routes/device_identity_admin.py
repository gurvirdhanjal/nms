from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from middleware.rbac import require_role
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from services.device_link_service import DeviceLinkService


device_identity_admin_bp = Blueprint('device_identity_admin_bp', __name__)


@device_identity_admin_bp.route('/admin/device-identity-links', methods=['GET'])
@require_role('admin')
def device_identity_links_page():
    candidates = (
        DeviceIdentityLinkCandidate.query.order_by(
            DeviceIdentityLinkCandidate.status.asc(),
            DeviceIdentityLinkCandidate.detected_at.desc(),
        )
        .limit(100)
        .all()
    )
    links = DeviceIdentityLink.query.order_by(DeviceIdentityLink.updated_at.desc()).limit(100).all()
    return render_template(
        'admin/device_identity_links.html',
        pending_candidates=[row.to_dict() for row in candidates],
        active_links=[row.to_dict() for row in links],
    )


@device_identity_admin_bp.route('/api/admin/device-identity-links', methods=['GET'])
@require_role('admin')
def list_device_identity_links():
    status = str(request.args.get('status') or '').strip().lower()
    mac = DeviceLinkService.normalized_mac(request.args.get('mac'))
    device_id = request.args.get('device_id', type=int)
    tracked_device_id = request.args.get('tracked_device_id', type=int)

    candidate_query = DeviceIdentityLinkCandidate.query
    link_query = DeviceIdentityLink.query

    if status in {'pending', 'confirmed', 'rejected'}:
        candidate_query = candidate_query.filter(DeviceIdentityLinkCandidate.status == status)
    elif status == 'active':
        link_query = link_query.filter(DeviceIdentityLink.is_active.is_(True))
    if mac:
        candidate_query = candidate_query.filter(DeviceIdentityLinkCandidate.normalized_mac == mac)
        link_query = link_query.filter(DeviceIdentityLink.normalized_mac == mac)
    if device_id:
        candidate_query = candidate_query.filter(DeviceIdentityLinkCandidate.device_id == int(device_id))
        link_query = link_query.filter(DeviceIdentityLink.device_id == int(device_id))
    if tracked_device_id:
        candidate_query = candidate_query.filter(DeviceIdentityLinkCandidate.tracked_device_id == int(tracked_device_id))
        link_query = link_query.filter(DeviceIdentityLink.tracked_device_id == int(tracked_device_id))

    return jsonify(
        {
            'success': True,
            'candidates': [row.to_dict() for row in candidate_query.order_by(DeviceIdentityLinkCandidate.detected_at.desc()).all()],
            'links': [row.to_dict() for row in link_query.order_by(DeviceIdentityLink.updated_at.desc()).all()],
        }
    )


@device_identity_admin_bp.route('/api/admin/device-identity-links', methods=['POST'])
@require_role('admin')
def decide_device_identity_link():
    payload = request.get_json(silent=True) or {}
    candidate_id = payload.get('candidate_id')
    action = str(payload.get('action') or '').strip().lower()
    reason = str(payload.get('reason') or '').strip() or None

    if candidate_id in (None, ''):
        return jsonify({'success': False, 'error': 'candidate_id is required'}), 400
    if action not in {'confirm', 'reject'}:
        return jsonify({'success': False, 'error': 'action must be confirm or reject'}), 400

    actor = str(session.get('username') or 'system').strip() or 'system'
    result = DeviceLinkService.decide_candidate(int(candidate_id), action, actor, reason)
    if hasattr(result, 'to_dict'):
        result_payload = result.to_dict()
    else:
        result_payload = {'candidate_id': int(candidate_id), 'status': action}
    return jsonify({'success': True, 'result': result_payload})
