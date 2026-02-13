from extensions import db
from datetime import datetime

class ServerHealthLog(db.Model):
    __tablename__ = 'server_health_logs'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False)
    cpu_usage = db.Column(db.Float, nullable=True)
    memory_usage = db.Column(db.Float, nullable=True)
    memory_used_gb = db.Column(db.Float, nullable=True)
    memory_total_gb = db.Column(db.Float, nullable=True)
    disk_usage = db.Column(db.Float, nullable=True)
    disk_used_gb = db.Column(db.Float, nullable=True)
    disk_free_gb = db.Column(db.Float, nullable=True)
    disk_total_gb = db.Column(db.Float, nullable=True)
    network_in_bps = db.Column(db.Float, nullable=True)
    network_out_bps = db.Column(db.Float, nullable=True)
    uptime = db.Column(db.String(50), nullable=True) # Seconds or formatted string
    source = db.Column(db.String(20), nullable=True, default='agent')
    os_name = db.Column(db.String(100), nullable=True)
    os_version = db.Column(db.String(255), nullable=True)
    os_arch = db.Column(db.String(50), nullable=True)
    
    # Load Average
    load_avg_1min = db.Column(db.Float, nullable=True)
    load_avg_5min = db.Column(db.Float, nullable=True)
    load_avg_15min = db.Column(db.Float, nullable=True)
    
    # Swap Memory
    swap_total_mb = db.Column(db.Float, nullable=True)
    swap_used_mb = db.Column(db.Float, nullable=True)
    swap_percent = db.Column(db.Float, nullable=True)
    
    # Disk I/O
    disk_read_bytes = db.Column(db.BigInteger, nullable=True)
    disk_write_bytes = db.Column(db.BigInteger, nullable=True)
    disk_read_count = db.Column(db.BigInteger, nullable=True)
    disk_write_count = db.Column(db.BigInteger, nullable=True)
    
    # Network Connections
    network_connections_total = db.Column(db.Integer, nullable=True)
    network_connections_established = db.Column(db.Integer, nullable=True)
    
    # Processes
    process_count = db.Column(db.Integer, nullable=True)
    zombie_count = db.Column(db.Integer, nullable=True)
    
    # JSON fields for complex data
    top_processes = db.Column(db.JSON, nullable=True)  # Top 5 processes by memory
    alerts = db.Column(db.JSON, nullable=True)  # Active system alerts
    
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'memory_used_gb': self.memory_used_gb,
            'memory_total_gb': self.memory_total_gb,
            'disk_usage': self.disk_usage,
            'disk_used_gb': self.disk_used_gb,
            'disk_free_gb': self.disk_free_gb,
            'disk_total_gb': self.disk_total_gb,
            'uptime': self.uptime,
            'source': self.source,
            'os_name': self.os_name,
            'os_version': self.os_version,
            'os_arch': self.os_arch,
            'load_avg_1min': self.load_avg_1min,
            'load_avg_5min': self.load_avg_5min,
            'load_avg_15min': self.load_avg_15min,
            'swap_total_mb': self.swap_total_mb,
            'swap_used_mb': self.swap_used_mb,
            'swap_percent': self.swap_percent,
            'disk_read_bytes': self.disk_read_bytes,
            'disk_write_bytes': self.disk_write_bytes,
            'disk_read_count': self.disk_read_count,
            'disk_write_count': self.disk_write_count,
            'network_connections_total': self.network_connections_total,
            'network_connections_established': self.network_connections_established,
            'process_count': self.process_count,
            'zombie_count': self.zombie_count,
            'top_processes': self.top_processes,
            'alerts': self.alerts,
            'timestamp': self.timestamp.isoformat()
        }
