from app import create_app
from extensions import db, bcrypt
from models.user import User
import getpass

def reset_password():
    app = create_app()
    with app.app_context():
        username = input("Enter username to reset [admin]: ").strip() or "admin"
        new_password = getpass.getpass(f"Enter new password for '{username}': ")
        confirm_password = getpass.getpass("Confirm new password: ")
        
        if new_password != confirm_password:
            print("❌ Passwords do not match!")
            return

        user = User.query.filter_by(username=username).first()
        if user:
            # Force update password hash
            user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
            db.session.commit()
            print(f"✅ Success! Password for user '{username}' has been updated.")
        else:
            print(f"❌ Error: User '{username}' not found.")

if __name__ == "__main__":
    reset_password()

