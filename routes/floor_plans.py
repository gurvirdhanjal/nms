"""Floor-plan (plant-map) geotagging API.

A super admin uploads one or more floor-plan images per site and places devices
on them by relative (percent) coordinates. Every logged-in user can view a plan
with live device status overlaid (the front-end polls the existing
/api/monitoring/status endpoint in a single batched request for the whole floor).

Routes:
  POST   /api/sites/<site_id>/floor-plans   admin  upload + create plan
  GET    /api/sites/<site_id>/floor-plans   login  list plans for a site
  GET    /api/floor-plans/<id>              login  plan metadata + placed devices
  GET    /api/floor-plans/<id>/image        login  serve the normalised PNG
  PUT    /api/floor-plans/<id>              admin  rename / reorder / replace image
  DELETE /api/floor-plans/<id>              admin  delete plan + clear placements
  POST   /api/floor-plans/<id>/placements   admin  bulk upsert device placements
"""
from flask import Blueprint, request, jsonify, send_file, session
from sqlalchemy import func

from extensions import db
from models.site import Site
from models.device import Device
from models.floor_plan import FloorPlan
from services.floor_plan_service import (
    normalise_upload,
    image_path,
    delete_image,
    FloorPlanError,
)
from services.sites_service import SitesService
from middleware.rbac import require_login, require_role, scoped_query, create_audit_log

floor_plans_bp = Blueprint('floor_plans', __name__)


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _placed_counts(plan_ids):
    """Return {floor_plan_id: device_count} for the given plans in one query."""
    if not plan_ids:
        return {}
    rows = (
        db.session.query(Device.floor_plan_id, func.count(Device.device_id))
        .filter(Device.floor_plan_id.in_(plan_ids))
        .group_by(Device.floor_plan_id)
        .all()
    )
    return {pid: cnt for pid, cnt in rows}


def _get_site_or_404(site_id):
    """Site fetch respecting RBAC scope (raises 404 if out of scope)."""
    return scoped_query(Site).get_or_404(site_id)


def _get_plan_or_404(plan_id):
    """Plan fetch whose parent site is in the caller's RBAC scope."""
    plan = FloorPlan.query.get_or_404(plan_id)
    # Ensure the plan's site is visible to this user.
    scoped_query(Site).filter(Site.id == plan.site_id).first_or_404()
    return plan


def _device_to_marker(device):
    return {
        'device_id': device.device_id,
        'device_name': device.device_name,
        'device_ip': device.device_ip,
        'device_type': device.device_type,
        'map_x': device.map_x,
        'map_y': device.map_y,
        'map_rotation': device.map_rotation,
        'map_locked': bool(device.map_locked),
        'connection_type': device.connection_type,
    }


# ============================================================================
# UPLOAD / LIST
# ============================================================================

@floor_plans_bp.route('/api/sites/<int:site_id>/floor-plans', methods=['POST'])
@require_role('admin')
def create_floor_plan(site_id):
    """Upload a new floor plan for a site (multipart: file, name)."""
    site = _get_site_or_404(site_id)

    file_storage = request.files.get('file')
    if file_storage is None or not file_storage.filename:
        return jsonify({'status': 'error', 'message': 'A plan image file is required'}), 400

    name = _clean_text(request.form.get('name')) or 'Floor Plan'

    try:
        norm = normalise_upload(file_storage)
    except FloorPlanError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    next_order = (
        db.session.query(func.coalesce(func.max(FloorPlan.sort_order), -1))
        .filter(FloorPlan.site_id == site_id)
        .scalar()
    ) + 1

    plan = FloorPlan(
        site_id=site.id,
        name=name,
        sort_order=next_order,
        version=1,
        created_by=session.get('username'),
        **norm,
    )
    db.session.add(plan)
    db.session.commit()

    create_audit_log(
        action='create',
        entity_type='floor_plan',
        entity_id=plan.id,
        entity_name=plan.name,
        description=f'Uploaded floor plan "{plan.name}" to site "{site.site_name}"',
    )

    return jsonify({'status': 'ok', 'data': plan.to_dict(device_count=0)}), 201


@floor_plans_bp.route('/api/sites/<int:site_id>/placeable-devices', methods=['GET'])
@require_login
def list_placeable_devices(site_id):
    """All devices belonging to a site, with their current placement.

    The map UI uses this to build the 'unplaced devices' tray (floor_plan_id is
    null) and to know which devices already sit on another plan.
    """
    _get_site_or_404(site_id)
    devices = SitesService().get_site_devices(site_id)
    return jsonify({
        'status': 'ok',
        'data': [
            {
                'device_id': d.device_id,
                'device_name': d.device_name,
                'device_ip': d.device_ip,
                'device_type': d.device_type,
                'floor_plan_id': d.floor_plan_id,
                'connection_type': d.connection_type,
            }
            for d in devices
        ],
    })


