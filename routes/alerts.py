from flask import Blueprint, render_template, request, jsonify
from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.department import Department
from models.site import Site
from middleware.rbac import require_login
from datetime import datetime

alerts_bp = Blueprint('alerts', __name__)


@alerts_bp.route('/alerts')
@require_login
def alerts_page():
    site_id = request.args.get('site_id', type=int)
    sites = Site.query.order_by(Site.site_name).all()
    return render_template('alerts.html', sites=sites, selected_site_id=site_id)


@alerts_bp.route('/api/alerts')
@require_login
def alerts_json():
    site_id   = request.args.get('site_id',  type=int)
    dept_id   = request.args.get('dept_id',  type=int)
    severity  = request.args.get('severity')
    status    = request.args.get('status', 'all')   # active | resolved | all
    limit     = min(request.args.get('limit', 100, type=int), 500)
    offset    = request.args.get('offset', 0, type=int)

    q = DashboardEvent.query

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
    event = DashboardEvent.query.get_or_404(alert_id)
    if event.resolved:
        return jsonify({"error": "Already resolved"}), 409

    event.resolved    = True
    event.resolved_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "alert_id":    event.event_id,
        "resolved":    event.resolved,
        "resolved_at": event.resolved_at.isoformat(),
    })
