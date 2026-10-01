from functools import wraps
from flask import request, jsonify, session, redirect, url_for, flash
from database.db import query

ROLE_HOME = {
    'ADMIN': 'admin_module',
    'HOSPITAL_OPERATOR': 'hospital_module',
}


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    rows = query(
        'SELECT u.*, o.name AS organization_name FROM users u LEFT JOIN organizations o ON o.id=u.organization_id WHERE u.id=%s AND u.active=TRUE',
        (uid,), True
    )
    return rows[0] if rows else None


def require_role(*roles):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({'error': 'Authentication required'}), 401
                return redirect(url_for('login', next=request.path))
            if roles and user['role'] not in roles:
                if request.path.startswith('/api/') or request.is_json:
                    return jsonify({'error': 'Insufficient role'}), 403
                flash('You are not authorized to access that module.', 'error')
                return redirect(url_for(ROLE_HOME[user['role']]))
            request.operator = user
            return function(*args, **kwargs)
        return wrapper
    return decorator
