"""
Redis-backed SSE broadcaster with process-local fanout.

The previous route implementation opened a dedicated Redis Pub/Sub connection for
every connected browser tab. That scales poorly on small Redis Cloud plans.
This module keeps a single Redis subscriber per Python process and fans events
out to local in-memory queues for active SSE clients.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

from config import Config
from extensions import redis_client

SSE_CHANNEL = "sse_events"
_SUBSCRIBER_QUEUE_SIZE = 128
_LISTENER_RETRY_SECONDS = 1.0

logger = logging.getLogger(__name__)


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_event(event_type: str, payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        event = dict(payload)
    else:
        event = {"payload": payload}

    event.setdefault("event_type", event_type)
    event.setdefault("event_id", str(uuid.uuid4()))
    event.setdefault("published_at", _utc_iso_now())
    return event


class RedisSSEHub:
    def __init__(self, channel: str = SSE_CHANNEL):
        self.channel = channel
        self._lock = threading.RLock()
        self._subscribers: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listener_online = False

    def subscribe(self, client_id: str | None = None) -> tuple[str, queue.Queue[dict[str, Any]]]:
        self.ensure_running()

        subscription_id = client_id or str(uuid.uuid4())
        subscriber_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers[subscription_id] = subscriber_queue
        return subscription_id, subscriber_queue

    def unsubscribe(self, client_id: str) -> None:
        with self._lock:
            self._subscribers.pop(client_id, None)

    def ensure_running(self) -> bool:
        if not Config.REDIS_SSE_ENABLED or redis_client is None:
            return False

        with self._lock:
            if self._listener_thread and self._listener_thread.is_alive():
                return True

            self._stop_event.clear()
            self._listener_thread = threading.Thread(
                target=self._listen_loop,
                name="redis-sse-listener",
                daemon=True,
            )
            self._listener_thread.start()
            return True

    def publish(self, event_type: str, payload: Any) -> bool:
        if not Config.REDIS_SSE_ENABLED or redis_client is None:
            return False

        event = _normalize_event(event_type, payload)
        try:
            redis_client.publish(self.channel, json.dumps(event, default=str))
            return True
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            redis.exceptions.RedisError,
            OSError,
        ) as exc:
            logger.warning("[SSE] Redis publish failed for event_type=%s: %s", event_type, exc)
            return False

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            subscriber_count = len(self._subscribers)
        return {
            "channel": self.channel,
            "subscribers": subscriber_count,
            "listener_online": self._listener_online,
            "redis_enabled": bool(Config.REDIS_SSE_ENABLED and redis_client is not None),
        }

    def _listen_loop(self) -> None:
        while not self._stop_event.is_set():
            if redis_client is None or not Config.REDIS_SSE_ENABLED:
                self._listener_online = False
                return

            pubsub = None
            try:
                pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(self.channel)
                self._listener_online = True
                logger.info("[SSE] Redis listener subscribed to channel=%s", self.channel)

                while not self._stop_event.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if not message:
                        continue

                    event = self._coerce_message(message.get("data"))
                    if event is None:
                        continue
                    self._fan_out(event)

            except (
                redis.exceptions.ConnectionError,
                redis.exceptions.TimeoutError,
                redis.exceptions.RedisError,
                OSError,
            ) as exc:
                self._listener_online = False
                logger.warning("[SSE] Redis listener error; retrying in %.1fs: %s", _LISTENER_RETRY_SECONDS, exc)
                time.sleep(_LISTENER_RETRY_SECONDS)
            except Exception:
                self._listener_online = False
                logger.exception("[SSE] Unexpected listener error; retrying in %.1fs", _LISTENER_RETRY_SECONDS)
                time.sleep(_LISTENER_RETRY_SECONDS)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        logger.debug("[SSE] Ignoring pubsub close failure", exc_info=True)

    def _coerce_message(self, raw_data: Any) -> dict[str, Any] | None:
        if raw_data is None:
            return None

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8", errors="replace")

        try:
            if isinstance(raw_data, str):
                payload = json.loads(raw_data)
            elif isinstance(raw_data, dict):
                payload = raw_data
            else:
                payload = {"payload": raw_data}
        except Exception:
            logger.debug("[SSE] Ignoring unparseable pubsub payload", exc_info=True)
            return None

        event_type = str(payload.get("event_type") or "message")
        return _normalize_event(event_type, payload)

    def _fan_out(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())

        for client_id, subscriber_queue in subscribers:
            try:
                subscriber_queue.put_nowait(event)
            except queue.Full:
                try:
                    subscriber_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber_queue.put_nowait(event)
                except queue.Full:
                    logger.debug("[SSE] Dropped event for saturated subscriber=%s", client_id)


_hub: RedisSSEHub | None = None
_hub_lock = threading.Lock()


def get_broadcaster() -> RedisSSEHub | None:
    if not Config.REDIS_SSE_ENABLED or redis_client is None:
        return None

    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = RedisSSEHub()
        _hub.ensure_running()
        return _hub


def broadcast_event(event_type: str, payload: Any) -> bool:
    broadcaster = get_broadcaster()
    if broadcaster is None:
        return False
    return broadcaster.publish(event_type, payload)
