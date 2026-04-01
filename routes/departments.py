from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models.department import Department
from models.device import Device
from models.site import Site
from models.user import User
from middleware.rbac import require_login, require_role, require_permission

departments_bp = Blueprint('departments', __name__)


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# ============================================================================
# UI ROUTES
# ============================================================================

@departments_bp.route('/departments')
@require_login
def departments_list_page():
    """Render the departments management page."""
    from middleware.rbac import scoped_query

    dept_rows = Department.get_all_with_counts(base_query=scoped_query(Department))
    departments = [row[0] for row in dept_rows]
    departments_payload = [
        row[0].to_dict(user_count=row[1], device_count=row[2]) for row in dept_rows
    ]
    sites = scoped_query(Site).order_by(Site.site_name).all()
    return render_template(
        'departments/list.html',
        departments=departments,
        departments_payload=departments_payload,
        sites=sites,
        sites_payload=[site.to_dict() for site in sites],
    )

@departments_bp.route('/departments/<int:dept_id>')
@require_login
def department_profile(dept_id):
    """View detailed department profile."""
    from middleware.rbac import scoped_query
    department = scoped_query(Department).get_or_404(dept_id)
    return render_template('departments/profile.html', department=department)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@departments_bp.route('/api/departments', methods=['GET'])
@require_login
def list_departments():
    """List all departments with device and user counts.

    Supports optional pagination via ?page=<n>&per_page=<n> (default: all).
    """
    from middleware.rbac import scoped_query
    dept_rows = Department.get_all_with_counts(base_query=scoped_query(Department))

    page = request.args.get('page', type=int)
    if page is not None:
        per_page = min(request.args.get('per_page', 50, type=int), 200)
        total = len(dept_rows)
        start = (page - 1) * per_page
        dept_rows = dept_rows[start: start + per_page]
        data = [row[0].to_dict(user_count=row[1], device_count=row[2]) for row in dept_rows]
        return jsonify({
            'status': 'ok',
            'data': data,
            'total': total,
            'page': page,
            'pages': max(1, (total + per_page - 1) // per_page),
        })

    return jsonify({
        'status': 'ok',
        'data': [row[0].to_dict(user_count=row[1], device_count=row[2]) for row in dept_rows]
    })


@departments_bp.route('/api/departments/<int:dept_id>', methods=['GET'])
@require_login
def get_department(dept_id):
    """Get a single department by ID."""
    from middleware.rbac import scoped_query
    department = scoped_query(Department).get_or_404(dept_id)
    return jsonify({'status': 'ok', 'data': department.to_dict()})


@departments_bp.route('/api/departments', methods=['POST'])
@require_role('admin', 'manager')
def create_department():
    """Create a new department."""
    data = request.get_json() or {}
    name = _clean_text(data.get('name'))

    if not name:
        return jsonify({'status': 'error', 'message': 'name is required'}), 400

    existing = Department.query.filter(
        db.func.lower(db.func.trim(Department.name)) == name.lower()
    ).first()
    if existing:
        return jsonify({'status': 'error', 'message': 'A department with that name already exists'}), 409

    department = Department(
        name=name,
        description=_clean_text(data.get('description')),
        site_id=data.get('site_id')
    )
    db.session.add(department)
    db.session.commit()

    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='create',
        entity_type='department',
        entity_id=department.id,
        entity_name=department.name,
        description=f'Created department "{department.name}"'
    )

    return jsonify({'status': 'ok', 'data': department.to_dict()}), 201


@departments_bp.route('/api/departments/<int:dept_id>', methods=['PUT'])
@require_role('admin', 'manager')
def update_department(dept_id):
    """Update an existing department."""
    from middleware.rbac import scoped_query
    department = scoped_query(Department).get_or_404(dept_id)
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    before_snapshot = department.to_dict()
    previous_site_id = department.site_id
    site_changed = False

    if 'name' in data:
        name = _clean_text(data.get('name'))
        if not name:
            return jsonify({'status': 'error', 'message': 'name is required'}), 400

        dup = Department.query.filter(
            db.func.lower(db.func.trim(Department.name)) == name.lower(),
            Department.id != dept_id
        ).first()
        if dup:
            return jsonify({'status': 'error', 'message': 'A department with that name already exists'}), 409
        department.name = name

    if 'description' in data:
        department.description = _clean_text(data.get('description'))
    
    if 'site_id' in data:
        raw_site_id = data.get('site_id')
        if raw_site_id in (None, ''):
            new_site_id = None
        else:
            try:
                new_site_id = int(raw_site_id)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'Invalid site_id'}), 400
        if previous_site_id != new_site_id:
            department.site_id = new_site_id
            site_changed = True

    if site_changed:
        Device.query.filter_by(department_id=dept_id).update(
            {'site_id': department.site_id}, synchronize_session='fetch'
        )

    db.session.commit()

    from middleware.rbac import create_audit_log
    from utils.audit_helpers import capture_model_diff
    create_audit_log(
        action='update',
        entity_type='department',
        entity_id=department.id,
        entity_name=department.name,
        description=f'Updated department "{department.name}"',
        changes=capture_model_diff(before_snapshot, department) or None,
    )

    return jsonify({'status': 'ok', 'data': department.to_dict()})


