import time
import requests
import json

def test_api():
    s = requests.Session()
    # Login
    print("Logging in...")
    login_res = s.post('http://localhost:5001/login', data={'username':'admin', 'password':'password'})
    if 'Invalid username or password' in login_res.text:
         print("Login failed! Checking alternate credentials...")
         s.post('http://localhost:5001/login', data={'username':'admin', 'password':'password123'})

    # Time live-summary
    print("Testing /api/tracking/live-summary...")
    t0 = time.time()
    r = s.get('http://localhost:5001/api/tracking/live-summary')
    ms = (time.time() - t0) * 1000
    
    try:
        data = r.json()
        print(f"API Response Time: {ms:.2f} ms")
        print(f"Success: {data.get('success')}")
        print(f"Total devices returned: {data.get('total_devices')}")
        if data.get('devices'):
             first = data['devices'][0]
             print(f"Sample device: {first['mac_address']} - Status: {first['status']} - Probe: {first['probe_method']}")
    except json.JSONDecodeError:
        print(f"Failed to decode JSON. Raw response: {r.text[:200]}")

if __name__ == '__main__':
    test_api()
