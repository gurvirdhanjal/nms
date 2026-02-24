from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models.site import Site
from models.device import Device
from models.dashboard import DashboardEvent
from models.server_health import ServerHealthLog
from services.sites_service import SitesService
from datetime import datetime, timedelta
from sqlalchemy import func

sites_bp = Blueprint('sites', __name__)


# ============================================================================
# UI ROUTES
# ============================================================================

@sites_bp.route('/sites')
def sites_list_page():
    """Render the sites management page."""
    return render_template('sites/list.html')


@sites_bp.route('/sites/<int:site_id>/dashboard')
def site_dashboard(site_id):
    """Render the site dashboard page with statistics, alerts, metrics, and devices."""
    # Get site
    site = Site.query.get_or_404(site_id)
    
    # Get site statistics
    sites_service = SitesService()
    stats = sites_service.get_site_stats(site_id)
    
    # Get recent alerts for devices at this site (last 50, unresolved first)
    device_ids = [d.device_id for d in site.devices.all()]
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
    
    # Get all devices for the site
    devices = site.devices.order_by(Device.device_name).all()
    
    # Determine which devices are online (have recent health logs)
    online_device_ids = set()
    if device_ids:
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        online_results = db.session.query(ServerHealthLog.device_id).filter(
            ServerHealthLog.device_id.in_(device_ids),
            ServerHealthLog.timestamp >= cutoff
        ).distinct().all()
        online_device_ids = {d[0] for d in online_results}
    
    return render_template(
        'sites/dashboard.html',
        site=site,
        stats=stats,
        recent_alerts=recent_alerts,
        metrics=metrics,
        devices=devices,
        online_device_ids=online_device_ids
    )


# ============================================================================
# API ENDPOINTS
# ============================================================================

@sites_bp.route('/api/sites', methods=['GET'])

def list_sites():
    """List all sites with device counts."""
    sites = Site.query.order_by(Site.site_name).all()
    return jsonify({
        'status': 'ok',
        'data': [s.to_dict() for s in sites]
    })


@sites_bp.route('/api/sites', methods=['POST'])

def create_site():
    """Create a new site."""
    data = request.get_json()
    if not data or not data.get('site_name'):
        return jsonify({'status': 'error', 'message': 'site_name is required'}), 400

    # Check for duplicates
    existing = Site.query.filter_by(site_name=data['site_name']).first()
    if existing:
        return jsonify({'status': 'error', 'message': 'A site with that name already exists'}), 409

    site = Site(
        site_name=data['site_name'],
        site_code=data.get('site_code'),
        address=data.get('address'),
        timezone=data.get('timezone', 'UTC'),
        contact_name=data.get('contact_name'),
        contact_email=data.get('contact_email'),
        contact_phone=data.get('contact_phone'),
    )
    db.session.add(site)
    db.session.commit()

    return jsonify({'status': 'ok', 'data': site.to_dict()}), 201


@sites_bp.route('/api/sites/<int:site_id>', methods=['GET'])

def get_site(site_id):
    """Get a single site by ID."""
    site = Site.query.get_or_404(site_id)
    return jsonify({'status': 'ok', 'data': site.to_dict()})


@sites_bp.route('/api/sites/<int:site_id>', methods=['PUT'])

def update_site(site_id):
    """Update an existing site."""
    site = Site.query.get_or_404(site_id)
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    if 'site_name' in data:
        # Check uniqueness for new name
        dup = Site.query.filter(Site.site_name == data['site_name'], Site.id != site_id).first()
        if dup:
            return jsonify({'status': 'error', 'message': 'A site with that name already exists'}), 409
        site.site_name = data['site_name']

    for field in ('site_code', 'address', 'timezone', 'contact_name', 'contact_email', 'contact_phone'):
        if field in data:
            setattr(site, field, data[field])

    db.session.commit()
    return jsonify({'status': 'ok', 'data': site.to_dict()})


@sites_bp.route('/api/sites/<int:site_id>', methods=['DELETE'])

def delete_site(site_id):
    """Delete a site. Devices are unassigned (site_id set to NULL)."""
    site = Site.query.get_or_404(site_id)

    # Unassign devices from this site
    Device.query.filter_by(site_id=site_id).update({'site_id': None})

    db.session.delete(site)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': f'Site "{site.site_name}" deleted'})


@sites_bp.route('/api/sites/<int:site_id>/assign', methods=['POST'])

def assign_devices_to_site(site_id):
    """Assign one or more devices to a site."""
    site = Site.query.get_or_404(site_id)
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    updated = Device.query.filter(Device.device_id.in_(device_ids)).update(
        {'site_id': site_id}, synchronize_session='fetch'
    )
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'message': f'{updated} device(s) assigned to site "{site.site_name}"'
    })


@sites_bp.route('/api/devices/unassign-site', methods=['POST'])

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
