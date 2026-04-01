from extensions import db
from datetime import datetime
from sqlalchemy import func, distinct


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
    created_by = db.Column(db.String(80), nullable=True)

    # Relationships
    site = db.relationship('Site', backref=db.backref('departments', lazy='dynamic'))
    users = db.relationship('User', backref='department', lazy='dynamic')
    devices = db.relationship('Device', backref='department', lazy='dynamic')

    def to_dict(self, user_count=None, device_count=None):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'site_id': self.site_id,
            # Caller may pass pre-computed counts to avoid N+1 queries.
            'user_count': user_count if user_count is not None else (self.users.count() if self.users else 0),
            'device_count': device_count if device_count is not None else (self.devices.count() if self.devices else 0),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': self.created_by or 'SYSTEM',
        }

    @classmethod
    def get_all_with_counts(cls, base_query=None):
        """
        Return (dept, user_count, device_count) tuples in a single query.
        Pass a pre-filtered base_query (e.g. from scoped_query) to respect RBAC scope.
        """
        from models.user import User
        from models.device import Device

        q = base_query if base_query is not None else db.session.query(cls)
        # Subquery to get the IDs we care about
        dept_ids = [d.id for d in q.with_entities(cls.id).all()]
        if not dept_ids:
            return []

        rows = (
            db.session.query(
                cls,
                func.count(distinct(User.id)).label('user_count'),
                func.count(distinct(Device.device_id)).label('device_count'),
            )
            .outerjoin(User, User.department_id == cls.id)
            .outerjoin(Device, Device.department_id == cls.id)
            .filter(cls.id.in_(dept_ids))
            .group_by(cls.id)
            .order_by(cls.name)
            .all()
        )
        return rows

    def __repr__(self):
        return f'<Department {self.name}>'
