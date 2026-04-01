import os
from app import create_app
from extensions import db, bcrypt
from models.user import User

app = create_app()
with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        hashed_password = bcrypt.generate_password_hash('admin').decode('utf-8')
        admin = User(username='admin', password=hashed_password, role='admin', is_active=True)
        db.session.add(admin)
        db.session.commit()
        print("Created admin user with password 'admin'")
    else:
        admin.password = bcrypt.generate_password_hash('admin').decode('utf-8')
        db.session.commit()
        print("Updated admin user password to 'admin'")
