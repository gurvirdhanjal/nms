"""
Debug test to understand why the bug doesn't manifest
"""

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
        print("Testing autoflush behavior...")
        print(f"SQLAlchemy autoflush setting: {db.session.autoflush}")
        print(f"SQLAlchemy autocommit setting: {db.session.autocommit}")
        
        test_ip = "192.168.99.250"
        
        # Clean up
        Device.query.filter_by(device_ip=test_ip).delete()
        db.session.commit()
        
        # Create device
        device = Device(
            device_name="Test Device",
            device_ip=test_ip,
            device_type="server",
            macaddress="AA:BB:CC:DD:EE:AA",
            hostname="test-device",
            manufacturer="Test"
        )
        db.session.add(device)
        
        print(f"\n1. After add, before query:")
        print(f"   device.device_id = {device.device_id}")
        
        # This query might trigger autoflush!
        existing = DeviceSnmpConfig.query.filter_by(device_id=device.device_id).first()
        
        print(f"\n2. After query:")
        print(f"   device.device_id = {device.device_id}")
        print(f"   existing config = {existing}")
        
        # Now try to create SNMP config
        if not existing:
            print(f"\n3. Creating SNMP config with device_id = {device.device_id}")
            config = DeviceSnmpConfig(device_id=device.device_id)
            config.community_string = "public"
            config.snmp_version = "2c"
            config.snmp_port = 161
            db.session.add(config)
        
        print(f"\n4. Before commit:")
        print(f"   device.device_id = {device.device_id}")
        
        db.session.commit()
        
        print(f"\n5. After commit:")
        print(f"   device.device_id = {device.device_id}")
        print(f"   SUCCESS!")
        
        # Cleanup
        DeviceSnmpConfig.query.filter_by(device_id=device.device_id).delete()
        Device.query.filter_by(device_ip=test_ip).delete()
        db.session.commit()
        
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {str(e)}")
        db.session.rollback()
