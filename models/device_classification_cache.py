from datetime import datetime
from extensions import db


class DeviceClassificationCache(db.Model):
    """
    Persistent cache for Gemini-powered device classification results.
    Keyed by fingerprint_hash (SHA-256 of stable signals: OUI + sorted ports + manufacturer).
    Prevents duplicate Gemini API calls for the same device fingerprint.
    source: 'gemini' | 'manual_override'
    """
    __tablename__ = 'device_classification_cache'

    fingerprint_hash = db.Column(db.String(64), primary_key=True)
    device_type      = db.Column(db.String(50), nullable=False)
    confidence       = db.Column(db.String(20), nullable=False, default='medium')
    reasoning        = db.Column(db.Text, nullable=True)
    source           = db.Column(db.String(20), nullable=False, default='gemini')
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
