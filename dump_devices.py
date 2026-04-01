import sys
import os
sys.path.append(os.getcwd())
from app import create_app
from models.device import Device

app = create_app()
with app.app_context():
    print("DEVICE_LIST_START")
    for d in Device.query.all():
        print(f"ID:{d.device_id}|Name:{d.device_name}|IP:{d.device_ip}|Type:{d.device_type}")
    print("DEVICE_LIST_END")
