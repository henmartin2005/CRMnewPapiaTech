from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash as _gen_hash

def generate_password_hash(p):
    return _gen_hash(p, method='pbkdf2:sha256')
from database import get_db
from functools import wraps

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALL_MODULES = [
    ('whatsapp',  'WhatsApp',   'bi-whatsapp'),
    ('messenger', 'Messenger',  'bi-messenger'),
    ('instagram', 'Instagram',  'bi-instagram'),
    ('emails',    'Emails',     'bi-envelope'),
    ('calendar',  'Calendario', 'bi-calendar3'),
    ('proposals', 'Propuestas', 'bi-file-earmark-text'),
    ('tasks',     'Tasks',      'bi-check2-square'),
    ('chat',      'Chat interno', 'bi-chat-square-text'),
]


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get('user_role')
        if role not in ('admin', 'superadmin'):
            flash('Acceso restringido a administradores.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/gmail', methods=['POST'])
@admin_required
def save_gmail():
    org_id        = session.get('org_id', 1)
    client_id     = request.form.get('gmail_client_id', '').strip()
    client_secret = request.form.get('gmail_client_secret', '').strip()

    db = get_db()
    if client_id:
        db.execute("UPDATE organizations SET gmail_client_id=? WHERE id=?", (client_id, org_id))
    if client_secret:
        db.execute("UPDATE organizations SET gmail_client_secret=? WHERE id=?", (client_secret, org_id))
    db.commit()
    db.close()
    flash('Credenciales de Gmail guardadas correctamente.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/stripe', methods=['POST'])
@admin_required
def save_stripe():
    org_id         = session.get('org_id', 1)
    secret_key     = request.form.get('stripe_secret_key', '').strip()
    webhook_secret = request.form.get('stripe_webhook_secret', '').strip()
    base_url       = request.form.get('app_base_url', '').strip().rstrip('/')

    db = get_db()
    db.execute("""
        UPDATE organizations
        SET stripe_secret_key=?, stripe_webhook_secret=?, app_base_url=?
        WHERE id=?
    """, (secret_key, webhook_secret, base_url, org_id))
    db.commit()
    db.close()
    flash('Configuración de Stripe guardada.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/settings')
@admin_required
def settings():
    org_id = session.get('org_id', 1)
    db     = get_db()

    org = db.execute(
        "SELECT stripe_secret_key, stripe_webhook_secret, app_base_url, gmail_client_id, gmail_client_secret FROM organizations WHERE id=?",
        (org_id,)
    ).fetchone()

    meta_connections = db.execute(
        "SELECT * FROM meta_channel_connections WHERE org_id=? ORDER BY channel, page_name",
        (org_id,),
    ).fetchall()

    users  = db.execute(
        "SELECT id, username, display_name, role, is_active FROM users WHERE org_id=? ORDER BY role DESC, username",
        (org_id,)
    ).fetchall()

    modules_by_user = {}
    for u in users:
        rows = db.execute(
            "SELECT module, enabled FROM user_modules WHERE user_id=?", (u['id'],)
        ).fetchall()
        modules_by_user[u['id']] = {r['module']: r['enabled'] for r in rows}

    chat_permissions = {}
    rows = db.execute(
        "SELECT user_id, can_write_general FROM internal_chat_permissions WHERE org_id=?",
        (org_id,),
    ).fetchall()
    chat_permissions = {r['user_id']: r['can_write_general'] for r in rows}

    # Modules enabled at org level (set by PapiaTech superadmin)
    org_mod_rows    = db.execute(
        "SELECT module FROM org_modules WHERE org_id=? AND enabled=1", (org_id,)
    ).fetchall()
    org_enabled_set = {r['module'] for r in org_mod_rows}
    # Tuples for template rendering — only org-enabled modules
    org_modules_display = [(k, l, i) for k, l, i in ALL_MODULES if k in org_enabled_set]

    db.close()

    # Mask secret keys for display
    def mask(val):
        if not val: return ''
        return val[:8] + '••••••••' + val[-4:] if len(val) > 12 else '••••••••'

    from flask import request as _req
    gmail_redirect_uri = _req.url_root.rstrip('/') + '/emails/oauth2callback'

    return render_template('admin/settings.html',
        users=users,
        modules_by_user=modules_by_user,
        all_modules=org_modules_display,
        org_modules=org_modules_display,
        stripe_secret_key_masked=mask(org['stripe_secret_key'] if org else ''),
        stripe_webhook_secret_masked=mask(org['stripe_webhook_secret'] if org else ''),
        app_base_url=org['app_base_url'] if org else '',
        stripe_configured=bool(org and org['stripe_secret_key']),
        gmail_client_id_masked=mask(org['gmail_client_id'] if org else ''),
        gmail_client_secret_masked=mask(org['gmail_client_secret'] if org else ''),
        gmail_configured=bool(org and org['gmail_client_id']),
        gmail_redirect_uri=gmail_redirect_uri,
        meta_connections=meta_connections,
        chat_permissions=chat_permissions,
    )


