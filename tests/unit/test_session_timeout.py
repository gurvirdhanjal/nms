"""Tests for middleware/session_middleware.py session timeout behaviour."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


def _make_app(timeout_minutes=30):
    """Create a minimal Flask app with session config."""
    from flask import Flask
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['SESSION_TIMEOUT_MINUTES'] = timeout_minutes
    return app


def test_configurable_timeout_respected():
    """Session should expire after configured minutes."""
    from middleware.session_middleware import check_session_timeout

    app = _make_app(timeout_minutes=10)
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        # Last activity 11 minutes ago → should expire
        session['last_activity'] = (datetime.utcnow() - timedelta(minutes=11)).isoformat()
        assert check_session_timeout() is False


def test_default_30_minute_timeout():
    """Default timeout is 30 minutes when not configured."""
    from middleware.session_middleware import check_session_timeout

    app = _make_app(timeout_minutes=30)
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        # Last activity 25 minutes ago → should still be valid
        session['last_activity'] = (datetime.utcnow() - timedelta(minutes=25)).isoformat()
        assert check_session_timeout() is True

        # Last activity 31 minutes ago → should expire
        session['last_activity'] = (datetime.utcnow() - timedelta(minutes=31)).isoformat()
        assert check_session_timeout() is False


def test_floor_guard_prevents_zero_timeout():
    """Setting timeout to 0 should be clamped to minimum 1 minute."""
    from middleware.session_middleware import check_session_timeout

    app = _make_app(timeout_minutes=0)
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        # Last activity 30 seconds ago → should be valid (floor = 1 min)
        session['last_activity'] = (datetime.utcnow() - timedelta(seconds=30)).isoformat()
        assert check_session_timeout() is True

        # Last activity 2 minutes ago → should expire (floor = 1 min)
        session['last_activity'] = (datetime.utcnow() - timedelta(minutes=2)).isoformat()
        assert check_session_timeout() is False


def test_timeout_logs_expiry(caplog):
    """Session expiry should produce a log message."""
    import logging
    from middleware.session_middleware import check_session_timeout

    app = _make_app(timeout_minutes=1)
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        session['last_activity'] = (datetime.utcnow() - timedelta(minutes=5)).isoformat()

        with caplog.at_level(logging.INFO, logger='middleware.session_middleware'):
            result = check_session_timeout()

        assert result is False
        assert '[SESSION] Timeout' in caplog.text
