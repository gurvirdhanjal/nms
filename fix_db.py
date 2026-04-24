from app import create_app
from extensions import db
from models.device import Device

app = create_app()
with app.app_context():
    d2 = Device.query.get(8848)
    if d2:
        print(f"Renaming IP of conflicting device 8848 from {d2.device_ip} to None to clear collision")
        d2.device_ip = None

    d1 = Device.query.get(8745)
    if d1:
        d1.device_ip = '172.16.2.110'
        d1.device_type = 'server'
        print(f"Updated device 8745: IP={d1.device_ip}, Type={d1.device_type}")
    
    db.session.commit()
    print("Database successfully updated.")