@admin_bp.route('/meta-connections', methods=['POST'])
@admin_required
def save_meta_connection():
    org_id            = session.get('org_id', 1)
    channel           = request.form.get('channel', '').strip()
    page_id           = request.form.get('page_id', '').strip()
    page_name         = request.form.get('page_name', '').strip()
    page_access_token = request.form.get('page_access_token', '').strip()
    is_active         = 1 if request.form.get('is_active') else 0

    if channel not in ('messenger', 'instagram') or not page_id or not page_access_token:
        flash('Canal, Page/IG ID y Access Token son requeridos.', 'danger')
        return redirect(url_for('admin.settings'))

    db = get_db()
    db.execute("""
        INSERT INTO meta_channel_connections
            (org_id, channel, page_id, page_name, page_access_token, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(org_id, channel, page_id) DO UPDATE SET
            page_name=excluded.page_name,
            page_access_token=excluded.page_access_token,
            is_active=excluded.is_active
    """, (org_id, channel, page_id, page_name, page_access_token, is_active))
    db.commit()
    db.close()
    flash('Canal de Meta guardado correctamente.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/meta-connections/<int:connection_id>/toggle', methods=['POST'])
@admin_required
def toggle_meta_connection(connection_id):
    org_id = session.get('org_id', 1)
    db = get_db()
    db.execute(
        "UPDATE meta_channel_connections SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=? AND org_id=?",
        (connection_id, org_id),
    )
    db.commit()
    db.close()
    flash('Estado del canal actualizado.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def create_user():
    org_id       = session.get('org_id', 1)
    username     = request.form.get('username', '').strip()
    password     = request.form.get('password', '').strip()
    display_name = request.form.get('display_name', '').strip()

    if not username or not password:
        flash('Usuario y contraseña son requeridos.', 'danger')
        return redirect(url_for('admin.settings'))

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        db.close()
        flash(f'El usuario "{username}" ya existe.', 'danger')
        return redirect(url_for('admin.settings'))

    db.execute(
        "INSERT INTO users (username, password_hash, display_name, role, org_id) VALUES (?, ?, ?, 'user', ?)",
        (username, generate_password_hash(password), display_name or username, org_id),
    )
    db.commit()
    new_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
    db.executemany(
        "INSERT OR IGNORE INTO user_modules (user_id, module, enabled) VALUES (?, ?, 0)",
        [(new_id, m[0]) for m in ALL_MODULES],
    )
    db.commit()
    db.close()
    flash(f'Usuario "{username}" creado.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/users/<int:user_id>/toggle-module', methods=['POST'])
@admin_required
def toggle_module(user_id):
    module  = request.json.get('module')
    enabled = 1 if request.json.get('enabled') else 0

    db = get_db()
    db.execute(
        "INSERT INTO user_modules (user_id, module, enabled) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, module) DO UPDATE SET enabled=excluded.enabled",
        (user_id, module, enabled),
    )
    db.commit()
    db.close()
    return jsonify({'success': True})


@admin_bp.route('/users/<int:user_id>/toggle-chat-write', methods=['POST'])
@admin_required
def toggle_chat_write(user_id):
    org_id = session.get('org_id', 1)
    enabled = 1 if (request.get_json(silent=True) or {}).get('enabled') else 0

    db = get_db()
    user = db.execute(
        "SELECT id, role FROM users WHERE id=? AND org_id=?",
        (user_id, org_id),
    ).fetchone()
    if not user or user['role'] in ('admin', 'superadmin'):
        db.close()
        return jsonify({'success': False, 'error': 'Usuario inválido'}), 400

    db.execute("""
        INSERT INTO internal_chat_permissions (user_id, org_id, can_write_general, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, org_id) DO UPDATE SET
            can_write_general=excluded.can_write_general,
            updated_at=CURRENT_TIMESTAMP
    """, (user_id, org_id, enabled))
    db.commit()
    db.close()
    return jsonify({'success': True})


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    org_id = session.get('org_id', 1)
    db   = get_db()
    user = db.execute("SELECT username, role FROM users WHERE id=? AND org_id=?", (user_id, org_id)).fetchone()
    if not user or user['role'] in ('admin', 'superadmin'):
        db.close()
        flash('No se puede eliminar ese usuario.', 'danger')
        return redirect(url_for('admin.settings'))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    db.close()
    flash(f'Usuario "{user["username"]}" eliminado.', 'success')
    return redirect(url_for('admin.settings'))


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_password(user_id):
    new_pass = request.form.get('new_password', '').strip()
    if not new_pass:
        flash('La contraseña no puede estar vacía.', 'danger')
        return redirect(url_for('admin.settings'))
    db = get_db()
    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_pass), user_id),
    )
    db.commit()
    db.close()
    flash('Contraseña actualizada.', 'success')
    return redirect(url_for('admin.settings'))
