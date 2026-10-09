import os
import json
import secrets
import functools
import urllib.parse
import urllib.request
from flask import Blueprint, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash
from database import get_db

# ── Login con Google ─────────────────────────────────────────────────────────
# Correo de Google vinculado al perfil admin principal (mismo usuario, mismo rol)
GOOGLE_ADMIN_EMAIL = os.getenv('GOOGLE_ADMIN_EMAIL', 'henrry@papiatech.com').strip().lower()
GOOGLE_AUTH_URL     = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL    = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'

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
            _start_session(user)
            next_url = request.args.get('next') or url_for('dashboard')
            return redirect(next_url)
        else:
            error = 'Usuario o contraseña incorrectos.'

    if not error and request.args.get('gerr'):
        error = request.args.get('gerr')
    return render_template('auth/login.html', error=error)


def _start_session(user):
    """Misma sesión para login con contraseña y con Google."""
    session.permanent = True
    session['logged_in']  = True
    session['username']   = user['username']
    session['user_id']    = user['id']
    session['user_role']  = user['role']
    session['org_id']     = user['org_id'] if 'org_id' in user.keys() else 1
    session.pop('viewed_org_id', None)


def _google_creds():
    client_id = client_secret = ''
    try:
        db = get_db()
        org = db.execute(
            "SELECT gmail_client_id, gmail_client_secret FROM organizations WHERE id = 1"
        ).fetchone()
        db.close()
        if org:
            client_id = org['gmail_client_id'] or ''
            client_secret = org['gmail_client_secret'] or ''
    except Exception:
        pass
    client_id = client_id or os.getenv('GOOGLE_LOGIN_CLIENT_ID') or os.getenv('GMAIL_CLIENT_ID', '')
    client_secret = client_secret or os.getenv('GOOGLE_LOGIN_CLIENT_SECRET') or os.getenv('GMAIL_CLIENT_SECRET', '')
    return client_id, client_secret


def _google_redirect_uri():
    return os.getenv('GOOGLE_LOGIN_REDIRECT_URI') or ('https://' + request.host + '/login/google/callback')


def _ensure_google_column(db):
    cols = [r['name'] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if 'google_email' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN google_email TEXT DEFAULT ''")
        db.commit()
    # Vincula el correo admin al superadmin principal (una sola vez)
    if GOOGLE_ADMIN_EMAIL:
        linked = db.execute(
            "SELECT id FROM users WHERE lower(google_email) = ?", (GOOGLE_ADMIN_EMAIL,)
        ).fetchone()
        if not linked:
            admin = db.execute(
                "SELECT id FROM users WHERE role = 'superadmin' AND is_active = 1 "
                "ORDER BY (org_id = 1) DESC, id ASC LIMIT 1"
            ).fetchone()
            if admin:
                db.execute("UPDATE users SET google_email = ? WHERE id = ?",
                           (GOOGLE_ADMIN_EMAIL, admin['id']))
                db.commit()


@auth_bp.route('/login/google')
def google_login():
    client_id, _ = _google_creds()
    if not client_id:
        return redirect(url_for('auth.login', gerr='Login con Google no está configurado.'))
    state = secrets.token_urlsafe(24)
    session['google_oauth_state'] = state
    session['google_oauth_next'] = request.args.get('next') or ''
    params = {
        'client_id': client_id,
        'redirect_uri': _google_redirect_uri(),
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'prompt': 'select_account',
    }
    return redirect(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params))


@auth_bp.route('/login/google/callback')
def google_callback():
    state = session.pop('google_oauth_state', None)
    next_url = session.pop('google_oauth_next', '') or url_for('dashboard')
    if not state or request.args.get('state') != state:
        return redirect(url_for('auth.login', gerr='Sesión de Google inválida, intenta de nuevo.'))
    code = request.args.get('code')
    if not code:
        return redirect(url_for('auth.login', gerr='Inicio de sesión con Google cancelado.'))

    client_id, client_secret = _google_creds()
    try:
        data = urllib.parse.urlencode({
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': _google_redirect_uri(),
            'grant_type': 'authorization_code',
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(GOOGLE_TOKEN_URL, data=data), timeout=15) as r:
            token = json.loads(r.read().decode())
        req = urllib.request.Request(GOOGLE_USERINFO_URL,
                                     headers={'Authorization': 'Bearer ' + token['access_token']})
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.loads(r.read().decode())
    except Exception:
        return redirect(url_for('auth.login', gerr='No se pudo verificar la cuenta de Google.'))

    email = (info.get('email') or '').strip().lower()
    if not email or not info.get('email_verified'):
        return redirect(url_for('auth.login', gerr='Correo de Google no verificado.'))

    db = get_db()
    _ensure_google_column(db)
    user = db.execute(
        "SELECT * FROM users WHERE lower(google_email) = ? AND is_active = 1", (email,)
    ).fetchone()
    db.close()
    if not user:
        return redirect(url_for('auth.login', gerr='Esta cuenta de Google no tiene acceso al CRM.'))

    _start_session(user)
    if not next_url.startswith('/'):
        next_url = url_for('dashboard')
    return redirect(next_url)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
