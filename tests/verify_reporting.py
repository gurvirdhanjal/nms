import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from flask import session

from datetime import datetime

def verify_executive_report():
    print("Verifying Executive Fleet Health Report API...")
    app = create_app()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['last_activity'] = datetime.utcnow().isoformat()
        
        response = client.get('/api/reports/executive?range=30d')
        
        if response.status_code != 200:
            print(f"FAILED: Status Code {response.status_code}")
            print(response.data)
            return
        
        data = response.get_json()
        print("Response Data loaded successfully.")
        
        required_keys = ['uptime_score', 'health_distribution', 'sla_metrics', 'top_problematic']
        missing = [k for k in required_keys if k not in data]
        
        if missing:
            print(f"FAILED: Missing keys {missing}")
        else:
            print("SUCCESS: Structure Valid")
            print(f"Uptime: {data['uptime_score']}%")
            print(f"Health: {data['health_distribution']}")
            print(f"Top Problematic Count: {len(data['top_problematic'])}")

if __name__ == "__main__":
    verify_executive_report()
