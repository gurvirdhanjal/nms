from extensions import db
from datetime import datetime

class AuditLog(db.Model):
    """Immutable audit trail for sensitive operations."""
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    
    # Who performed the action
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True, index=True)
    username = db.Column(db.String(80), nullable=False)  # Denormalized for immutability
    user_role = db.Column(db.String(20), nullable=False)  # Role at time of action
    
    # What action was performed
    action = db.Column(db.String(50), nullable=False, index=True)  # create, update, delete, login, etc.
    entity_type = db.Column(db.String(50), nullable=False, index=True)  # device, user, site, department, etc.
    entity_id = db.Column(db.Integer, nullable=True, index=True)  # ID of affected entity
    entity_name = db.Column(db.String(200), nullable=True)  # Denormalized name for readability
    
    # Additional context
    description = db.Column(db.Text, nullable=True)  # Human-readable description
    changes = db.Column(db.JSON, nullable=True)  # Before/after values for updates
    ip_address = db.Column(db.String(50), nullable=True)  # Client IP
    user_agent = db.Column(db.String(200), nullable=True)  # Browser/client info
    
    # Outcome
    success = db.Column(db.Boolean, nullable=True)       # True=ok, False=failed, None=not applicable
    error_detail = db.Column(db.Text, nullable=True)     # exception message or failure reason

    # When it happened
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('audit_logs', lazy='dynamic'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'user_role': self.user_role,
            'action': self.action,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'entity_name': self.entity_name,
            'description': self.description,
            'changes': self.changes,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'success': self.success,
            'error_detail': self.error_detail,
        }
    
    def __repr__(self):
        return f'<AuditLog {self.username} {self.action} {self.entity_type} {self.entity_id}>'
