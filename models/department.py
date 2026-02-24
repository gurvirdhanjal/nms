from extensions import db
from datetime import datetime


class Department(db.Model):
    """Organizational department for device and user scoping."""
    __tablename__ = 'departments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    description = db.Column(db.Text, nullable=True)

    # Optional site association
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id', ondelete='SET NULL'), nullable=True, index=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    site = db.relationship('Site', backref=db.backref('departments', lazy='dynamic'))
    users = db.relationship('User', backref='department', lazy='dynamic')
    devices = db.relationship('Device', backref='department', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'site_id': self.site_id,
            'user_count': self.users.count() if self.users else 0,
            'device_count': self.devices.count() if self.devices else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f'<Department {self.name}>'
