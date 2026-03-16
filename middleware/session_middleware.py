# file name: middleware/session_middleware.py
import logging

from flask import session, redirect, url_for, request, jsonify, flash, current_app
from datetime import datetime, timedelta
from functools import wraps
import json

logger = logging.getLogger(__name__)


def check_session_timeout():
    """Check if session has timed out.

    Reads SESSION_TIMEOUT_MINUTES from Flask app config (default 30).
    Enforces a minimum of 1 minute to prevent accidental lockout.
    """
    if 'logged_in' not in session:
        return False

    if 'last_activity' not in session:
        return False

    try:
        last_activity = datetime.fromisoformat(session['last_activity'])
        time_diff = datetime.utcnow() - last_activity

        timeout_min = int(current_app.config.get('SESSION_TIMEOUT_MINUTES', 30))
        timeout_min = max(timeout_min, 1)  # enforce minimum 1 minute
        if time_diff > timedelta(minutes=timeout_min):
            logger.info('[SESSION] Timeout: last_activity=%s, diff=%ss, threshold=%sm',
                        session['last_activity'], int(time_diff.total_seconds()), timeout_min)
            return False
    except (ValueError, KeyError):
        return False

    return True

def update_last_activity():
    """Update last activity time for current session"""
    if session.get('logged_in'):
        session['last_activity'] = datetime.utcnow().isoformat()

def setup_auth_middleware(bp):
    """Set up authentication middleware for a blueprint"""
    @bp.before_request
    def require_login():
        # Get the endpoint (route function name)
        endpoint = request.endpoint

        # Skip auth check for these specific endpoints
        exempt_endpoints = [
            'static',  # Static files
            'auth_bp.login',
            'auth_bp.logout',
            'auth_bp.register',
            'auth_bp.forgot_password',
            'auth_bp.validate_otp',
            'auth_bp.reset_password',
            'auth_bp.session_status',  # IMPORTANT: Don't check session-status
            'agent_bp.receive_metrics', # Agent handles its own auth
        ]

        # If this is an exempt endpoint, skip auth check
        if endpoint in exempt_endpoints:
            return None

        # Allow API key auth for /api/* endpoints
        if request.path.startswith('/api/'):
            provided_key = request.headers.get('X-API-Key')
            bound_key_id = request.headers.get('X-Agent-Key-Id')
            bound_key = request.headers.get('X-Agent-Key')
            expected_mobile_key = current_app.config.get('MOBILE_API_KEY')
            expected_tracking_key = current_app.config.get('API_KEY') # TRACKING_API_KEY in config

            # Restricted tracking endpoints can authenticate via bound agent keys.
            if request.path.startswith('/api/tracking/') and bound_key_id and bound_key:
                return None

            if provided_key:
                if (expected_mobile_key and provided_key == expected_mobile_key) or \
                   (expected_tracking_key and provided_key == expected_tracking_key):
                    return None
                return jsonify({'success': False, 'error': 'Invalid API key'}), 401

        # If user is not logged in at all
        if not session.get('logged_in'):
            logger.info('[SESSION] Not authenticated: endpoint=%s path=%s', endpoint, request.path)
            # If this is an API endpoint, return JSON error
            if 'api' in request.path or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Not authenticated'}), 401
            # Otherwise redirect to login
            return redirect(url_for('auth_bp.login'))

        # If logged in but session expired
        if not check_session_timeout():
            # Clear session
            session.clear()

            # For API endpoints, return JSON
            if 'api' in request.path or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired'}), 401

            # For regular pages, redirect to login
            flash('Your session has expired. Please login again.', 'warning')
            return redirect(url_for('auth_bp.login'))

        # Session is valid, update last activity
        update_last_activity()

    return bp
