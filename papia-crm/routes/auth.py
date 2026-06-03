import os
import functools
from flask import Blueprint, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash
from database import get_db

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.full_path))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        db   = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1", (username,)
        ).fetchone()
        db.close()

        if user and check_password_hash(user['password_hash'], password):
            session.permanent = True
            session['logged_in']  = True
            session['username']   = username
            session['user_id']    = user['id']
            session['user_role']  = user['role']
            session['org_id']     = user['org_id'] if 'org_id' in user.keys() else 1
            session.pop('viewed_org_id', None)
            next_url = request.args.get('next') or url_for('dashboard')
            return redirect(next_url)
        else:
            error = 'Usuario o contraseña incorrectos.'

    return render_template('auth/login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
