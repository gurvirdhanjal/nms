import sys
import os
import requests
sys.path.append(os.getcwd())
from app import create_app
from extensions import db
from models.device import Device

app = create_app()
with app.app_context():
    # Fetch agent info
    try:
        resp = requests.get("http://127.0.0.1:5002/api/identity", timeout=5)
        if resp.status_code == 200:
            agent_data = resp.json()
            print(f"Retrieved agent data: {agent_data}")
            
            dev = Device.query.filter_by(device_ip='127.0.0.1').first()
            if dev:
                dev.mac_address = agent_data.get('mac_address')
                dev.os_version = agent_data.get('os')
                dev.agent_version = "2.0"
                db.session.commit()
                print("Updated device with agent details.")
            else:
                print("Local device not found in DB.")
        else:
            print(f"Failed to fetch identity: {resp.status_code}")
    except Exception as e:
        print(f"Error updating agent details: {e}")
