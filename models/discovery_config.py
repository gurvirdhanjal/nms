from extensions import db
from datetime import datetime, timezone
import json


class DiscoveryConfig(db.Model):
    """Singleton configuration for auto-discovery.

    Only ONE row should ever exist (id=1).  Use ``get_config()`` to
    retrieve it (creates a default row if missing).
    """

    __tablename__ = "discovery_config"

    id = db.Column(db.Integer, primary_key=True, default=1)

    # Master switch
    enabled = db.Column(db.Boolean, default=False)

    # Subnets to scan (JSON list of CIDR strings)
    _subnets = db.Column("subnets", db.Text, default='["192.168.1.0/24"]')

    # Intervals (minutes)
    light_interval_min = db.Column(db.Integer, default=30)
    heavy_interval_min = db.Column(db.Integer, default=1440)   # 24 h

    # Performance knobs
    max_concurrent_pings = db.Column(db.Integer, default=50)
    ping_timeout = db.Column(db.Integer, default=2)

    # Auto-add policy
    auto_add_policy = db.Column(db.String(20), default="auto")   # auto | approval
    auto_add_after_n = db.Column(db.Integer, default=2)          # consecutive detections
    auto_monitor_new = db.Column(db.Boolean, default=True)

    # Last-run stats
    last_light_scan = db.Column(db.DateTime, nullable=True)
    last_heavy_scan = db.Column(db.DateTime, nullable=True)
    last_scan_duration = db.Column(db.Float, nullable=True)
    last_new_count = db.Column(db.Integer, default=0)
    last_updated_count = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text, nullable=True)

    # ---- helpers ----

    @property
    def subnets(self):
        try:
            return json.loads(self._subnets or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @subnets.setter
    def subnets(self, value):
        if isinstance(value, list):
            self._subnets = json.dumps(value)
        else:
            self._subnets = value

    @staticmethod
    def _iso_utc(dt):
        if not dt:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.astimezone(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "subnets": self.subnets,
            "light_interval_min": self.light_interval_min,
            "heavy_interval_min": self.heavy_interval_min,
            "max_concurrent_pings": self.max_concurrent_pings,
            "ping_timeout": self.ping_timeout,
            "auto_add_policy": self.auto_add_policy,
            "auto_add_after_n": self.auto_add_after_n,
            "auto_monitor_new": self.auto_monitor_new,
            "last_light_scan": self._iso_utc(self.last_light_scan),
            "last_heavy_scan": self._iso_utc(self.last_heavy_scan),
            "last_scan_duration": self.last_scan_duration,
            "last_new_count": self.last_new_count,
            "last_updated_count": self.last_updated_count,
            "last_error": self.last_error,
        }


def get_config() -> DiscoveryConfig:
    """Return the singleton config row, creating defaults if absent."""
    cfg = DiscoveryConfig.query.get(1)
    if cfg is None:
        cfg = DiscoveryConfig(id=1)
        db.session.add(cfg)
        db.session.commit()
    return cfg
