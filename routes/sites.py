from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models.site import Site
from models.device import Device
from models.department import Department
from models.dashboard import DashboardEvent
from models.server_health import ServerHealthLog
from services.dashboard_availability import build_device_availability_snapshot
from services.sites_service import SitesService
from middleware.rbac import require_login, require_role
from datetime import datetime, timedelta
from sqlalchemy import func

sites_bp = Blueprint('sites', __name__)


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# ============================================================================
# UI ROUTES
# ============================================================================

@sites_bp.route('/sites')
@require_role('admin')
def sites_list_page():
    """Render the sites management page."""
    from middleware.rbac import scoped_query

    sites = scoped_query(Site).order_by(Site.site_name).all()
    return render_template(
        'sites/list.html',
        sites=sites,
        sites_payload=[site.to_dict() for site in sites],
    )


@sites_bp.route('/sites/<int:site_id>/dashboard')
@require_login
def site_dashboard(site_id):
    """Render the site dashboard page with statistics, alerts, metrics, and devices."""
    from middleware.rbac import scoped_query
    # Get site
    site = scoped_query(Site).get_or_404(site_id)
    
    sites_service = SitesService()
    all_site_devices = sites_service.get_site_devices(site_id)
    availability_snapshot = build_device_availability_snapshot(all_site_devices)
    stats = sites_service.get_site_stats(
        site_id,
        devices=all_site_devices,
        availability_snapshot=availability_snapshot,
    )
    
    # Get all devices for the site (directly assigned OR assigned via department)
    departments = site.departments.all() if hasattr(site, 'departments') else []
    device_ids = [d.device_id for d in all_site_devices]
    recent_alerts = []
    if device_ids:
        recent_alerts = DashboardEvent.query.filter(
            DashboardEvent.device_id.in_(device_ids)
        ).order_by(
            DashboardEvent.resolved.asc(),
            DashboardEvent.timestamp.desc()
        ).limit(50).all()
    
    # Get aggregate metrics for the site
    metrics = {
        'avg_cpu': 0,
        'avg_memory': 0,
        'avg_disk': 0,
        'avg_latency': 0,
        'avg_packet_loss': 0,
        'total_health_logs': 0
    }
    
    if device_ids:
        # Get recent health logs (last 24 hours)
        cutoff = datetime.utcnow() - timedelta(hours=24)
        
        # Calculate averages from recent health logs
        health_stats = db.session.query(
            func.avg(ServerHealthLog.cpu_usage).label('avg_cpu'),
            func.avg(ServerHealthLog.memory_usage).label('avg_memory'),
            func.avg(ServerHealthLog.disk_usage).label('avg_disk'),
            func.avg(ServerHealthLog.ping_latency_ms).label('avg_latency'),
            func.avg(ServerHealthLog.packet_loss_pct).label('avg_packet_loss'),
            func.count(ServerHealthLog.id).label('total_logs')
        ).filter(
            ServerHealthLog.device_id.in_(device_ids),
            ServerHealthLog.timestamp >= cutoff
        ).first()
        
        if health_stats:
            metrics['avg_cpu'] = health_stats.avg_cpu or 0
            metrics['avg_memory'] = health_stats.avg_memory or 0
            metrics['avg_disk'] = health_stats.avg_disk or 0
            metrics['avg_latency'] = health_stats.avg_latency or 0
            metrics['avg_packet_loss'] = health_stats.avg_packet_loss or 0
            metrics['total_health_logs'] = health_stats.total_logs or 0
    
    # Get all aggregated devices for the site
    devices = sorted(all_site_devices, key=lambda d: (d.device_name or "").lower())
    
    online_device_ids = set(availability_snapshot.get("online_device_ids") or set())

    # Devices per department (including unassigned)
    dept_stats_map = {}
    for dept in departments:
        dept_stats_map[dept.id] = {
            'name': dept.name,
            'device_count': 0,
            'online_count': 0,
            'offline_count': 0,
            'warning_count': 0
        }

    unassigned_bucket = {
        'name': 'Unassigned',
        'device_count': 0,
        'online_count': 0,
        'offline_count': 0,
        'warning_count': 0
    }

    for device in all_site_devices:
        if device.department_id and device.department_id in dept_stats_map:
            bucket = dept_stats_map[device.department_id]
        else:
            bucket = unassigned_bucket

        bucket['device_count'] += 1
        if device.device_id in online_device_ids:
            bucket['online_count'] += 1
        else:
            bucket['offline_count'] += 1

        if (
            (device.health_alert_strikes or 0) >= 2
            or (device.latency_strikes or 0) >= 2
            or (device.packet_loss_strikes or 0) >= 2
        ):
            bucket['warning_count'] += 1

    dept_device_stats = sorted(dept_stats_map.values(), key=lambda row: row['name'].lower())
    if unassigned_bucket['device_count'] > 0:
        dept_device_stats.append(unassigned_bucket)

    return render_template(
        'sites/dashboard.html',
        site=site,
        stats=stats,
        recent_alerts=recent_alerts,
        metrics=metrics,
        devices=devices,
        online_device_ids=online_device_ids,
        dept_device_stats=dept_device_stats
    )


