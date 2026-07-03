from flask import Blueprint, render_template, request, jsonify, session
from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.department import Department
from models.site import Site
from middleware.rbac import require_login, create_audit_log, current_role
from datetime import datetime

alerts_bp = Blueprint('alerts', __name__)

VALID_SEVERITIES = {'CRITICAL', 'WARNING', 'INFO', 'OK'}
VALID_STATUSES = {'active', 'resolved', 'all'}


@alerts_bp.route('/alerts')
@require_login
def alerts_page():
    site_id = request.args.get('site_id', type=int)
    sites = Site.query.order_by(Site.site_name).all()
    is_admin = current_role() == 'admin'
    return render_template('alerts.html', sites=sites, selected_site_id=site_id, is_admin=is_admin)


@alerts_bp.route('/api/alerts')
@require_login
def alerts_json():
    site_id   = request.args.get('site_id',  type=int)
    dept_id   = request.args.get('dept_id',  type=int)
    severity  = request.args.get('severity')
    status    = request.args.get('status', 'all')   # active | resolved | all
    limit     = min(request.args.get('limit', 100, type=int), 500)
    offset    = request.args.get('offset', 0, type=int)

    # Fix 3: Validate severity and status parameters
    if severity and severity.upper() not in VALID_SEVERITIES:
        return jsonify({"error": f"Invalid severity. Valid values: {sorted(VALID_SEVERITIES)}"}), 400
    if status not in VALID_STATUSES:
        return jsonify({"error": "status must be one of: active, resolved, all"}), 400

    q = DashboardEvent.query

    # Fix 2: Scope enforcement — non-admin users see only their own site/department
    role = current_role()
    if role != 'admin':
        user_site_id = session.get('site_id')
        user_dept_id = session.get('department_id')
        # Fallback: load from DB if session is missing scope keys
        if user_site_id is None and user_dept_id is None:
            user_id = session.get('user_id')
            if user_id:
                from models.user import User
                user = User.query.get(user_id)
                if user:
                    user_site_id = getattr(user, 'site_id', None)
                    user_dept_id = getattr(user, 'department_id', None)
        if role == 'manager' and user_site_id is not None:
            # Managers see all events for their site
            q = q.filter(DashboardEvent.site_id == user_site_id)
        elif user_dept_id is not None:
            # Operators/viewers see only their department's events
            q = q.filter(DashboardEvent.department_id == user_dept_id)
        else:
            # No scope assigned — show nothing for safety
            q = q.filter(False)

    if site_id:
        q = q.filter(DashboardEvent.site_id == site_id)
    if dept_id:
        q = q.filter(DashboardEvent.department_id == dept_id)
    if severity:
        q = q.filter(DashboardEvent.severity == severity.upper())
    if status == 'active':
        q = q.filter(DashboardEvent.resolved == False)  # noqa: E712
    elif status == 'resolved':
        q = q.filter(DashboardEvent.resolved == True)  # noqa: E712

    total = q.count()

    # active_count: when status='all', count unresolved in the filtered set
    if status == 'all':
        active_count = q.filter(DashboardEvent.resolved == False).count()  # noqa: E712
    elif status == 'active':
        active_count = total
    else:
        active_count = 0

    rows = q.order_by(DashboardEvent.timestamp.desc()).offset(offset).limit(limit).all()

    # Bulk-load device names to avoid N+1
    device_ids = list({r.device_id for r in rows if r.device_id})
    devices_by_id = (
        {d.device_id: d for d in Device.query.filter(Device.device_id.in_(device_ids)).all()}
        if device_ids
        else {}
    )
    dept_ids = list({d.department_id for d in devices_by_id.values() if d.department_id})
    depts_by_id = (
        {d.id: d for d in Department.query.filter(Department.id.in_(dept_ids)).all()}
        if dept_ids
        else {}
    )

    alerts_payload = []
    for ev in rows:
        dev = devices_by_id.get(ev.device_id)
        dept = depts_by_id.get(dev.department_id) if dev else None
        alerts_payload.append({
            "alert_id":    ev.event_id,
            "severity":    ev.severity,
            "device_id":   ev.device_id,
            "device_name": dev.device_name if dev else "Unknown",
            "device_ip":   ev.device_ip,
            "dept_name":   dept.name if dept else None,
            "metric_name": ev.metric_name,
            "message":     ev.message,
            "timestamp":   ev.timestamp.isoformat() if ev.timestamp else None,
            "resolved":    ev.resolved,
            "resolved_at": ev.resolved_at.isoformat() if ev.resolved_at else None,
        })

    return jsonify({
        "alerts":       alerts_payload,
        "total":        total,
        "active_count": active_count,
    })


