from datetime import datetime

from extensions import db


class ServerHealthHourlyRollup(db.Model):
    __tablename__ = 'server_health_hourly_rollups'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        nullable=False,
    )
    source = db.Column(db.String(20), nullable=False, default='agent')
    bucket_hour = db.Column(db.DateTime, nullable=False, index=True)

    avg_cpu_usage = db.Column(db.Float, nullable=True)
    max_cpu_usage = db.Column(db.Float, nullable=True)
    avg_memory_usage = db.Column(db.Float, nullable=True)
    max_memory_usage = db.Column(db.Float, nullable=True)
    avg_disk_usage = db.Column(db.Float, nullable=True)
    avg_network_in_bps = db.Column(db.Float, nullable=True)
    avg_network_out_bps = db.Column(db.Float, nullable=True)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    online_samples = db.Column(db.Integer, nullable=True, default=0)

    # ICMP metrics (from source='icmp' rows)
    avg_ping_latency_ms = db.Column(db.Float, nullable=True)
    max_ping_latency_ms = db.Column(db.Float, nullable=True)
    avg_packet_loss_pct = db.Column(db.Float, nullable=True)
    max_packet_loss_pct = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            'device_id',
            'source',
            'bucket_hour',
            name='uq_server_health_hourly_device_source_bucket',
        ),
        db.Index('idx_server_health_hourly_device_bucket', 'device_id', 'bucket_hour'),
    )


class ServerHealthDailyRollup(db.Model):
    __tablename__ = 'server_health_daily_rollups'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        nullable=False,
    )
    source = db.Column(db.String(20), nullable=False, default='agent')
    bucket_day = db.Column(db.Date, nullable=False, index=True)

    avg_cpu_usage = db.Column(db.Float, nullable=True)
    max_cpu_usage = db.Column(db.Float, nullable=True)
    avg_memory_usage = db.Column(db.Float, nullable=True)
    max_memory_usage = db.Column(db.Float, nullable=True)
    avg_disk_usage = db.Column(db.Float, nullable=True)
    avg_network_in_bps = db.Column(db.Float, nullable=True)
    avg_network_out_bps = db.Column(db.Float, nullable=True)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    online_samples = db.Column(db.Integer, nullable=True, default=0)

    # ICMP metrics (from source='icmp' rows)
    avg_ping_latency_ms = db.Column(db.Float, nullable=True)
    max_ping_latency_ms = db.Column(db.Float, nullable=True)
    avg_packet_loss_pct = db.Column(db.Float, nullable=True)
    max_packet_loss_pct = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            'device_id',
            'source',
            'bucket_day',
            name='uq_server_health_daily_device_source_bucket',
        ),
        db.Index('idx_server_health_daily_device_bucket', 'device_id', 'bucket_day'),
    )


class ServerHealthRollupState(db.Model):
    __tablename__ = 'server_health_rollup_state'

    name = db.Column(db.String(64), primary_key=True)
    rolled_until = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
