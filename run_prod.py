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
    # threads=16: SSE /api/events/stream holds one thread per open browser tab.
    # 16 fits inside the DB pool (pool_size=20 + overflow=10 = 30 max) while
    # still giving headroom for ~6 SSE tabs + concurrent normal requests.
    # send_bytes=0: flush each yielded chunk immediately (required for MJPEG/audio/SSE streams).
    # Without this Waitress buffers up to 18 KB before sending, breaking real-time decode.
    serve(app, host='0.0.0.0', port=port, threads=16, send_bytes=0)
