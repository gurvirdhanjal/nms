import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db
from models.device import Device
from sqlalchemy import func

def deduplicate_devices():
    app = create_app()
    with app.app_context():
        print("--- Starting Device Deduplication ---")
        
        # 1. Find duplicate IPs
        # Group by IP, count > 1
        duplicates = db.session.query(Device.device_ip, func.count(Device.device_id))\
            .group_by(Device.device_ip)\
            .having(func.count(Device.device_id) > 1)\
            .all()
            
        print(f"Found {len(duplicates)} IPs with duplicate records.")
        
        for ip, count in duplicates:
            if not ip: continue
            
            # Get all devices with this IP
            devices = Device.query.filter_by(device_ip=ip).order_by(Device.updated_at.desc(), Device.created_at.desc()).all()
            
            # Keep the first one (most recently updated)
            primary = devices[0]
            to_delete = devices[1:]
            
            print(f"Processing IP {ip}: Keeping ID {primary.device_id} ({primary.device_name}), deleting {len(to_delete)} others.")
            
            for secondary in to_delete:
                # Merge logic (optional - copy missing fields to primary??)
                # For now, just simplistic merge of NON-NULL fields if primary is NULL
                if not primary.macaddress and secondary.macaddress:
                    primary.macaddress = secondary.macaddress
                if not primary.hostname and secondary.hostname:
                    primary.hostname = secondary.hostname
                if not primary.manufacturer and secondary.manufacturer:
                    primary.manufacturer = secondary.manufacturer
                
                # Delete secondary
                db.session.delete(secondary)
                
        db.session.commit()
        print("--- Deduplication Complete ---")

if __name__ == "__main__":
    deduplicate_devices()
