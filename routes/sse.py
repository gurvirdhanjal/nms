"""
SSE (Server-Sent Events) streaming endpoint.
Subscribes to Redis Pub/Sub directly inside the generator object.
"""
import uuid
import json
from flask import Blueprint, Response, stream_with_context, jsonify
from middleware.rbac import require_login
from services.sse_broadcaster import get_broadcaster, SSE_CHANNEL
from extensions import redis_client

sse_bp = Blueprint('sse_bp', __name__, url_prefix='/api/events')

@sse_bp.before_request
@require_login
def _sse_auth_guard():
    return None

@sse_bp.route('/stream')
def event_stream():
    broadcaster = get_broadcaster()
    
    # Graceful Fallback check
    if not broadcaster or not redis_client:
        # Return 503 so the frontend JS closes the EventSource and falls back to polling!
        return jsonify({"error": "SSE Pub/Sub Offline"}), 503
        
    client_id = str(uuid.uuid4())
    
    def generate():
        pubsub = redis_client.pubsub()
        pubsub.subscribe(SSE_CHANNEL)
        
        try:
            yield f"event: connected\ndata: {json.dumps({'client_id': client_id[:8], 'status': 'connected'})}\n\n"
            
            while True:
                # Use get_message with timeout to avoid blocking forever, enabling GeneratorExit to fire
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                
                if message is not None:
                    try:
                        data = json.loads(message['data'])
                        event_type = data.get('event_type', 'message')
                        import uuid
                        event_id = str(uuid.uuid4())
                        yield f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
                    except Exception:
                        pass
                else:
                    # Keep-alive heartbeat every 5 seconds of silence
                    yield ": heartbeat\n\n"
                    
        except GeneratorExit:
            # Clean exit when client disconnects
            pass
        finally:
            pubsub.close()
            
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*'
        }
    )

@sse_bp.route('/status')
def get_status():
    if not get_broadcaster():
        return jsonify({'status': 'offline', 'error': 'Redis unavailable'}), 503
    return jsonify({'status': 'active'})
