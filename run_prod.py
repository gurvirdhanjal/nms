from waitress import serve
from app import create_app
import os

# Create the application instance
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Starting Production Server on port {port}...")
    print(f"Access at http://localhost:{port}")
    
    # Serve using Waitress (Production strength WSGI server)
    serve(app, host='0.0.0.0', port=port, threads=6)
