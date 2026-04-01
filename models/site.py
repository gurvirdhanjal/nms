from extensions import db
from datetime import datetime
from sqlalchemy import func, or_


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
    created_by = db.Column(db.String(80), nullable=True)

    # Relationships
    devices = db.relationship('Device', backref='site', lazy='dynamic')
    subnets = db.relationship('Subnet', backref='site', lazy='dynamic', cascade='all, delete-orphan')
    users = db.relationship('User', backref='site', lazy='dynamic')

    def to_dict(self, device_count=None):
        # Caller may pass pre-computed device_count to avoid N+1 queries.
        if device_count is None:
            from models.device import Device
            departments = self.departments.all() if hasattr(self, 'departments') else []
            dept_ids = [d.id for d in departments]
            if dept_ids:
                device_count = Device.query.filter(
                    db.or_(
                        Device.site_id == self.id,
                        Device.department_id.in_(dept_ids)
                    )
                ).count()
            else:
                device_count = self.devices.count() if self.devices else 0

        return {
            'id': self.id,
            'site_name': self.site_name,
            'site_code': self.site_code,
            'address': self.address,
            'timezone': self.timezone,
            'contact_name': self.contact_name,
            'contact_email': self.contact_email,
            'contact_phone': self.contact_phone,
            'device_count': device_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by or 'SYSTEM',
        }

    @classmethod
    def get_all_with_device_counts(cls, base_query=None):
        """
        Return (site, device_count) tuples using a single aggregate query.
        device_count includes devices assigned to the site directly OR via a department.
        Pass a pre-filtered base_query (e.g. from scoped_query) to respect RBAC scope.
        """
        from models.device import Device
        from models.department import Department

        q = base_query if base_query is not None else db.session.query(cls)
        site_ids = [s.id for s in q.with_entities(cls.id).all()]
        if not site_ids:
            return []

        sites = q.order_by(cls.site_name).all()

        # Get dept_ids per site in bulk
        dept_map: dict[int, list[int]] = {s.id: [] for s in sites}
        dept_rows = (
            db.session.query(Department.id, Department.site_id)
            .filter(Department.site_id.in_(site_ids))
            .all()
        )
        for dept_id, site_id in dept_rows:
            dept_map[site_id].append(dept_id)

        # Bulk device count per site (direct + via department)
        all_dept_ids = [d for ids in dept_map.values() for d in ids]
        count_by_site: dict[int, int] = {s.id: 0 for s in sites}

        # Direct site_id assignments
        direct_rows = (
            db.session.query(Device.site_id, func.count(Device.device_id))
            .filter(Device.site_id.in_(site_ids))
            .group_by(Device.site_id)
            .all()
        )
        for site_id, cnt in direct_rows:
            count_by_site[site_id] = count_by_site.get(site_id, 0) + cnt

        # Department-based assignments (only count devices not already counted via site_id)
        if all_dept_ids:
            dept_rows2 = (
                db.session.query(Department.site_id, func.count(Device.device_id))
                .join(Device, Device.department_id == Department.id)
                .filter(
                    Department.site_id.in_(site_ids),
                    Device.site_id.is_(None),  # avoid double-counting
                )
                .group_by(Department.site_id)
                .all()
            )
            for site_id, cnt in dept_rows2:
                count_by_site[site_id] = count_by_site.get(site_id, 0) + cnt

        return [(s, count_by_site.get(s.id, 0)) for s in sites]

    def __repr__(self):
        return f'<Site {self.site_name} ({self.site_code})>'