@floor_plans_bp.route('/api/sites/<int:site_id>/floor-plans', methods=['GET'])
@require_login
def list_floor_plans(site_id):
    """List floor plans for a site, ordered by sort_order."""
    _get_site_or_404(site_id)
    plans = (
        FloorPlan.query.filter_by(site_id=site_id)
        .order_by(FloorPlan.sort_order, FloorPlan.id)
        .all()
    )
    counts = _placed_counts([p.id for p in plans])
    return jsonify({
        'status': 'ok',
        'data': [p.to_dict(device_count=counts.get(p.id, 0)) for p in plans],
    })


@floor_plans_bp.route('/api/floor-plans/<int:plan_id>/suggestions', methods=['GET'])
@require_login
def placement_suggestions(plan_id):
    """SNMP-topology accelerator.

    For each switch already placed on this plan, surface its UNPLACED downstream
    devices (Device.parent_switch_id) together with the switch's marker
    coordinates, so the UI can offer to cluster them near their switch. Built
    entirely on existing discovery data (parent_switch_id) — no new probing.
    """
    plan = _get_plan_or_404(plan_id)

    placed_switches = (
        Device.query.filter_by(floor_plan_id=plan_id)
        .filter(Device.map_x.isnot(None))
        .all()
    )
    suggestions = []
    for switch in placed_switches:
        children = (
            Device.query.filter_by(parent_switch_id=switch.device_id)
            .filter(Device.floor_plan_id.is_(None))
            .order_by(Device.device_name)
            .all()
        )
        for child in children:
            suggestions.append({
                'device_id': child.device_id,
                'device_name': child.device_name,
                'connection_type': child.connection_type,
                'parent_switch_id': switch.device_id,
                'parent_switch_name': switch.device_name,
                'switch_x': switch.map_x,
                'switch_y': switch.map_y,
            })

    return jsonify({'status': 'ok', 'data': suggestions})


# ============================================================================
# SINGLE PLAN — metadata, image, update, delete
# ============================================================================

@floor_plans_bp.route('/api/floor-plans/<int:plan_id>', methods=['GET'])
@require_login
def get_floor_plan(plan_id):
    """Plan metadata + the devices currently placed on it."""
    plan = _get_plan_or_404(plan_id)
    placed = (
        Device.query.filter_by(floor_plan_id=plan_id)
        .order_by(Device.device_name)
        .all()
    )
    data = plan.to_dict(device_count=len(placed))
    data['placed_devices'] = [_device_to_marker(d) for d in placed]
    return jsonify({'status': 'ok', 'data': data})


@floor_plans_bp.route('/api/floor-plans/<int:plan_id>/image', methods=['GET'])
@require_login
def get_floor_plan_image(plan_id):
    """Serve the normalised PNG behind auth (not from static/)."""
    plan = _get_plan_or_404(plan_id)
    path = image_path(plan.image_filename)
    return send_file(path, mimetype=plan.mime_type or 'image/png', conditional=True)


@floor_plans_bp.route('/api/floor-plans/<int:plan_id>', methods=['PUT'])
@require_role('admin')
def update_floor_plan(plan_id):
    """Rename / reorder, or replace the image (bumps version, keeps placements).

    Accepts multipart (when replacing the image, optional `file` + form fields)
    or JSON (rename/reorder only).
    """
    plan = _get_plan_or_404(plan_id)
    before = plan.to_dict()

    file_storage = request.files.get('file')
    if file_storage is not None and file_storage.filename:
        # Replace image: normalise new file, swap, bump version. Device
        # coordinates are intentionally left untouched.
        try:
            norm = normalise_upload(file_storage)
        except FloorPlanError as exc:
            return jsonify({'status': 'error', 'message': str(exc)}), 400
        old_filename = plan.image_filename
        plan.image_filename = norm['image_filename']
        plan.mime_type = norm['mime_type']
        plan.image_width = norm['image_width']
        plan.image_height = norm['image_height']
        plan.original_filename = norm['original_filename']
        plan.version = (plan.version or 1) + 1
        delete_image(old_filename)

    # JSON or form fields for rename / reorder
    payload = request.get_json(silent=True) or request.form
    name = _clean_text(payload.get('name')) if payload else None
    if name:
        plan.name = name
    if payload and payload.get('sort_order') not in (None, ''):
        try:
            plan.sort_order = int(payload.get('sort_order'))
        except (TypeError, ValueError):
            pass

    db.session.commit()

    from utils.audit_helpers import capture_model_diff
    create_audit_log(
        action='update',
        entity_type='floor_plan',
        entity_id=plan.id,
        entity_name=plan.name,
        description=f'Updated floor plan "{plan.name}"',
        changes=capture_model_diff(before, plan) or None,
    )

    return jsonify({'status': 'ok', 'data': plan.to_dict()})


