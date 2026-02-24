from flask import Blueprint, request, jsonify, render_template
from extensions import db
from models.department import Department
from models.device import Device
from models.user import User

departments_bp = Blueprint('departments', __name__)


# ============================================================================
# UI ROUTES
# ============================================================================

@departments_bp.route('/departments')
def departments_list_page():
    """Render the departments management page."""
    return render_template('departments/list.html')


# ============================================================================
# API ENDPOINTS
# ============================================================================

@departments_bp.route('/api/departments', methods=['GET'])
def list_departments():
    """List all departments with device and user counts."""
    departments = Department.query.order_by(Department.name).all()
    return jsonify({
        'status': 'ok',
        'data': [d.to_dict() for d in departments]
    })


@departments_bp.route('/api/departments/<int:dept_id>', methods=['GET'])
def get_department(dept_id):
    """Get a single department by ID."""
    department = Department.query.get_or_404(dept_id)
    return jsonify({'status': 'ok', 'data': department.to_dict()})


@departments_bp.route('/api/departments', methods=['POST'])
def create_department():
    """Create a new department."""
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'status': 'error', 'message': 'name is required'}), 400

    # Check for duplicates
    existing = Department.query.filter_by(name=data['name']).first()
    if existing:
        return jsonify({'status': 'error', 'message': 'A department with that name already exists'}), 409

    department = Department(
        name=data['name'],
        description=data.get('description'),
        site_id=data.get('site_id')
    )
    db.session.add(department)
    db.session.commit()

    return jsonify({'status': 'ok', 'data': department.to_dict()}), 201


@departments_bp.route('/api/departments/<int:dept_id>', methods=['PUT'])
def update_department(dept_id):
    """Update an existing department."""
    department = Department.query.get_or_404(dept_id)
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    if 'name' in data:
        # Check uniqueness for new name
        dup = Department.query.filter(Department.name == data['name'], Department.id != dept_id).first()
        if dup:
            return jsonify({'status': 'error', 'message': 'A department with that name already exists'}), 409
        department.name = data['name']

    if 'description' in data:
        department.description = data['description']
    
    if 'site_id' in data:
        department.site_id = data['site_id']

    db.session.commit()
    return jsonify({'status': 'ok', 'data': department.to_dict()})


@departments_bp.route('/api/departments/<int:dept_id>', methods=['DELETE'])
def delete_department(dept_id):
    """Delete a department. Returns 409 if devices or users are assigned."""
    department = Department.query.get_or_404(dept_id)

    # Check if any devices are assigned
    device_count = Device.query.filter_by(department_id=dept_id).count()
    if device_count > 0:
        return jsonify({
            'status': 'error',
            'message': f'Cannot delete department: {device_count} device(s) are assigned to it'
        }), 409

    # Check if any users are assigned
    user_count = User.query.filter_by(department_id=dept_id).count()
    if user_count > 0:
        return jsonify({
            'status': 'error',
            'message': f'Cannot delete department: {user_count} user(s) are assigned to it'
        }), 409

    db.session.delete(department)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': f'Department "{department.name}" deleted'})


@departments_bp.route('/api/departments/<int:dept_id>/assign', methods=['POST'])
def assign_devices_to_department(dept_id):
    """Assign one or more devices to a department."""
    department = Department.query.get_or_404(dept_id)
    data = request.get_json()
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify({'status': 'error', 'message': 'device_ids array is required'}), 400

    updated = Device.query.filter(Device.device_id.in_(device_ids)).update(
        {'department_id': dept_id}, synchronize_session='fetch'
    )
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'message': f'{updated} device(s) assigned to department "{department.name}"'
    })


@departments_bp.route('/api/devices/unassign-department', methods=['POST'])
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
