"""
ComplianceProfile — per-device threshold override profiles.

rules_json is a flat dict of threshold overrides, keyed by a
short human-readable name.  Only keys that are present override
the global default; everything else falls through.

Supported keys (all values are numeric):
    cpu_warning       cpu_critical
    memory_warning    memory_critical
    disk_warning      disk_critical

Example:
    {"cpu_warning": 70, "cpu_critical": 85,
     "disk_warning": 75, "memory_warning": 80}

See services/alert_manager.py :: _RULES_JSON_MAP for the full
mapping to METRIC_CATALOG keys.
"""
from datetime import datetime
from extensions import db


class ComplianceProfile(db.Model):
    __tablename__ = 'compliance_profiles'

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name        = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    rules_json  = db.Column(db.JSON, nullable=False, default=dict)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<ComplianceProfile id={self.id} name={self.name!r}>'

    def to_dict(self):
        rj = self.rules_json or {}
        return {
            'id':          self.id,
            'name':        self.name,
            'description': self.description,
            'rules_json':  rj,
            # Explicitly surface ICMP keys for API consumers (Phase 6)
            'latency_warning_ms':       rj.get('latency_warning_ms'),
            'latency_critical_ms':      rj.get('latency_critical_ms'),
            'packet_loss_warning_pct':  rj.get('packet_loss_warning_pct'),
            'packet_loss_critical_pct': rj.get('packet_loss_critical_pct'),
            'applicable_device_types':  rj.get('applicable_device_types', []),
            'created_at':  self.created_at.isoformat() if self.created_at else None,
        }
