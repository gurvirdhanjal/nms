from flask import Blueprint, jsonify, request, render_template, abort
from extensions import db
from models.subnet import Subnet
from models.site import Site
from middleware.rbac import require_login, require_role
import ipaddress

subnets_bp = Blueprint('subnets', __name__)

@subnets_bp.route('/subnets/page')
@require_role('admin')
def subnets_page():
    """Render the Subnets Management UI tab."""
    sites = Site.query.order_by(Site.site_name.asc()).all()
    subnets = Subnet.query.order_by(Subnet.site_id.asc(), Subnet.cidr.asc()).all()
    return render_template('subnets.html', sites=sites, subnets=subnets)

@subnets_bp.route('/api/subnets', methods=['GET'])
@require_login
def get_subnets():
    """Returns a list of all subnets formatted for the UI."""
    subnets = Subnet.query.order_by(Subnet.site_id.asc(), Subnet.cidr.asc()).all()  # FIXME: SCOPING — returns all subnets globally, not scoped to user's site
    return jsonify({
        'status': 'success',
        'subnets': [s.to_dict() for s in subnets]
    })


@subnets_bp.route('/api/subnets/detect-local', methods=['GET'])
@require_role('admin')
def detect_local_subnet():
    """
    Auto-detect the local network subnet using the scanner service.
    Returns the detected CIDR for auto-populating the subnet input field.
    """
    try:
        from services.discovery_service import get_discovery_service
        service = get_discovery_service()
        detected_range = service.scanner.get_local_ip_range()
        
        # Validate and normalize
        normalized_cidr = Subnet.validate_cidr(detected_range)
        if not normalized_cidr:
            return jsonify({
                'status': 'error',
                'message': f'Detected range "{detected_range}" is not a valid CIDR'
            }), 400
        
        return jsonify({
            'status': 'success',
            'cidr': normalized_cidr,
            'detected_range': detected_range
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Failed to detect local subnet: {str(e)}'
        }), 500


@subnets_bp.route('/api/subnets', methods=['POST'])
@require_role('admin')
def add_subnet():
    """Adds a new Subnet mapped to a site_id"""
    data = request.json
    cidr_input = data.get('cidr', '').strip()
    site_id = data.get('site_id')
    description = data.get('description', '').strip()
    auto_detect = data.get('auto_detect', False)

    # Auto-detection mode
    if auto_detect and not cidr_input:
        try:
            from services.discovery_service import get_discovery_service
            service = get_discovery_service()
            cidr_input = service.scanner.get_local_ip_range()
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Auto-detection failed: {str(e)}'
            }), 400

    if not cidr_input or not site_id:
        return jsonify({'status': 'error', 'message': 'CIDR and Site ID are required'}), 400

    # Validate and normalize CIDR using Python's ipaddress library
    normalized_cidr = Subnet.validate_cidr(cidr_input)
    if not normalized_cidr:
        return jsonify({'status': 'error', 'message': f'Invalid CIDR block: {cidr_input}'}), 400

    # Check for overlapping subnets in the same site
    existing_subnets = Subnet.query.filter_by(site_id=site_id).all()
    for existing in existing_subnets:
        if existing.cidr == normalized_cidr:
            return jsonify({
                'status': 'error',
                'message': f'CIDR {normalized_cidr} is already mapped to this site'
            }), 409
    
    try:
        new_subnet = Subnet(
            cidr=normalized_cidr,
            site_id=site_id,
            description=description or (f'Auto-detected: {cidr_input}' if auto_detect else '')
        )
        db.session.add(new_subnet)
        db.session.commit()
        
        # Audit logging
        from middleware.rbac import create_audit_log
        create_audit_log(
            action='create',
            entity_type='subnet',
            entity_id=new_subnet.id,
            entity_name=normalized_cidr,
            description=f'Added subnet {normalized_cidr} to site ID {site_id}' + (' (auto-detected)' if auto_detect else '')
        )
        
        return jsonify({'status': 'success', 'subnet': new_subnet.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        # Handle unique constraint violation softly
        if 'uq_site_cidr' in str(e) or 'UniqueViolation' in str(e):
            return jsonify({'status': 'error', 'message': f'CIDR {normalized_cidr} is already mapped to this site.'}), 409
        return jsonify({'status': 'error', 'message': str(e)}), 500

@subnets_bp.route('/api/subnets/<int:subnet_id>', methods=['DELETE'])
@require_role('admin')
def delete_subnet(subnet_id):
    """Deletes a subnet mapping"""
    subnet = Subnet.query.get_or_404(subnet_id)
    try:
        db.session.delete(subnet)
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Subnet mapping deleted.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
