# routes/camera_streaming.py
from flask import Blueprint, render_template, jsonify, request, Response, session, redirect, url_for
from extensions import db
from models.device import Device
import cv2
import threading
import time
import os
from datetime import datetime
import base64
import numpy as np

camera_bp = Blueprint('camera_bp', __name__)

# Global variables for camera management
active_streams = {}
stream_lock = threading.Lock()
snapshots_dir = 'static/snapshots'
recordings_dir = 'static/recordings'

# Force FFmpeg to use TCP transport globally (avoids UDP packet loss)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Create directories if they don't exist
os.makedirs(snapshots_dir, exist_ok=True)
os.makedirs(recordings_dir, exist_ok=True)

CAMERA_DEVICE_TYPES = {'camera', 'camera/iot', 'camera_iot'}

def is_camera_type(value: str) -> bool:
    return (value or '').strip().lower() in CAMERA_DEVICE_TYPES

class CameraStream:
    def __init__(self, device_id, rtsp_link):
        self.device_id = device_id
        self.rtsp_link = rtsp_link
        self.cap = None
        self.is_running = False
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.recording = False
        self.writer = None
        self.last_frame_time = 0
        self.capture_thread = None
        self.streaming_clients = 0
        self.client_lock = threading.Lock()
        self.consecutive_errors = 0
        self.max_errors = 5
        self.last_error = None  # Track the last error message
        
    def start_stream(self):
        """Start the camera stream with background frame capture"""
        try:
            print(f"Attempting to start camera stream for device {self.device_id} with RTSP: {self.rtsp_link}")
            
            # Force FFmpeg to use TCP transport (avoids UDP packet loss)
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            
            self.cap = cv2.VideoCapture(self.rtsp_link)
            if not self.cap.isOpened():
                msg = f"Failed to open camera stream: {self.rtsp_link}"
                print(msg)
                self.last_error = "Failed to open stream"
                return False
            
            # Set camera properties for better performance
            # COMMENTED OUT: Buffer size 1 breaks H.265/H.264 decoding if packets are dropped
            # self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, 15)
            
            # Test frame capture
            ret, test_frame = self.cap.read()
            if not ret:
                print(f"Failed to read initial frame from camera {self.device_id}")
                self.cap.release()
                return False
                
            self.is_running = True
            # Start background frame capture thread
            self.capture_thread = threading.Thread(target=self._capture_frames, daemon=True)
            self.capture_thread.start()
            
            print(f"Successfully started stream for camera {self.device_id}")
            return True
            
        except Exception as e:
            msg = f"Error starting stream for camera {self.device_id}: {str(e)}"
            print(msg)
            self.last_error = str(e)
            if self.cap:
                self.cap.release()
            return False
    
    def _capture_frames(self):
        """Background thread to continuously capture frames"""
        print(f"Starting frame capture thread for camera {self.device_id}")
        
        while self.is_running:
            try:
                if self.cap and self.cap.isOpened():
                    ret, frame = self.cap.read()
                    if ret:
                        # Resize frame to reduce bandwidth
                        frame = cv2.resize(frame, (640, 480))
                        
                        # Encode as JPEG
                        ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        if ret:
                            with self.frame_lock:
                                self.current_frame = jpeg.tobytes()
                            self.last_frame_time = time.time()
                            self.consecutive_errors = 0
                            
                            # If recording, write frame
                            if self.recording and self.writer:
                                self.writer.write(frame)
                        else:
                            self.consecutive_errors += 1
                            print(f"Failed to encode frame for camera {self.device_id}")
                    else:
                        self.consecutive_errors += 1
                        self.last_error = f"Read failed (Count: {self.consecutive_errors})"
                        print(f"Failed to read frame from camera {self.device_id}, error count: {self.consecutive_errors}")
                        
                    # If too many consecutive errors, try to reconnect
                    if self.consecutive_errors >= self.max_errors:
                        print(f"Reconnecting camera {self.device_id} due to {self.consecutive_errors} consecutive errors")
                        self._reconnect_camera()
                        self.consecutive_errors = 0
                        
                else:
                    print(f"Camera {self.device_id} is not opened, attempting to reconnect")
                    self._reconnect_camera()
                    
                time.sleep(0.005)  # Minimal sleep, rely on cap.read() blocking
                
            except Exception as e:
                print(f"Error in frame capture for camera {self.device_id}: {str(e)}")
                self.last_error = str(e)
                self.consecutive_errors += 1
                time.sleep(1)  # Wait before retrying
    
    def _reconnect_camera(self):
        """Reconnect to camera"""
        try:
            print(f"Attempting to reconnect camera {self.device_id}")
            
            if self.cap:
                self.cap.release()
                time.sleep(1)  # Wait before reconnecting
            
            self.cap = cv2.VideoCapture(self.rtsp_link)
            if self.cap.isOpened():
                # self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Removed for stability
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap.set(cv2.CAP_PROP_FPS, 15)
                print(f"Successfully reconnected camera {self.device_id}")
            else:
                print(f"Failed to reconnect camera {self.device_id}")
                
        except Exception as e:
            print(f"Error reconnecting camera {self.device_id}: {str(e)}")
    
    def get_current_frame(self):
        """Get the current frame in JPEG format"""
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame
            else:
                # Return a black frame if no frame is available
                return self._generate_black_frame()
    
    def _generate_black_frame(self):
        """Generate a black frame as placeholder"""
        black_image = np.zeros((480, 640, 3), dtype=np.uint8)
        ret, jpeg = cv2.imencode('.jpg', black_image)
        return jpeg.tobytes()
    
    def add_client(self):
        """Increment client count"""
        with self.client_lock:
            self.streaming_clients += 1
            print(f"Added client to camera {self.device_id}. Total clients: {self.streaming_clients}")
    
    def remove_client(self):
        """Decrement client count"""
        with self.client_lock:
            self.streaming_clients = max(0, self.streaming_clients - 1)
            print(f"Removed client from camera {self.device_id}. Total clients: {self.streaming_clients}")
    
    def has_clients(self):
        """Check if there are active clients"""
        return self.streaming_clients > 0
    
    def stop_stream(self):
        """Stop the camera stream"""
        print(f"Stopping stream for camera {self.device_id}")
        self.is_running = False
        
        if self.writer:
            self.writer.release()
            self.writer = None
            
        if self.cap:
            self.cap.release()
            self.cap = None
        
        # Wait for capture thread to finish
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=5.0)
            
        print(f"Successfully stopped stream for camera {self.device_id}")

