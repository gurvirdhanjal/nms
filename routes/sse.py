"""
SSE (Server-Sent Events) streaming endpoint.

Uses a shared Redis subscriber per Python process and local in-memory fanout to
connected browser tabs. This keeps Redis-backed realtime updates without
opening one Redis Pub/Sub connection per client.
"""
import uuid
import json
from queue import Empty

from flask import Blueprint, Response, stream_with_context, jsonify, current_app
from config import Config
from middleware.rbac import require_login
from services.sse_broadcaster import get_broadcaster

sse_bp = Blueprint('sse_bp', __name__, url_prefix='/api/events')

@sse_bp.before_request
@require_login
def _sse_auth_guard():
    return None

@sse_bp.route('/stream')
def event_stream():
    broadcaster = get_broadcaster()

    if not Config.REDIS_SSE_ENABLED:
        return jsonify({"error": "SSE disabled; polling fallback active"}), 503
    if not broadcaster:
        return jsonify({"error": "SSE Pub/Sub Offline"}), 503

    client_id = str(uuid.uuid4())
    client_id, subscriber_queue = broadcaster.subscribe(client_id=client_id)

    def generate():
        try:
            yield f"event: connected\ndata: {json.dumps({'client_id': client_id[:8], 'status': 'connected'})}\n\n"

            while True:
                try:
                    event = subscriber_queue.get(timeout=15.0)
                    event_type = event.get('event_type', 'message')
                    event_id = event.get('event_id', str(uuid.uuid4()))
                    yield f"id: {event_id}\nevent: {event_type}\ndata: {json.dumps(event)}\n\n"
                except Empty:
                    yield "event: heartbeat\ndata: {}\n\n"
        except GeneratorExit:
            pass
        except Exception:
            current_app.logger.exception("[SSE] Stream generator failed for client=%s", client_id[:8])
        finally:
            broadcaster.unsubscribe(client_id)
            try:
                from extensions import db
                db.session.remove()
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
        }
    )

@sse_bp.route('/status')
def get_status():
    if not Config.REDIS_SSE_ENABLED:
        return jsonify({'status': 'disabled', 'error': 'Redis SSE disabled; polling fallback active'}), 503
    broadcaster = get_broadcaster()
    if not broadcaster:
        return jsonify({'status': 'offline', 'error': 'Redis unavailable'}), 503
    return jsonify({'status': 'active', **broadcaster.get_stats()})