# ============================================================================
# API ENDPOINTS
# ============================================================================

@sites_bp.route('/api/sites', methods=['GET'])
@require_login
def list_sites():
    """List all sites with device counts."""
    from middleware.rbac import scoped_query
    sites = scoped_query(Site).order_by(Site.site_name).all()
    return jsonify({
        'status': 'ok',
        'data': [s.to_dict() for s in sites]
    })


@sites_bp.route('/api/sites', methods=['POST'])
@require_role('admin')
def create_site():
    """Create a new site."""
    data = request.get_json() or {}
    site_name = _clean_text(data.get('site_name'))
    site_code = _clean_text(data.get('site_code'))

    if not site_name:
        return jsonify({'status': 'error', 'message': 'site_name is required'}), 400

    existing = Site.query.filter(
        db.func.lower(db.func.trim(Site.site_name)) == site_name.lower()
    ).first()
    if existing:
        return jsonify({'status': 'error', 'message': 'A site with that name already exists'}), 409

    if site_code:
        existing_code = Site.query.filter(
            db.func.lower(db.func.trim(Site.site_code)) == site_code.lower()
        ).first()
        if existing_code:
            return jsonify({'status': 'error', 'message': 'A site with that code already exists'}), 409

    site = Site(
        site_name=site_name,
        site_code=site_code,
        address=_clean_text(data.get('address')),
        timezone=_clean_text(data.get('timezone')) or 'UTC',
        contact_name=_clean_text(data.get('contact_name')),
        contact_email=_clean_text(data.get('contact_email')),
        contact_phone=_clean_text(data.get('contact_phone')),
    )
    db.session.add(site)
    db.session.commit()

    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='create',
        entity_type='site',
        entity_id=site.id,
        entity_name=site.site_name,
        description=f'Created site "{site.site_name}"'
    )

    return jsonify({'status': 'ok', 'data': site.to_dict()}), 201


@sites_bp.route('/api/sites/<int:site_id>', methods=['GET'])
@require_login
def get_site(site_id):
    """Get a single site by ID."""
    from middleware.rbac import scoped_query
    site = scoped_query(Site).get_or_404(site_id)
    return jsonify({'status': 'ok', 'data': site.to_dict()})