def generate_frames(device_id, fps=None):
    """
    Generate video frames for streaming with Adaptive FPS.
    - fps=2: Grid View (Low CPU)
    - fps=25: Live View (High Performance)
    """
    with stream_lock:
        if device_id not in active_streams:
            print(f"Camera {device_id} not found in active streams. Auto-starting...")
            # Auto-start if valid camera
            camera = Device.query.get(device_id)
            if camera and camera.rstplink:
                if not start_camera_stream(device_id, camera.rstplink):
                    return
            else:
                return
        
        stream = active_streams[device_id]
        stream.add_client()
        print(f"Starting frame generation for camera {device_id} at {fps if fps else 'default'} FPS")
    
    # Calculate delay based on requested FPS
    delay = 1.0 / float(fps) if fps else 0.1 # Default 10 FPS
    
    try:
        while stream.is_running and stream.has_clients():
            frame = stream.get_current_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                # Send black frame if no frame available
                black_frame = generate_black_frame()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + black_frame + b'\r\n')
            
            # Adaptive Sleep (Throttle for 50+ cameras)
            time.sleep(delay)
            
    except Exception as e:
        print(f"Error generating frames for camera {device_id}: {str(e)}")
    finally:
        # Clean up client count
        with stream_lock:
            if device_id in active_streams:
                active_streams[device_id].remove_client()
                print(f"Client disconnected from camera {device_id}. Remaining clients: {active_streams[device_id].streaming_clients}")
                
                # AGGRESSIVE CLEANUP: Stop stream IMMEDIATELY if no clients to save CPU
                if not active_streams[device_id].has_clients():
                    print(f"No clients left for camera {device_id}. Stopping stream to save resources.")
                    stop_camera_stream(device_id)

def generate_black_frame():
    """Generate a black frame as placeholder"""
    black_image = np.zeros((480, 640, 3), dtype=np.uint8)
    ret, jpeg = cv2.imencode('.jpg', black_image)
    return jpeg.tobytes()

def start_camera_stream(device_id, rtsp_link):
    """Start a camera stream"""
    with stream_lock:
        if device_id in active_streams:
            print(f"Stream already exists for camera {device_id}")
            return True
        
        print(f"Creating new stream for camera {device_id}")
        stream = CameraStream(device_id, rtsp_link)
        if stream.start_stream():
            active_streams[device_id] = stream
            print(f"Successfully started stream for camera {device_id}")
            return True
        else:
            print(f"Failed to start stream for camera {device_id}")
            return False

def stop_camera_stream(device_id):
    """Stop a camera stream"""
    with stream_lock:
        if device_id in active_streams:
            stream = active_streams[device_id]
            stream.stop_stream()
            del active_streams[device_id]
            print(f"Stopped stream for camera {device_id}")
            return True
        else:
            print(f"Camera {device_id} not found in active streams")
            return False

