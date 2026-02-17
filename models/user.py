from extensions import db
from datetime import datetime

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)         # Nullable for LDAP users
    role = db.Column(db.String(20), nullable=False, default='user')
    email = db.Column(db.String(120), unique=True, nullable=True)   # Nullable — LDAP may not provide
    phone_number = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    # LDAP integration
    auth_source = db.Column(db.String(20), default='local')     # 'local' | 'ldap'
    display_name = db.Column(db.String(100), nullable=True)
    external_id = db.Column(db.String(100), nullable=True)      # AD objectGUID

    @property
    def is_ldap(self):
        return self.auth_source == 'ldap'

    def __repr__(self):
        return f'<User {self.username} ({self.auth_source})>'