@floor_plans_bp.route('/api/floor-plans/<int:plan_id>', methods=['DELETE'])
@require_role('admin')
def delete_floor_plan(plan_id):
    """Delete a plan, clear placements of its devices, remove the image file."""
    plan = _get_plan_or_404(plan_id)
    name = plan.name
    image_filename = plan.image_filename

    Device.query.filter_by(floor_plan_id=plan_id).update(
        {
            'floor_plan_id': None,
            'map_x': None,
            'map_y': None,
            'map_rotation': None,
        },
        synchronize_session='fetch',
    )
    db.session.delete(plan)
    db.session.commit()
    delete_image(image_filename)

    create_audit_log(
        action='delete',
        entity_type='floor_plan',
        entity_id=plan_id,
        entity_name=name,
        description=f'Deleted floor plan "{name}"',
    )
    return jsonify({'status': 'ok', 'message': f'Floor plan "{name}" deleted'})


# ============================================================================
# PLACEMENTS — bulk upsert
# ============================================================================

@floor_plans_bp.route('/api/floor-plans/<int:plan_id>/placements', methods=['POST'])
@require_role('admin')
def upsert_placements(plan_id):
    """Bulk place / move / unplace devices on a plan.

    Body: {"placements": [{device_id, map_x, map_y, map_rotation?, map_locked?}], "force"?: bool}

    Rules:
      - map_x/map_y null  -> unplace the device (clear plan + coords).
      - otherwise         -> place at the given percent coordinates on this plan.
      - a device whose map_locked is currently True is NOT moved/unplaced unless
        the item explicitly sets map_locked=false, or the request sets force=true.
      - map_locked, when present in an item, is applied (lets admins toggle locks).
      - a device must belong to this plan's site to be placed.
    """
    plan = _get_plan_or_404(plan_id)
    body = request.get_json(silent=True) or {}
    placements = body.get('placements')
    if not isinstance(placements, list) or not placements:
        return jsonify({'status': 'error', 'message': 'placements array is required'}), 400

    force = bool(body.get('force'))

    # Devices eligible for this plan = devices of the plan's site.
    eligible_ids = {d.device_id for d in SitesService().get_site_devices(plan.site_id)}

    def _coord(value):
        if value is None or value == '':
            return None
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return None

    updated = 0
    skipped_locked = []
    invalid = []

    for item in placements:
        if not isinstance(item, dict):
            continue
        try:
            device_id = int(item.get('device_id'))
        except (TypeError, ValueError):
            continue
        if device_id not in eligible_ids:
            invalid.append(device_id)
            continue
        device = Device.query.get(device_id)
        if device is None:
            invalid.append(device_id)
            continue

        # Determine the lock intent for this request.
        explicit_lock = item.get('map_locked', None)
        will_unlock = explicit_lock is False
        movement_blocked = bool(device.map_locked) and not will_unlock and not force

        # Apply an explicit lock toggle regardless (it's the operator's intent).
        if explicit_lock is not None:
            device.map_locked = bool(explicit_lock)

        if movement_blocked:
            skipped_locked.append(device_id)
            # still commit any lock toggle above
            continue

        x = _coord(item.get('map_x'))
        y = _coord(item.get('map_y'))
        if x is None or y is None:
            # Unplace
            device.floor_plan_id = None
            device.map_x = None
            device.map_y = None
            device.map_rotation = None
        else:
            device.floor_plan_id = plan.id
            device.map_x = x
            device.map_y = y
            rot = item.get('map_rotation')
            if rot is not None and rot != '':
                try:
                    device.map_rotation = float(rot)
                except (TypeError, ValueError):
                    pass
        updated += 1

    db.session.commit()

    create_audit_log(
        action='update',
        entity_type='floor_plan',
        entity_id=plan.id,
        entity_name=plan.name,
        description=(
            f'Updated {updated} device placement(s) on "{plan.name}"'
            + (f', {len(skipped_locked)} locked' if skipped_locked else '')
        ),
    )

    return jsonify({
        'status': 'ok',
        'updated': updated,
        'skipped_locked': skipped_locked,
        'invalid': invalid[:20],
    })
