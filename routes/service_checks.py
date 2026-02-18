"""
Service Check API routes for Network Monitoring System.
Provides endpoints for TCP, HTTP, and DNS connectivity checks.
"""
from flask import Blueprint, jsonify, request
from datetime import datetime
from middleware.rbac import require_login

service_checks_bp = Blueprint('service_checks_bp', __name__, url_prefix='/api/services')


@service_checks_bp.before_request
@require_login
def _service_checks_auth_guard():
    return None


# ============================================================
# GET /api/services/check/tcp
# ============================================================
@service_checks_bp.route('/check/tcp')
def check_tcp():
    """
    Check TCP port connectivity.
    Query params: host, port, timeout (optional)
    """
    host = request.args.get('host')
    port = request.args.get('port')
    timeout = request.args.get('timeout', 5, type=float)
    
    if not host or not port:
        return jsonify({'error': 'Missing host or port parameter'}), 400
    
    try:
        port = int(port)
    except ValueError:
        return jsonify({'error': 'Port must be a number'}), 400
    
    try:
        from services.service_checker import service_checker
        
        result = service_checker.check_tcp(host, port, timeout)
        
        return jsonify({
            'check_type': 'tcp',
            'host': host,
            'port': port,
            **result.to_dict()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/services/check/http
# ============================================================
@service_checks_bp.route('/check/http')
def check_http():
    """
    Check HTTP endpoint availability.
    Query params: url, method (GET), expected_status (200), timeout, verify_ssl (true)
    """
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'Missing url parameter'}), 400
    
    method = request.args.get('method', 'GET').upper()
    expected_status = request.args.get('expected_status', 200, type=int)
    timeout = request.args.get('timeout', 10, type=float)
    verify_ssl = request.args.get('verify_ssl', 'true').lower() == 'true'
    expected_content = request.args.get('expected_content')
    
    try:
        from services.service_checker import service_checker
        
        result = service_checker.check_http(
            url=url,
            method=method,
            expected_status=expected_status,
            timeout=timeout,
            verify_ssl=verify_ssl,
            expected_content=expected_content
        )
        
        return jsonify({
            'check_type': 'http',
            'url': url,
            **result.to_dict()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/services/check/dns
# ============================================================
@service_checks_bp.route('/check/dns')
def check_dns():
    """
    Check DNS resolution.
    Query params: hostname, record_type (A), nameserver (optional), timeout
    """
    hostname = request.args.get('hostname')
    if not hostname:
        return jsonify({'error': 'Missing hostname parameter'}), 400
    
    record_type = request.args.get('record_type', 'A').upper()
    nameserver = request.args.get('nameserver')
    timeout = request.args.get('timeout', 5, type=float)
    
    try:
        from services.service_checker import service_checker
        
        result = service_checker.check_dns(
            hostname=hostname,
            record_type=record_type,
            nameserver=nameserver,
            timeout=timeout
        )
        
        return jsonify({
            'check_type': 'dns',
            'hostname': hostname,
            **result.to_dict()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/services/check/ports
# ============================================================
@service_checks_bp.route('/check/ports')
def check_common_ports():
    """
    Check multiple common ports on a host.
    Query params: host, ports (comma-separated list or 'common')
    """
    host = request.args.get('host')
    if not host:
        return jsonify({'error': 'Missing host parameter'}), 400
    
    ports_param = request.args.get('ports', 'common')
    timeout = request.args.get('timeout', 2, type=float)
    
    try:
        from services.service_checker import service_checker, COMMON_PORTS
        
        if ports_param == 'common':
            ports_to_check = COMMON_PORTS
        else:
            # Parse comma-separated port list
            ports_to_check = {}
            for p in ports_param.split(','):
                p = p.strip()
                try:
                    port_num = int(p)
                    ports_to_check[f'PORT_{port_num}'] = port_num
                except ValueError:
                    continue
        
        results = []
        for name, port in ports_to_check.items():
            result = service_checker.check_tcp(host, port, timeout)
            results.append({
                'service': name,
                'port': port,
                **result.to_dict()
            })
        
        open_ports = [r for r in results if r['status'] == 'UP']
        
        return jsonify({
            'host': host,
            'total_checked': len(results),
            'open_count': len(open_ports),
            'results': results
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# POST /api/services/check/batch
# ============================================================
@service_checks_bp.route('/check/batch', methods=['POST'])
def check_batch():
    """
    Run multiple checks in batch.
    Body: { checks: [ { type: 'tcp'|'http'|'dns', ... }, ... ] }
    """
    try:
        from services.service_checker import service_checker
        
        data = request.get_json()
        checks = data.get('checks', [])
        
        if not checks:
            return jsonify({'error': 'No checks provided'}), 400
        
        results = []
        
        for check in checks:
            check_type = check.get('type', '').lower()
            
            if check_type == 'tcp':
                result = service_checker.check_tcp(
                    host=check.get('host'),
                    port=check.get('port'),
                    timeout=check.get('timeout', 5)
                )
            elif check_type == 'http':
                result = service_checker.check_http(
                    url=check.get('url'),
                    method=check.get('method', 'GET'),
                    expected_status=check.get('expected_status', 200),
                    timeout=check.get('timeout', 10)
                )
            elif check_type == 'dns':
                result = service_checker.check_dns(
                    hostname=check.get('hostname'),
                    record_type=check.get('record_type', 'A'),
                    timeout=check.get('timeout', 5)
                )
            else:
                result = None
            
            if result:
                results.append({
                    'check': check,
                    **result.to_dict()
                })
        
        # Summary
        up_count = len([r for r in results if r.get('status') == 'UP'])
        
        return jsonify({
            'total_checks': len(results),
            'up_count': up_count,
            'down_count': len(results) - up_count,
            'results': results
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
