import os
import sys
from flask import Flask
from extensions import db
from config import Config
from models.device import Device
from models.snmp_config import DeviceSnmpConfig

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    try:
        print("Starting test device creation...")
        test_ip = "192.168.99.99"
        # Cleanup if exists
        Device.query.filter_by(device_ip=test_ip).delete()
        db.session.commit()
        
        device = Device(
            device_name="Test Device",
            device_ip=test_ip,
            device_type="server"
        )
        db.session.add(device)
        print(f"Device added to session. ID before flush: {device.device_id}")
        
        db.session.flush()
        print(f"Device flushed. ID after flush: {device.device_id}")
        
        if device.device_id is None:
            print("ERROR: device_id is still None after flush!")
        else:
            print("Success! device_id is populated.")
            
        db.session.rollback() # Don't actually save
    except Exception as e:
        print(f"Caught exception: {e}")
        db.session.rollback()
