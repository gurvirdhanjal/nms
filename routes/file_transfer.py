import os
import json
import requests
import shutil
import tempfile
import zipfile
from flask import Blueprint, render_template, request, jsonify, session, send_file
from werkzeug.utils import secure_filename
from datetime import datetime
import threading
from middleware.rbac import require_role

file_transfer_bp = Blueprint('file_transfer_bp', __name__)

# Configuration
UPLOAD_FOLDER = 'client_uploads'
CLIENT_FOLDER = 'file_transfer'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CLIENT_FOLDER, exist_ok=True)

# Store connected clients
connected_clients = {}

# Authentication decorators (centralized RBAC middleware)
# File transfer operations are admin-only.
login_required = require_role('admin')
admin_required = require_role('admin')

def test_client_connection(client_ip, client_port=5002):
    """Test connection to client"""
    try:
        response = requests.get(
            f'http://{client_ip}:{client_port}/api/health',
            timeout=5
        )
        if response.status_code == 200:
            return True
    except:
        pass
    return False

def get_client_api_key(client_ip):
    """Get client API key (simplified - should be stored securely)"""
    # In production, store in database
    return "8f42v73054r1749f8g58848be5e6502c"

@file_transfer_bp.route('/file_transfer')
@login_required
@admin_required
def file_transfer_dashboard():
    """File transfer dashboard"""
    return render_template('file_transfer.html')

