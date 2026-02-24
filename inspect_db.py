import os
import sys
from flask import Flask
from extensions import db
from config import Config
from models.device import Device
from models.snmp_config import DeviceSnmpConfig
from sqlalchemy import inspect

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    inspector = inspect(db.engine)
    
    for table_name in ['device', 'device_snmp_config']:
        if table_name in inspector.get_table_names():
            print(f"Table: {table_name}")
            pk = inspector.get_pk_constraint(table_name)
            print(f"  Primary Key: {pk['constrained_columns']}")
            for column in inspector.get_columns(table_name):
                print(f"  - {column['name']}: {column['type']}")
        else:
            print(f"Table {table_name} NOT FOUND")
