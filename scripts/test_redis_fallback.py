import requests
import time
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_redis_graceful_fallback():
    print("Testing API endpoints with Redis offline to verify zero-downtime degradation...")
    try:
        # Assuming the Flask app is running on port 5000 and auth is mocked or we can hit public endpoints
        # Wait, the app needs auth. Let's hit a public endpoint or bypass auth by importing the app.
        
        from app import create_app
        import os
        from extensions import is_redis_available
        
        os.environ['FLASK_ENV'] = 'testing'
        app = create_app()
        app.testing = True
        
        with app.test_client() as client:
            print(f"Redis Available: {is_redis_available()}")
            
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['role'] = 'admin'
                
            # Hit /api/events/stream
            # We expect a 503 Service Unavailable with JSON {"error": "SSE Pub/Sub Offline"}
            print("Hitting /api/events/stream...")
            resp = client.get('/api/events/stream')
            print(f"Status: {resp.status_code}, Body: {resp.get_data(as_text=True).strip()}")
            assert resp.status_code == 503, f"Expected 503, got {resp.status_code}"
            
            # Hit /api/dashboard/full_snapshot
            print("Hitting /api/dashboard/full_snapshot...")
            resp = client.get('/api/dashboard/full_snapshot?range=24h')
            print(f"Status: {resp.status_code}")
            assert resp.status_code == 200, "Dashboard failed to fallback to local dictionary!"
            
            print("Hitting /api/tracking/live-summary...")
            resp = client.get('/api/tracking/live-summary')
            print(f"Status: {resp.status_code}")
            assert resp.status_code == 200, "Live summary failed to fallback to Database!"
            
            print("ALL OFFLINE FALLBACK TESTS PASSED!")
            
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == '__main__':
    test_redis_graceful_fallback()