def cleanup_idle_streams():
    """Clean up streams with no active clients"""
    with stream_lock:
        streams_to_remove = []
        for device_id, stream in active_streams.items():
            if not stream.has_clients():
                streams_to_remove.append(device_id)
        
        for device_id in streams_to_remove:
            print(f"Cleaning up idle stream for camera {device_id}")
            stream = active_streams[device_id]
            stream.stop_stream()
            del active_streams[device_id]

# Background thread for cleanup
def cleanup_worker():
    """Background thread to clean up idle streams"""
    print("Starting camera stream cleanup worker")
    while True:
        try:
            cleanup_idle_streams()
            time.sleep(30)  # Check every 30 seconds
        except Exception as e:
            print(f"Error in cleanup worker: {str(e)}")
            time.sleep(60)

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
cleanup_thread.start()

@camera_bp.route('/cameras')
def camera_dashboard():
    """Camera streaming dashboard"""
    if not session.get('logged_in'):
        return redirect(url_for('auth_bp.login'))
    
    # Get all camera devices
    cameras = Device.query.filter(Device.device_type.in_(CAMERA_DEVICE_TYPES)).all()
    print(f"Loaded {len(cameras)} cameras for dashboard")
    
    return render_template('cameras/camera_dashboard.html', cameras=cameras)