@alerts_bp.route('/api/alerts/<string:alert_id>/resolve', methods=['PATCH'])
@require_login
def resolve_alert(alert_id: str):
    # Fix 4: Replace deprecated Query.get_or_404 with db.session.get
    event = db.session.get(DashboardEvent, alert_id)
    if event is None:
        return jsonify({"error": "Alert not found"}), 404

    if event.resolved:
        return jsonify({"error": "Already resolved"}), 409

    event.resolved    = True
    event.resolved_at = datetime.utcnow()
    # Fix 5: Match dashboard.py resolve behavior — append marker to message
    event.message = (event.message or '') + " [MANUALLY RESOLVED]"
    db.session.commit()

    # Fix 5: Audit log matching dashboard.py pattern
    device_name = event.device_ip or 'Unknown'
    if event.device_id:
        device = Device.query.get(event.device_id)
        if device:
            device_name = device.device_name or device.device_ip

    create_audit_log(
        action='resolve',
        entity_type='alert',
        entity_id=None,  # Alert IDs are UUIDs, not integers
        entity_name=f"{event.event_id[:8]} - {device_name} - {event.severity}",
        description=f"Alert resolved: {event.message[:100]}",
    )

    return jsonify({
        "alert_id":    event.event_id,
        "resolved":    event.resolved,
        "resolved_at": event.resolved_at.isoformat(),
    })


@alerts_bp.route('/api/alerts/device/<int:device_id>')
@require_login
def device_alert_history(device_id: int):
    """Alert history for a single device (used by the history modal)."""
    limit  = min(request.args.get('limit', 50, type=int), 200)
    offset = request.args.get('offset', 0, type=int)

    device = Device.query.get(device_id)
    if device is None:
        return jsonify({"error": "Device not found"}), 404

    # Scope enforcement — non-admins can only see their site/dept devices
    role = current_role()
    if role != 'admin':
        user_site_id = session.get('site_id')
        user_dept_id = session.get('department_id')
        if role == 'manager' and user_site_id is not None:
            if getattr(device, 'site_id', None) != user_site_id:
                return jsonify({"error": "Not found"}), 404
        elif user_dept_id is not None:
            if getattr(device, 'department_id', None) != user_dept_id:
                return jsonify({"error": "Not found"}), 404
        else:
            return jsonify({"error": "Not found"}), 404

    q = DashboardEvent.query.filter(DashboardEvent.device_id == device_id)
    total = q.count()
    events = q.order_by(DashboardEvent.timestamp.desc()).offset(offset).limit(limit).all()

    return jsonify({
        "device_id":   device_id,
        "device_name": device.device_name,
        "device_ip":   device.device_ip,
        "total":       total,
        "alerts": [
            {
                "alert_id":    ev.event_id,
                "severity":    ev.severity,
                "event_type":  ev.event_type,
                "metric_name": ev.metric_name,
                "message":     ev.message,
                "timestamp":   ev.timestamp.isoformat() if ev.timestamp else None,
                "resolved":    ev.resolved,
                "resolved_at": ev.resolved_at.isoformat() if ev.resolved_at else None,
            }
            for ev in events
        ],
    })