@departments_bp.route('/api/departments/<int:dept_id>', methods=['DELETE'])
@require_role('admin', 'manager')
def delete_department(dept_id):
    """Delete a department. Devices/users are unassigned automatically."""
    from middleware.rbac import scoped_query
    department = scoped_query(Department).get_or_404(dept_id)
    dept_name = department.name  # Store before deletion

    device_count = Device.query.filter_by(department_id=dept_id).count()
    user_count = User.query.filter_by(department_id=dept_id).count()

    if device_count:
        Device.query.filter_by(department_id=dept_id).update(
            {'department_id': None}, synchronize_session='fetch'
        )
    if user_count:
        User.query.filter_by(department_id=dept_id).update(
            {'department_id': None}, synchronize_session='fetch'
        )

    db.session.delete(department)
    db.session.commit()
    
    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='delete',
        entity_type='department',
        entity_id=dept_id,
        entity_name=dept_name,
        description=f'Deleted department "{dept_name}"' + (f' (unassigned devices={device_count}, users={user_count})' if device_count or user_count else '')
    )
    
    message = f'Department "{dept_name}" deleted'
    if device_count or user_count:
        message += f' (unassigned devices={device_count}, users={user_count})'
    return jsonify({'status': 'ok', 'message': message})


@departments_bp.route('/api/departments/<int:dept_id>/assign', methods=['POST'])
@require_permission('devices.edit')
def assign_devices_to_department(dept_id):
    """Assign one or more devices to a department."""
    from middleware.rbac import scoped_query
    from models.subnet import Subnet
    
    department = scoped_query(Department).get_or_404(dept_id)
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    # CRITICAL FIX: Validate that devices belong to subnets mapped to the department's site
    if department.site_id:
        devices_to_assign = Device.query.filter(Device.device_id.in_(device_ids)).all()
        invalid_devices = []
        
        for device in devices_to_assign:
            if device.device_ip and not Subnet.is_ip_in_site_subnets(device.device_ip, department.site_id):
                invalid_devices.append({
                    'device_id': device.device_id,
                    'device_ip': device.device_ip,
                    'device_name': device.device_name
                })
        
        if invalid_devices:
            return jsonify({
                'status': 'error',
                'message': f'{len(invalid_devices)} device(s) do not belong to subnets mapped to this site',
                'invalid_devices': invalid_devices
            }), 400

    # Proceed with assignment
    updated = Device.query.filter(Device.device_id.in_(device_ids)).update(
        {'department_id': dept_id, 'site_id': department.site_id}, synchronize_session='fetch'
    )
    db.session.commit()

    # Audit logging
    from middleware.rbac import create_audit_log
    create_audit_log(
        action='assign',
        entity_type='department',
        entity_id=dept_id,
        entity_name=department.name,
        description=f'Assigned {updated} device(s) to department "{department.name}"'
    )

    return jsonify({
        'status': 'ok',
        'message': f'{updated} device(s) assigned to department "{department.name}"'
    })


@departments_bp.route('/api/departments/<int:dept_id>/assignable-devices', methods=['GET'])
@require_permission('devices.view')
def get_assignable_devices_for_department(dept_id):
    """
    Get list of devices that can be assigned to this department.
    Only returns devices from subnets mapped to the department's site.
    """
    from middleware.rbac import scoped_query
    from models.subnet import Subnet
    
    department = scoped_query(Department).get_or_404(dept_id)
    
    # If department has no site, return all unassigned devices
    if not department.site_id:
        devices = Device.query.filter(
            db.or_(
                Device.department_id == None,
                Device.department_id == dept_id
            )
        ).all()
        return jsonify({
            'status': 'ok',
            'data': [d.to_dict() for d in devices],
            'warning': 'Department has no site assigned. Showing all devices.'
        })
    
    # Get subnets for this site
    site_subnets = Subnet.get_subnets_for_site(department.site_id)
    
    if not site_subnets:
        return jsonify({
            'status': 'ok',
            'data': [],
            'warning': f'No subnets mapped to site ID {department.site_id}. Please map subnets first.'
        })
    
    # Build list of CIDR blocks
    subnet_cidrs = [s.cidr for s in site_subnets]
    
    # Get all devices (unassigned or already in this department)
    all_devices = Device.query.filter(
        db.or_(
            Device.department_id == None,
            Device.department_id == dept_id
        )
    ).all()
    
    # Filter devices by subnet membership
    assignable_devices = []
    for device in all_devices:
        if device.device_ip:
            for subnet in site_subnets:
                if subnet.contains_ip(device.device_ip):
                    assignable_devices.append(device)
                    break
    
    return jsonify({
        'status': 'ok',
        'data': [d.to_dict() for d in assignable_devices],
        'site_subnets': subnet_cidrs,
        'total_filtered': len(all_devices),
        'total_assignable': len(assignable_devices)
    })



@departments_bp.route('/api/devices/unassign-department', methods=['POST'])
@require_permission('devices.edit')
def unassign_devices_from_department():
    """Remove department assignment from one or more devices."""
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    updated = Device.query.filter(Device.device_id.in_(device_ids)).update(
        {'department_id': None}, synchronize_session='fetch'
    )
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'message': f'{updated} device(s) unassigned from their departments'
    })
