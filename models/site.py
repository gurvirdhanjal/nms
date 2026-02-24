from extensions import db
from datetime import datetime


class Site(db.Model):
    __tablename__ = 'sites'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    site_code = db.Column(db.String(50), nullable=True, unique=True, index=True)

    # Location
    address = db.Column(db.Text, nullable=True)
    timezone = db.Column(db.String(50), default='UTC')

    # Contact
    contact_name = db.Column(db.String(200), nullable=True)
    contact_email = db.Column(db.String(200), nullable=True)
    contact_phone = db.Column(db.String(50), nullable=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    devices = db.relationship('Device', backref='site', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'site_name': self.site_name,
            'site_code': self.site_code,
            'address': self.address,
            'timezone': self.timezone,
            'contact_name': self.contact_name,
            'contact_email': self.contact_email,
            'contact_phone': self.contact_phone,
            'device_count': self.devices.count() if self.devices else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<Site {self.site_name} ({self.site_code})>'