@sites_bp.route('/api/sites/<int:site_id>', methods=['PUT'])
@require_role('admin')
def update_site(site_id):
    """Update an existing site."""
    site = Site.query.get_or_404(site_id)
    data = request.get_json() or {}
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    if 'site_name' in data:
        site_name = _clean_text(data.get('site_name'))
        if not site_name:
            return jsonify({'status': 'error', 'message': 'site_name is required'}), 400

        dup = Site.query.filter(
            db.func.lower(db.func.trim(Site.site_name)) == site_name.lower(),
            Site.id != site_id
        ).first()
        if dup:
            return jsonify({'status': 'error', 'message': 'A site with that name already exists'}), 409
        site.site_name = site_name

    if 'site_code' in data:
        site_code = _clean_text(data.get('site_code'))
        if site_code:
            dup_code = Site.query.filter(
                db.func.lower(db.func.trim(Site.site_code)) == site_code.lower(),
                Site.id != site_id
            ).first()
            if dup_code:
                return jsonify({'status': 'error', 'message': 'A site with that code already exists'}), 409
        site.site_code = site_code

    for field in ('address', 'timezone', 'contact_name', 'contact_email', 'contact_phone'):
        if field in data:
            value = _clean_text(data[field])
            if field == 'timezone':
                value = value or 'UTC'
            setattr(site, field, value)

    db.session.commit()
    return jsonify({'status': 'ok', 'data': site.to_dict()})


@sites_bp.route('/api/sites/<int:site_id>', methods=['DELETE'])
@require_role('admin')
def delete_site(site_id):
    """Delete a site. Departments and device/user assignments are cleaned up."""
    site = Site.query.get_or_404(site_id)
    site_name = site.site_name  # Store before deletion

    dept_ids = [
        row[0]
        for row in db.session.query(Department.id).filter_by(site_id=site_id).all()
    ]
    if dept_ids:
        # Unassign users/devices from departments before deleting them
        from models.user import User
        User.query.filter(User.department_id.in_(dept_ids)).update(
            {'department_id': None}, synchronize_session='fetch'
        )
        Device.query.filter(Device.department_id.in_(dept_ids)).update(
            {'department_id': None}, synchronize_session='fetch'
        )
        Department.query.filter(Department.id.in_(dept_ids)).delete(synchronize_session='fetch')

    # Unassign devices from this site
    Device.query.filter_by(site_id=site_id).update({'site_id': None})

    db.session.delete(site)
    db.session.commit()
    
    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='delete',
        entity_type='site',
        entity_id=site_id,
        entity_name=site_name,
        description=f'Deleted site "{site_name}"' + (f' (departments removed: {len(dept_ids)})' if dept_ids else '')
    )
    
    message = f'Site "{site_name}" deleted'
    if dept_ids:
        message += f' (departments removed: {len(dept_ids)})'
    return jsonify({'status': 'ok', 'message': message})


@sites_bp.route('/api/sites/<int:site_id>/assign', methods=['POST'])
@require_role('admin')
def assign_devices_to_site(site_id):
    """Assign one or more devices to a site."""
    site = Site.query.get_or_404(site_id)
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    locked_rows = (
        db.session.query(Device.device_id)
        .join(Department, Device.department_id == Department.id)
        .filter(Device.device_id.in_(device_ids))
        .filter(Device.department_id.isnot(None))
        .filter(db.or_(Department.site_id.is_(None), Department.site_id != site_id))
        .all()
    )
    locked_ids = {row[0] for row in locked_rows}
    eligible_ids = [dev_id for dev_id in device_ids if dev_id not in locked_ids]

    updated = 0
    if eligible_ids:
        updated = Device.query.filter(Device.device_id.in_(eligible_ids)).update(
            {'site_id': site_id}, synchronize_session='fetch'
        )
        db.session.commit()

    skipped = len(locked_ids)
    message = f'{updated} device(s) assigned to site "{site.site_name}"'
    if skipped:
        message += f', {skipped} skipped due to department site mismatch'

    return jsonify({
        'status': 'ok',
        'message': message,
        'updated': updated,
        'skipped': skipped,
        'skipped_ids': list(locked_ids)[:20]
    })


@sites_bp.route('/api/devices/unassign-site', methods=['POST'])
@require_role('admin')
def unassign_devices_from_site():
    """Remove site assignment from one or more devices."""
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    updated = Device.query.filter(Device.device_id.in_(device_ids)).update(
        {'site_id': None}, synchronize_session='fetch'
    )
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'message': f'{updated} device(s) unassigned from their sites'
    })
