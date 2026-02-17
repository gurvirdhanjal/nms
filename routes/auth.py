# file name: routes/auth.py (updated)
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app
from extensions import db, bcrypt
from datetime import datetime, timedelta
import random
import logging
from services.email_service import send_otp_email_async
from middleware.session_middleware import update_last_activity,check_session_timeout
import time
from sqlalchemy.exc import OperationalError


log = logging.getLogger(__name__)

auth_bp = Blueprint('auth_bp', __name__, url_prefix='')

@auth_bp.route('/')
def index():
    return redirect(url_for('auth_bp.login'))

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # Clear any existing session on GET request
    if request.method == 'GET' and session.get('logged_in'):
        session.clear()
    
    ldap_enabled = current_app.config.get('LDAP_ENABLED', False)

    if request.method == 'POST':
        from models.user import User
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        authenticated_user = None

        # ── LDAP Authentication (primary when enabled) ──────────
        if ldap_enabled:
            try:
                from services.ldap_service import LDAPService, LDAPConnectionError
                try:
                    ldap_result = LDAPService.authenticate(username, password)
                    if ldap_result:
                        # Upsert user in local DB
                        authenticated_user = _upsert_ldap_user(username, ldap_result)
                except LDAPConnectionError as e:
                    log.warning(f"[AUTH] LDAP connection failed: {e}. Falling back to local auth.")
            except ImportError:
                log.warning("[AUTH] ldap3 not installed. Skipping LDAP auth.")

        # ── Local DB Authentication (fallback) ──────────────────
        if authenticated_user is None:
            user = User.query.filter_by(username=username).first()
            if user and user.password and bcrypt.check_password_hash(user.password, password):
                if not user.is_active:
                    return render_template('auth/login.html', error="Account is deactivated.", ldap_enabled=ldap_enabled)
                authenticated_user = user

        # ── Session creation ────────────────────────────────────
        if authenticated_user:
            session.clear()
            session['logged_in'] = True
            session['username'] = authenticated_user.username
            session['user_id'] = authenticated_user.id
            session['role'] = authenticated_user.role
            session['auth_source'] = authenticated_user.auth_source
            session['session_id'] = f"{authenticated_user.id}_{datetime.utcnow().timestamp()}"
            session['last_activity'] = datetime.utcnow().isoformat()
            session['login_time'] = datetime.utcnow().isoformat()
            session.permanent = False
            update_last_activity()
            return redirect(url_for('monitoring_bp.dashboard'))

        return render_template('auth/login.html', error="Invalid credentials!", ldap_enabled=ldap_enabled)
    else:
        message = request.args.get('message')
        return render_template('auth/login.html', message=message, ldap_enabled=ldap_enabled)


def _upsert_ldap_user(username, ldap_result):
    """Find or create a local User record for an LDAP-authenticated user."""
    from models.user import User

    user = User.query.filter_by(username=username).first()

    if user:
        # Update existing user with latest LDAP data
        user.auth_source = 'ldap'
        user.display_name = ldap_result.get('display_name') or user.display_name
        user.email = ldap_result.get('email') or user.email
        user.external_id = ldap_result.get('external_id') or user.external_id
        user.role = ldap_result.get('role', user.role)
        user.is_active = True
    else:
        # Create new user from LDAP data
        user = User(
            username=username,
            password=None,      # No local password for LDAP users
            auth_source='ldap',
            role=ldap_result.get('role', 'user'),
            email=ldap_result.get('email'),
            display_name=ldap_result.get('display_name'),
            external_id=ldap_result.get('external_id'),
            is_active=True,
        )
        db.session.add(user)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error(f"[AUTH] Failed to upsert LDAP user '{username}': {e}")

    return user

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth_bp.login', message="You have been logged out."))
# file name: routes/auth.py (updated session_status)
@auth_bp.route('/session-status', methods=['GET'])
def session_status():
    """API endpoint to check session status - NO AUTH CHECK HERE!"""
    # This endpoint should NOT check authentication itself
    
    # Check if user has a valid session
    is_valid = False
    remaining_time = 0
    
    if session.get('logged_in'):
        is_valid = check_session_timeout()
        
        if is_valid:
            # Calculate remaining time
            last_activity = datetime.fromisoformat(session.get('last_activity', datetime.utcnow().isoformat()))
            time_diff = datetime.utcnow() - last_activity
            remaining_time = max(0, 300 - time_diff.total_seconds())  # 5 minutes in seconds
    
    return jsonify({
        'logged_in': session.get('logged_in', False),
        'valid_session': is_valid,
        'username': session.get('username'),
        'role': session.get('role'),
        'last_activity': session.get('last_activity'),
        'remaining_time': remaining_time,
        'timestamp': datetime.utcnow().isoformat()
    })

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    from models.user import User
    # Only allow registration if no admin exists
    if User.query.filter_by(role="admin").count() > 0:
        return render_template('auth/register.html', error="Admin user already exists! Please contact administrator for new accounts.")
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = bcrypt.generate_password_hash(request.form.get('password')).decode('utf-8')
        role = request.form.get('role', 'user')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        
        if User.query.filter_by(username=username).first():
            return render_template('auth/register.html', error="Username already exists!")
        
        if User.query.filter_by(email=email).first():
            return render_template('auth/register.html', error="Email already exists!")
        
        user = User(
            username=username, 
            password=password, 
            role=role, 
            email=email, 
            phone_number=phone_number
        )
        db.session.add(user)
        db.session.commit()
        
        return redirect(url_for('auth_bp.login', message="Registration successful! Please login."))
    
    return render_template('auth/register.html')

@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    from models.user import User
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            otp = random.randint(100000, 999999)
            send_otp_email_async(user.email, otp)
            session['otp'] = otp
            session['user_id'] = user.id
            session['otp_sent'] = True 
            return redirect(url_for('auth_bp.validate_otp'))
        else:
            return render_template('auth/forgot_password.html', error="Email not found.")
    return render_template('auth/forgot_password.html')

@auth_bp.route('/validate_otp', methods=['GET', 'POST'])
def validate_otp():
    if not session.get('otp_sent'):
        return redirect(url_for('auth_bp.forgot_password'))
    
    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        if entered_otp and entered_otp.isdigit():
            if int(entered_otp) == session.get('otp'):
                session['otp_validated'] = True
                return redirect(url_for('auth_bp.reset_password'))
        
        return render_template('auth/validate_otp.html', error="Invalid OTP!")
    
    return render_template('auth/validate_otp.html')

@auth_bp.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    from models.user import User
    if not session.get('otp_validated'):
        return redirect(url_for('auth_bp.forgot_password'))
                        
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            return render_template('auth/reset_password.html', error="Passwords do not match!")
        
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        user = User.query.get(session.get('user_id'))
        user.password = hashed_password
        
        # Retry logic for DB lock
        max_retries = 3
        for attempt in range(max_retries):
            try:
                db.session.commit()
                break
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                else:
                    raise e
        
        # Clear OTP session
        session.pop('otp', None)
        session.pop('user_id', None)
        session.pop('otp_sent', None)
        session.pop('otp_validated', None)
        
        return redirect(url_for('auth_bp.login', message="Password reset successfully! Please login with your new password."))
    
    return render_template('auth/reset_password.html')