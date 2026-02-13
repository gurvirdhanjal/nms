import sys
from unittest.mock import MagicMock

# MOCK CONFIG
sys.modules['config'] = MagicMock()
sys.modules['config'].Config.API_KEY = 'test_key'

# MOCK MODELS
mock_models = MagicMock()
sys.modules['models.tracked_device'] = mock_models
sys.modules['models'] = mock_models

# NOW IMPORT TARGET
# We need to ensure extensions is mocking db too?
sys.modules['extensions'] = MagicMock()
sys.modules['extensions'].db = MagicMock()

try:
    from flask import Flask
    from routes.tracking import tracking_bp
    import routes.tracking 
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

# SETUP APP
app = Flask(__name__)
app.register_blueprint(tracking_bp)
client = app.test_client()

def test_audio_route():
    print("Testing /api/tracking/stream/audio/...")
    
    # Mock Device
    mock_device = MagicMock()
    mock_device.ip_address = '127.0.0.1'
    
    # Configure Query
    # The route calls TrackedDevice.query.filter_by(mac_address=...).first()
    # We need to access the class that was imported in routes.tracking
    
    # Since we mocked the module 'models.tracked_device', 
    # the imported TrackedDevice in routes.tracking is a MagicMock.
    
    routes.tracking.TrackedDevice.query.filter_by.return_value.first.return_value = mock_device

    # Mock requests.get
    # route calls requests.get(..., stream=True)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_content.return_value = [b"audio_chunk"]
    
    routes.tracking.requests.get = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_response)))

    # CALL
    resp = client.get('/api/tracking/stream/audio/00:11:22:33:44:55')
    
    if resp.status_code == 200:
        print("PASS: Status 200")
    else:
        print(f"FAIL: Status {resp.status_code}")
        print(resp.json)

def test_toggle_mic():
    print("Testing /api/tracking/toggle-mic/...")
    
    # Reset mocks
    mock_device = MagicMock()
    mock_device.ip_address = '127.0.0.1'
    routes.tracking.TrackedDevice.query.filter_by.return_value.first.return_value = mock_device

    # Mock responses for logic: 
    # 1. Status check -> Active
    # 2. Stop call -> OK
    mock_status = MagicMock()
    mock_status.status_code = 200
    mock_status.json.return_value = {'active': True}
    
    mock_stop = MagicMock()
    mock_stop.status_code = 200
    
    # Custom side effect to trace calls
    def get_side_effect(*args, **kwargs):
        url = args[0] if args else kwargs.get('url')
        print(f"DEBUG: requests.get called for {url}")
        if 'mic_status' in url:
            return mock_status
        elif 'stop_mic' in url:
            return mock_stop
        elif 'audio_stream' in url:
             # handle possible extra call?
             return mock_status # fallback
        else:
             raise ValueError(f"Unexpected URL: {url}")

    routes.tracking.requests.get = MagicMock(side_effect=get_side_effect)
    
    resp = client.post('/api/tracking/toggle-mic/00:11:22:33:44:55')
    
    if resp.status_code == 200 and resp.json.get('action') == 'stopped':
        print("PASS: Toggled/Stopped")
    else:
        print(f"FAIL: {resp.status_code}")
        print(f"Response: {resp.data}")
        print(f"JSON: {resp.json}")


if __name__ == "__main__":
    try:
        test_audio_route()
        test_toggle_mic()
    except Exception as e:
        print(f"SCRIPT CRASH: {e}")
        import traceback
        traceback.print_exc()
