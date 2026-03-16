import sys
import os
sys.path.append(os.getcwd())
from app import create_app
from extensions import db
from models.device import Device

app = create_app()
with app.app_context():
    # Check if a device with 127.0.0.1 exists
    local_dev = Device.query.filter_by(device_ip='127.0.0.1').first()
    if not local_dev:
        print("Adding local workstation (127.0.0.1) to database...")
        new_dev = Device(
            device_name="LocalWorkstation",
            device_ip="127.0.0.1",
            device_type="workstation",
            is_monitored=True,
            monitoring_mode="agent"
        )
        db.session.add(new_dev)
        db.session.commit()
        print(f"Added device ID: {new_dev.device_id}")
    else:
        print(f"Device already exists: {local_dev.device_name} (ID: {local_dev.device_id})")
        local_dev.monitoring_mode = 'agent'
        local_dev.is_monitored = True
        db.session.commit()
        print("Updated monitoring mode to 'agent'")