@camera_bp.route('/api/cameras/stream/<int:device_id>')
def video_feed(device_id):
    """Video streaming route - FIXED for browser compatibility"""
    if not session.get('logged_in'):
        return "Unauthorized", 401
    
    camera = Device.query.get(device_id)
    if not camera or not is_camera_type(camera.device_type):
        print(f"Camera {device_id} not found or not a camera device")
        return "Camera not found", 404
    
    print(f"Video feed requested for camera {device_id} ({camera.device_name})")
    
    # Start stream if not already running
    if device_id not in active_streams:
        print(f"Starting new stream for camera {device_id}")
        if not start_camera_stream(device_id, camera.rstplink):
            return "Failed to start stream", 500
    
    # Get FPS from Query Param (Default to 10 if not set)
    fps = request.args.get('fps', default=10, type=int)
    
    # Return the streaming response with correct headers
    return Response(
        generate_frames(device_id, fps),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@camera_bp.route('/api/cameras/snapshot/<int:device_id>')
def take_snapshot(device_id):
    """Take a snapshot from camera"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    camera = Device.query.get(device_id)
    if not camera or not is_camera_type(camera.device_type):
        return jsonify({'success': False, 'error': 'Camera not found'}), 404
    
    try:
        print(f"Taking snapshot for camera {device_id}")
        
        # Use active stream if available for faster snapshot
        if device_id in active_streams:
            stream = active_streams[device_id]
            frame_data = stream.get_current_frame()
            if frame_data:
                # Convert bytes back to image and save
                nparr = np.frombuffer(frame_data, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    # Save snapshot
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"snapshot_{camera.device_name}_{timestamp}.jpg"
                    filepath = os.path.join(snapshots_dir, filename)
                    
                    cv2.imwrite(filepath, frame)
                    
                    print(f"Snapshot taken successfully from active stream: {filename}")
                    return jsonify({
                        'success': True,
                        'message': 'Snapshot taken successfully',
                        'filename': filename,
                        'filepath': f'/static/snapshots/{filename}'
                    })
        
        # Fallback to direct camera capture
        print(f"Using direct camera capture for snapshot {device_id}")
        cap = cv2.VideoCapture(camera.rstplink)
        if not cap.isOpened():
            return jsonify({'success': False, 'error': 'Cannot connect to camera'}), 500
        
        # Set smaller resolution for snapshot
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        
        # Allow camera to warm up
        time.sleep(0.5)
        
        ret, frame = cap.read()
        cap.release()
        
        if ret and frame is not None:
            # Save snapshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"snapshot_{camera.device_name}_{timestamp}.jpg"
            filepath = os.path.join(snapshots_dir, filename)
            
            cv2.imwrite(filepath, frame)
            
            print(f"Snapshot taken successfully via direct capture: {filename}")
            return jsonify({
                'success': True,
                'message': 'Snapshot taken successfully',
                'filename': filename,
                'filepath': f'/static/snapshots/{filename}'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to capture frame'}), 500
            
    except Exception as e:
        print(f"Error taking snapshot for camera {device_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/start-recording/<int:device_id>')
def start_recording(device_id):
    """Start recording from camera"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    camera = Device.query.get(device_id)
    if not camera or not is_camera_type(camera.device_type):
        return jsonify({'success': False, 'error': 'Camera not found'}), 404
    
    try:
        with stream_lock:
            if device_id not in active_streams:
                return jsonify({'success': False, 'error': 'Camera stream not active'}), 400
            
            stream = active_streams[device_id]
            if stream.recording:
                return jsonify({'success': False, 'error': 'Recording already in progress'}), 400
            
            # Start recording
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{camera.device_name}_{timestamp}.avi"
            filepath = os.path.join(recordings_dir, filename)
            
            # Define video writer
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            stream.writer = cv2.VideoWriter(filepath, fourcc, 15.0, (640, 480))
            stream.recording = True
            
            print(f"Started recording for camera {device_id}: {filename}")
            return jsonify({
                'success': True,
                'message': 'Recording started',
                'filename': filename
            })
            
    except Exception as e:
        print(f"Error starting recording for camera {device_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/stop-recording/<int:device_id>')
def stop_recording(device_id):
    """Stop recording from camera"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        with stream_lock:
            if device_id not in active_streams:
                return jsonify({'success': False, 'error': 'Camera stream not active'}), 400
            
            stream = active_streams[device_id]
            if stream.recording and stream.writer:
                stream.writer.release()
                stream.writer = None
                stream.recording = False
                
                print(f"Stopped recording for camera {device_id}")
                return jsonify({
                    'success': True,
                    'message': 'Recording stopped'
                })
            else:
                return jsonify({'success': False, 'error': 'No recording in progress'}), 400
                
    except Exception as e:
        print(f"Error stopping recording for camera {device_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/status')
def get_camera_status():
    """Get status of all cameras"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        cameras = Device.query.filter(Device.device_type.in_(CAMERA_DEVICE_TYPES)).all()
        camera_status = []
        
        for camera in cameras:
            status = {
                'device_id': camera.device_id,
                'device_name': camera.device_name,
                'device_ip': camera.device_ip,
                'rtsp_link': camera.rstplink,
                'is_streaming': camera.device_id in active_streams,
                'is_recording': False,
                'clients': 0,
                'last_frame_time': 0,
                'last_error': None
            }
            
            if camera.device_id in active_streams:
                stream = active_streams[camera.device_id]
                status['is_recording'] = stream.recording
                status['clients'] = stream.streaming_clients
                status['last_frame_time'] = stream.last_frame_time
                status['is_running'] = stream.is_running
                status['last_error'] = stream.last_error
                
            camera_status.append(status)
        
        print(f"Returning status for {len(camera_status)} cameras")
        return jsonify({
            'success': True,
            'cameras': camera_status
        })
        
    except Exception as e:
        print(f"Error getting camera status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/control', methods=['POST'])
def camera_control():
    """Control camera streams (start/stop multiple)"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400
            
        action = data.get('action')
        camera_ids = data.get('camera_ids', [])
        
        if not action or action not in ['start', 'stop']:
            return jsonify({'success': False, 'error': 'Invalid action'}), 400
            
        if not camera_ids:
            return jsonify({'success': False, 'error': 'No camera IDs provided'}), 400
        
        print(f"Camera control request: action={action}, cameras={camera_ids}")
        
        results = []
        
        for camera_id in camera_ids:
            camera = Device.query.get(camera_id)
            if not camera or not is_camera_type(camera.device_type):
                results.append({
                    'camera_id': camera_id,
                    'success': False,
                    'error': 'Camera not found'
                })
                continue
            
            if action == 'start':
                success = start_camera_stream(camera_id, camera.rstplink)
                results.append({
                    'camera_id': camera_id,
                    'success': success,
                    'message': 'Stream started' if success else 'Failed to start stream'
                })
            elif action == 'stop':
                success = stop_camera_stream(camera_id)
                results.append({
                    'camera_id': camera_id,
                    'success': success,
                    'message': 'Stream stopped' if success else 'Stream not found'
                })
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        print(f"Error in camera control: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/cleanup', methods=['POST'])
def cleanup_streams():
    """Manually trigger stream cleanup"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        cleanup_idle_streams()
        return jsonify({
            'success': True,
            'message': 'Cleanup completed'
        })
    except Exception as e:
        print(f"Error in cleanup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@camera_bp.route('/api/cameras/test-connection/<int:device_id>')
def test_camera_connection(device_id):
    """Test camera connection"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    camera = Device.query.get(device_id)
    if not camera or not is_camera_type(camera.device_type):
        return jsonify({'success': False, 'error': 'Camera not found'}), 404
    
    try:
        print(f"Testing connection to camera {device_id}: {camera.rstplink}")
        cap = cv2.VideoCapture(camera.rstplink)
        
        if not cap.isOpened():
            return jsonify({
                'success': False,
                'error': 'Cannot connect to camera'
            })
        
        # Try to read a frame
        ret, frame = cap.read()
        cap.release()
        
        if ret and frame is not None:
            return jsonify({
                'success': True,
                'message': 'Camera connection successful',
                'frame_size': f"{frame.shape[1]}x{frame.shape[0]}"
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Connected but cannot read frames'
            })
            
    except Exception as e:
        print(f"Error testing camera connection: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Connection test failed: {str(e)}'
        })

# Initialize on import
print("Camera Streaming Module Initialized")
