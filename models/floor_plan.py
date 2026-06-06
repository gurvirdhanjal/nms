from extensions import db
from datetime import datetime


class FloorPlan(db.Model):
    """An uploaded floor-plan / plant-map image for a site.

    A site can have several plans (e.g. "Ground Floor", "Plant A"). Devices are
    placed on at most one plan via Device.floor_plan_id + map_x/map_y (percent
    coordinates), so marker positions are resolution-independent.

    The uploaded file (PNG/JPG, or a PDF rasterised to PNG on upload) is stored
    outside static/ and served only through an authenticated route so RBAC is
    preserved.
    """

    __tablename__ = 'floor_plans'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey('sites.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    name = db.Column(db.String(200), nullable=False)

    # Stored normalised image (always a raster the browser can render directly).
    image_filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    image_width = db.Column(db.Integer, nullable=True)
    image_height = db.Column(db.Integer, nullable=True)

    sort_order = db.Column(db.Integer, default=0, nullable=False)

    # Incremented each time the image is replaced (e.g. "Ground Floor v2").
    # Device coordinates are preserved across a re-upload, which is the common
    # factory case where the layout is re-drawn but machines stay put.
    version = db.Column(db.Integer, default=1, nullable=False, server_default='1')

    created_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    site = db.relationship('Site', backref=db.backref('floor_plans', lazy='dynamic'))
    devices = db.relationship(
        'Device',
        backref=db.backref('floor_plan', lazy=True),
        lazy='dynamic',
        foreign_keys='Device.floor_plan_id',
    )

    def to_dict(self, device_count=None):
        return {
            'id': self.id,
            'site_id': self.site_id,
            'name': self.name,
            'image_url': f'/api/floor-plans/{self.id}/image?v={self.version}',
            'original_filename': self.original_filename,
            'mime_type': self.mime_type,
            'image_width': self.image_width,
            'image_height': self.image_height,
            'sort_order': self.sort_order,
            'version': self.version,
            'device_count': device_count,
            'created_by': self.created_by or 'SYSTEM',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<FloorPlan {self.name} (site={self.site_id})>'
