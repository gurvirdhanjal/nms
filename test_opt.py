from app import create_app
from extensions import db
from models.device import Device
from models.snmp_config import DeviceSnmpConfig
from middleware.rbac import scoped_query

app = create_app()
with app.app_context():
    # Admin context
    from flask import g
    g.user_role = 'admin'

    q = scoped_query(Device).join(
        DeviceSnmpConfig, Device.device_id == DeviceSnmpConfig.device_id
    ).filter(DeviceSnmpConfig.is_enabled == True)

    print(q)
    print("Count:", q.count())
