"""
SSE (Server-Sent Events) streaming endpoint for real-time dashboard updates.
"""
import uuid
from flask import Blueprint, Response, stream_with_context
from middleware.rbac import require_login
from services.sse_broadcaster import get_broadcaster

sse_bp = Blueprint('sse_bp', __name__, url_prefix='/api/events')


@sse_bp.before_request
@require_login
def _sse_auth_guard():
    return None


@sse_bp.route('/stream')
def event_stream():
    """
    SSE streaming endpoint.
    
    Returns a text/event-stream response that pushes real-time events
    to connected dashboard clients.
    
    Events include:
    - device_status: Device goes up/down
    - alert_created: New alert triggered  
    - latency_spike: Latency exceeds threshold
    - interface_threshold: Interface utilization crosses limit
    
    Headers:
    - Cache-Control: no-cache (prevent caching)
    - Connection: keep-alive (persistent connection)
    - X-Accel-Buffering: no (disable nginx buffering)
    """
    client_id = str(uuid.uuid4())
    broadcaster = get_broadcaster()
    
    def generate():
        """Generator that yields SSE events."""
        client_queue = broadcaster.register_client(client_id)
        
        try:
            # Send initial connection event
            yield f"event: connected\ndata: {{\"client_id\": \"{client_id[:8]}\", \"status\": \"connected\"}}\n\n"
            
            while True:
                try:
                    # Block for up to 30 seconds waiting for events
                    # This allows the heartbeat to keep the connection alive
                    message = client_queue.get(timeout=35)
                    yield message
                except Exception:
                    # Timeout - send heartbeat event as keep-alive
                    yield "event: heartbeat\ndata: {}\n\n"
                    
        except GeneratorExit:
            # Client disconnected
            pass
        finally:
            broadcaster.unregister_client(client_id)
    
    response = Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Access-Control-Allow-Origin': '*'
        }
    )
    
    return response


@sse_bp.route('/status')
def get_status():
    """Get SSE connection status (for debugging)."""
    broadcaster = get_broadcaster()
    return {
        'connected_clients': broadcaster.get_client_count(),
        'status': 'active'
    }