@file_transfer_bp.route('/api/clients/discover', methods=['GET'])
@login_required
def discover_clients():
    """Discover clients in local network"""
    try:
        # Get local network range (simplified)
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        network_prefix = '.'.join(local_ip.split('.')[:3])
        
        discovered_clients = []
        
        # Scan local network (limited range for speed)
        def check_client(ip):
            try:
                response = requests.get(
                    f'http://{ip}:5002/api/health',
                    timeout=2
                )
                if response.status_code == 200:
                    data = response.json()
                    discovered_clients.append({
                        'ip': ip,
                        'hostname': data.get('hostname', 'Unknown'),
                        'status': 'online',
                        'port': 5002,
                        'last_seen': datetime.now().isoformat()
                    })
            except:
                pass
        
        # Threaded scanning
        threads = []
        for i in range(1, 51):  # Scan first 50 IPs
            ip = f"{network_prefix}.{i}"
            thread = threading.Thread(target=check_client, args=(ip,))
            thread.start()
            threads.append(thread)
        
        for thread in threads:
            thread.join()
        
        return jsonify({
            'success': True,
            'clients': discovered_clients
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/clients/connect', methods=['POST'])
@login_required
def connect_to_client():
    """Connect to specific client"""
    data = request.get_json()
    client_ip = data.get('ip', '').strip()
    client_port = data.get('port', 5002)
    
    if not client_ip:
        return jsonify({'error': 'Client IP is required'}), 400
    
    try:
        # Test connection
        if not test_client_connection(client_ip, client_port):
            return jsonify({'error': 'Cannot connect to client'}), 400
        
        # Get client info
        api_key = get_client_api_key(client_ip)
        headers = {'X-API-Key': api_key}
        
        response = requests.get(
            f'http://{client_ip}:{client_port}/api/secure/stats',
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            client_info = response.json()
            
            # Store in session
            session['connected_client'] = {
                'ip': client_ip,
                'port': client_port,
                'api_key': api_key,
                'info': client_info,
                'connected_at': datetime.now().isoformat()
            }
            
            return jsonify({
                'success': True,
                'message': f'Connected to {client_ip}',
                'client': client_info
            })
        else:
            return jsonify({'error': 'Failed to authenticate with client'}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/clients/disconnect', methods=['POST'])
@login_required
def disconnect_client():
    """Disconnect from current client"""
    session.pop('connected_client', None)
    return jsonify({'success': True, 'message': 'Disconnected'})

@file_transfer_bp.route('/api/clients/current', methods=['GET'])
@login_required
def get_current_client():
    """Get currently connected client"""
    client = session.get('connected_client')
    if client:
        return jsonify({'success': True, 'client': client})
    return jsonify({'success': False, 'message': 'No client connected'})

@file_transfer_bp.route('/api/files/client/list', methods=['POST'])
@login_required
def list_client_files():
    """List files on connected client"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    data = request.get_json()
    path = data.get('path', '')
    
    try:
        response = requests.get(
            f'http://{client["ip"]}:{client["port"]}/api/files/list',
            params={'path': path},
            headers={'X-API-Key': client['api_key']},
            timeout=10
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Failed to list files'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/client/download', methods=['POST'])
@login_required
def download_from_client():
    """Download file from client"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    data = request.get_json()
    file_path = data.get('path', '')
    
    if not file_path:
        return jsonify({'error': 'File path is required'}), 400
    
    try:
        response = requests.get(
            f'http://{client["ip"]}:{client["port"]}/api/files/download',
            params={'path': file_path},
            headers={'X-API-Key': client['api_key']},
            stream=True,
            timeout=30
        )
        
        if response.status_code == 200:
            # Save to temp file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip' if file_path.endswith('.zip') else '')
            temp_file.close()
            
            with open(temp_file.name, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            filename = os.path.basename(file_path)
            if file_path.endswith('.zip'):
                filename = f"{os.path.basename(file_path)}.zip"
            
            return send_file(
                temp_file.name,
                as_attachment=True,
                download_name=filename
            )
        else:
            return jsonify({'error': 'Download failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/client/upload', methods=['POST'])
@login_required
def upload_to_client():
    """Upload file to client"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    target_path = request.form.get('path', '')
    
    try:
        files = {'file': (request.files['file'].filename, request.files['file'])}
        data = {'path': target_path}
        
        response = requests.post(
            f'http://{client["ip"]}:{client["port"]}/api/files/upload',
            files=files,
            data=data,
            headers={'X-API-Key': client['api_key']},
            timeout=30
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Upload failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/client/create_folder', methods=['POST'])
@login_required
def create_client_folder():
    """Create folder on client"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    data = request.get_json()
    
    try:
        response = requests.post(
            f'http://{client["ip"]}:{client["port"]}/api/files/create_folder',
            json=data,
            headers={'X-API-Key': client['api_key']},
            timeout=10
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Failed to create folder'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/client/delete', methods=['POST'])
@login_required
def delete_client_file():
    """Delete file on client"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    data = request.get_json()
    
    try:
        response = requests.post(
            f'http://{client["ip"]}:{client["port"]}/api/files/delete',
            json=data,
            headers={'X-API-Key': client['api_key']},
            timeout=10
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Failed to delete'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/client/system_info', methods=['GET'])
@login_required
def get_client_system_info():
    """Get client system info"""
    client = session.get('connected_client')
    if not client:
        return jsonify({'error': 'No client connected'}), 400
    
    try:
        response = requests.get(
            f'http://{client["ip"]}:{client["port"]}/api/files/system_info',
            headers={'X-API-Key': client['api_key']},
            timeout=10
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': 'Failed to get system info'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/local/list', methods=['POST'])
@login_required
def list_local_files():
    """List files on local server"""
    data = request.get_json()
    path = data.get('path', CLIENT_FOLDER)
    
    if not os.path.exists(path):
        return jsonify({'error': 'Path does not exist'}), 404
    
    try:
        items = []
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            try:
                item_info = {
                    'name': item,
                    'path': item_path,
                    'is_dir': os.path.isdir(item_path),
                    'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
                    'modified': os.path.getmtime(item_path),
                    'created': os.path.getctime(item_path)
                }
                items.append(item_info)
            except:
                continue
        
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        return jsonify({
            'success': True,
            'current_path': path,
            'parent_path': os.path.dirname(path),
            'items': items
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/local/download', methods=['POST'])
@login_required
def download_local_file():
    """Download file from local server"""
    data = request.get_json()
    file_path = data.get('path', '')
    
    if not file_path:
        return jsonify({'error': 'File path is required'}), 400
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        return send_file(
            file_path,
            as_attachment=True,
            download_name=os.path.basename(file_path)
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@file_transfer_bp.route('/api/files/local/upload', methods=['POST'])
@login_required
def upload_local_file():
    """Upload file to local server"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    target_path = request.form.get('path', CLIENT_FOLDER)
    os.makedirs(target_path, exist_ok=True)
    
    uploaded_files = []
    failed_files = []
    
    files = request.files.getlist('file')
    for file in files:
        if file.filename == '':
            continue
        
        filename = secure_filename(file.filename)
        file_path = os.path.join(target_path, filename)
        
        # Handle duplicates
        counter = 1
        while os.path.exists(file_path):
            name, ext = os.path.splitext(filename)
            file_path = os.path.join(target_path, f"{name}_{counter}{ext}")
            counter += 1
        
        try:
            file.save(file_path)
            uploaded_files.append({
                'filename': os.path.basename(file_path),
                'path': file_path,
                'size': os.path.getsize(file_path)
            })
        except Exception as e:
            failed_files.append({
                'filename': file.filename,
                'error': str(e)
            })
    
    return jsonify({
        'success': True,
        'uploaded': len(uploaded_files),
        'failed': len(failed_files),
        'uploaded_files': uploaded_files,
        'failed_files': failed_files
    })

@file_transfer_bp.route('/api/files/transfer_between', methods=['POST'])
@login_required
def transfer_between_systems():
    """Transfer files between client and server"""
    data = request.get_json()
    source_paths = data.get('source_paths', [])
    destination_type = data.get('destination_type', '')  # 'client' or 'server'
    destination_path = data.get('destination_path', '')
    action = data.get('action', 'copy')  # 'copy' or 'move'
    
    if not source_paths or not destination_type:
        return jsonify({'error': 'Missing parameters'}), 400
    
    client = session.get('connected_client')
    if destination_type == 'client' and not client:
        return jsonify({'error': 'No client connected'}), 400
    
    transferred = []
    failed = []
    
    for source_path in source_paths:
        if not os.path.exists(source_path):
            failed.append({'path': source_path, 'error': 'Source does not exist'})
            continue
        
        try:
            filename = os.path.basename(source_path)
            
            if destination_type == 'client':
                # Upload to client
                with open(source_path, 'rb') as f:
                    files = {'file': (filename, f)}
                    data = {'path': destination_path}
                    
                    response = requests.post(
                        f'http://{client["ip"]}:{client["port"]}/api/files/upload',
                        files=files,
                        data=data,
                        headers={'X-API-Key': client['api_key']},
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        transferred.append(source_path)
                        if action == 'move':
                            os.remove(source_path)
                    else:
                        failed.append({'path': source_path, 'error': 'Upload failed'})
                        
            else:  # server
                # Copy/move locally
                dest_path = os.path.join(destination_path, filename)
                
                # Handle duplicates
                counter = 1
                while os.path.exists(dest_path):
                    name, ext = os.path.splitext(filename)
                    dest_path = os.path.join(destination_path, f"{name}_{counter}{ext}")
                    counter += 1
                
                if action == 'move':
                    shutil.move(source_path, dest_path)
                else:
                    if os.path.isdir(source_path):
                        shutil.copytree(source_path, dest_path)
                    else:
                        shutil.copy2(source_path, dest_path)
                
                transferred.append(source_path)
                
        except Exception as e:
            failed.append({'path': source_path, 'error': str(e)})
    
    return jsonify({
        'success': True,
        'transferred': len(transferred),
        'failed': len(failed),
        'transferred_items': transferred,
        'failed_items': failed
    })
