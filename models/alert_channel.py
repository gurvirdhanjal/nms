"""
AlertChannel — configurable alert delivery channels (email, Slack, Teams).

Routing is handled by services/alert_routing_service.py.
Transport is handled by services/notification_service.send_via_channel().

config_json schema per channel_type:
  email: { "recipients": ["a@b.com", ...] }
  slack: { "webhook_url": "<encrypted>" }
  teams: { "webhook_url": "<encrypted>" }
"""
from datetime import datetime
from extensions import db

_REQUIRED_CONFIG_KEYS = {
    'email': ['recipients'],
    'slack': ['webhook_url'],
    'teams': ['webhook_url'],
}

VALID_CHANNEL_TYPES = tuple(_REQUIRED_CONFIG_KEYS.keys())


class AlertChannel(db.Model):
    __tablename__ = 'alert_channels'

    id                     = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name                   = db.Column(db.String(100), nullable=False, unique=True)
    channel_type           = db.Column(db.String(20), nullable=False)   # email / slack / teams
    config_json            = db.Column(db.JSON, nullable=True)
    is_enabled             = db.Column(db.Boolean, nullable=False, default=True)
    send_on_critical       = db.Column(db.Boolean, nullable=False, default=True)
    send_on_warning        = db.Column(db.Boolean, nullable=False, default=False)
    applicable_device_types = db.Column(db.JSON, nullable=True)          # [] = all types
    created_at             = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at             = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<AlertChannel id={self.id} name={self.name!r} type={self.channel_type}>'

    def to_dict(self, mask_secrets=True):
        config = dict(self.config_json or {})
        if mask_secrets and self.channel_type in ('slack', 'teams') and 'webhook_url' in config:
            config['webhook_url'] = '••••••'
        return {
            'id':                      self.id,
            'name':                    self.name,
            'channel_type':            self.channel_type,
            'config_json':             config,
            'is_enabled':              self.is_enabled,
            'send_on_critical':        self.send_on_critical,
            'send_on_warning':         self.send_on_warning,
            'applicable_device_types': self.applicable_device_types or [],
            'created_at':              self.created_at.isoformat() if self.created_at else None,
            'updated_at':              self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def validate_config(cls, channel_type, config_json):
        """Return (ok: bool, error: str | None). Validates required keys per type."""
        required = _REQUIRED_CONFIG_KEYS.get(channel_type)
        if required is None:
            return False, f'Unknown channel_type "{channel_type}"'
        config = config_json or {}
        for key in required:
            if not config.get(key):
                return False, f'config_json.{key} is required for {channel_type} channels'
        if channel_type == 'email':
            recipients = config.get('recipients', [])
            if isinstance(recipients, str):
                recipients = [r.strip() for r in recipients.split(',') if r.strip()]
            if not recipients:
                return False, 'At least one recipient email address is required'
        return True, None
