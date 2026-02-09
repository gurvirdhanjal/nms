from extensions import db
from datetime import datetime

class ServerHealthLog(db.Model):
    __tablename__ = 'server_health_logs'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id'), nullable=False)
    cpu_usage = db.Column(db.Float, nullable=True)
    memory_usage = db.Column(db.Float, nullable=True)
    disk_usage = db.Column(db.Float, nullable=True)
    network_in_bps = db.Column(db.Float, nullable=True)
    network_out_bps = db.Column(db.Float, nullable=True)
    uptime = db.Column(db.String(50), nullable=True) # Seconds or formatted string
    source = db.Column(db.String(20), nullable=True, default='agent')
    os_name = db.Column(db.String(100), nullable=True)
    os_version = db.Column(db.String(255), nullable=True)
    os_arch = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'disk_usage': self.disk_usage,
            'uptime': self.uptime,
            'source': self.source,
            'os_name': self.os_name,
            'os_version': self.os_version,
            'os_arch': self.os_arch,
            'timestamp': self.timestamp.isoformat()
        }
