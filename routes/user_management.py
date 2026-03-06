from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
from extensions import db, bcrypt
from models.user import User
from middleware.rbac import require_role

user_management_bp = Blueprint('user_management_bp', __name__, url_prefix='')

@user_management_bp.route('/user_management')
@require_role('admin')
def user_management():
    users = User.query.all()
    user = None

    if 'edit_id' in request.args:
        user = User.query.get(request.args.get('edit_id'))

    if 'delete_id' in request.args:
        user = User.query.get(request.args.get('delete_id'))
        if user:
            # Prevent deleting own account
            if user.id == session.get('user_id'):
                flash('You cannot delete your own account.', 'danger')
            else:
                db.session.delete(user)
                db.session.commit()
                flash('User deleted successfully.', 'success')
        return redirect(url_for('user_management_bp.user_management'))

    from models.department import Department
    departments = Department.query.all()

    return render_template('user_management.html', users=users, user=user, departments=departments)

@user_management_bp.route('/user_management/save', methods=['POST'])
@require_role('admin')
def save_user():
    try:
        user_id = request.form.get('user_id')
        username = request.form['username']
        password = request.form.get('password', '')  # Make optional for updates
        role = request.form['role']
        email = request.form['email']
        phone_number = request.form.get('phone_number', '')
        department_id = request.form.get('department_id')
        if department_id:
            department_id = int(department_id)
        else:
            department_id = None

        # Check if username already exists (for new users or when changing username)
        existing_user = User.query.filter_by(username=username).first()
        if existing_user and (not user_id or existing_user.id != int(user_id)):
            flash('Username already exists!', 'danger')
            return redirect(url_for('user_management_bp.user_management'))

        # Check if email already exists (for new users or when changing email)
        existing_email = User.query.filter_by(email=email).first()
        if existing_email and (not user_id or existing_email.id != int(user_id)):
            flash('Email already exists!', 'danger')
            return redirect(url_for('user_management_bp.user_management'))

        if user_id:
            # Update existing user
            user = User.query.get(user_id)
            
            # Track changes for audit log
            changes = {}
            old_role = user.role
            
            if user.username != username:
                changes['username'] = {'before': user.username, 'after': username}
            if user.role != role:
                changes['role'] = {'before': user.role, 'after': role}
            if user.email != email:
                changes['email'] = {'before': user.email, 'after': email}
            if user.department_id != department_id:
                changes['department_id'] = {'before': user.department_id, 'after': department_id}
            if password:
                changes['password'] = {'before': '[REDACTED]', 'after': '[REDACTED]'}
            
            user.username = username
            # Only update password if provided
            if password:
                user.password = bcrypt.generate_password_hash(password).decode('utf-8')
            user.role = role
            user.department_id = department_id
            user.email = email
            user.phone_number = phone_number
            
            db.session.commit()
            
            # Audit logging for user update
            from middleware.rbac import create_audit_log
            create_audit_log(
                action='update',
                entity_type='user',
                entity_id=user.id,
                entity_name=username,
                description=f'Updated user {username}',
                changes=changes if changes else None
            )
            
            flash('User updated successfully.', 'success')
        else:
            # Create new user - password is required for new users
            if not password:
                flash('Password is required for new users.', 'danger')
                return redirect(url_for('user_management_bp.user_management'))
                
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(
                username=username, 
                password=hashed_password, 
                role=role, 
                department_id=department_id,
                email=email, 
                phone_number=phone_number
            )
            db.session.add(user)
            db.session.commit()
            
            # Audit logging for user creation
            from middleware.rbac import create_audit_log
            create_audit_log(
                action='create',
                entity_type='user',
                entity_id=user.id,
                entity_name=username,
                description=f'Created user {username} with role {role}'
            )
            
            flash('User created successfully.', 'success')

        return redirect(url_for('user_management_bp.user_management'))
    
    except Exception as e:
        flash(f'Error saving user: {str(e)}', 'danger')
        return redirect(url_for('user_management_bp.user_management'))

@user_management_bp.route('/api/users/<int:user_id>/toggle_status', methods=['POST'])
@require_role('admin')
def toggle_user_status(user_id):
    user = User.query.get(user_id)
    if user:
        # Prevent deactivating own account
        if user.id == session.get('user_id'):
            return jsonify({'error': 'You cannot deactivate your own account'}), 400
        
        old_status = user.is_active
        user.is_active = not user.is_active
        db.session.commit()
        
        # Audit logging for user status change
        from middleware.rbac import create_audit_log
        action = 'deactivate' if not user.is_active else 'activate'
        create_audit_log(
            action=action,
            entity_type='user',
            entity_id=user.id,
            entity_name=user.username,
            description=f'{action.capitalize()}d user {user.username}',
            changes={
                'is_active': {
                    'before': old_status,
                    'after': user.is_active
                }
            }
        )
        
        return jsonify({'success': True, 'is_active': user.is_active})
    else:
        return jsonify({'error': 'User not found'}), 404
