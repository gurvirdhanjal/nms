from app import create_app
from utils.db_migrations import ensure_server_health_columns

app = create_app()
with app.app_context():
    print("Running migrations...")
    ensure_server_health_columns()
    print("Migrations complete.")
