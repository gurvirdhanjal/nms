import redis

import extensions


def test_is_redis_available_returns_false_on_timeout(monkeypatch):
    class TimeoutRedis:
        def ping(self):
            raise redis.exceptions.TimeoutError("timed out")

    monkeypatch.setattr(extensions, "redis_client", TimeoutRedis())

    assert extensions.is_redis_available() is False


def test_is_redis_available_returns_true_when_ping_succeeds(monkeypatch):
    class HealthyRedis:
        def ping(self):
            return True

    monkeypatch.setattr(extensions, "redis_client", HealthyRedis())

    assert extensions.is_redis_available() is True
