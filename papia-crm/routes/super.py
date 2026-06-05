from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from database import get_db
from werkzeug.security import generate_password_hash as _gen_hash
from functools import wraps

super_bp = Blueprint('super', __name__, url_prefix='/super')

ALL_MODULES = [
    ('whatsapp',  'WhatsApp',   'bi-whatsapp'),
    ('emails',    'Emails',     'bi-envelope'),
    ('calendar',  'Calendario', 'bi-calendar3'),
    ('proposals', 'Propuestas', 'bi-file-earmark-text'),
    ('tasks',     'Tasks',      'bi-check2-square'),
]
ALL_MODULE_KEYS = [m[0] for m in ALL_MODULES]


def generate_password_hash(p):
    return _gen_hash(p, method='pbkdf2:sha256')


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user_role') != 'superadmin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


@super_bp.route('/organizations')
@superadmin_required
def organizations():
    db   = get_db()
    orgs = db.execute("""
        SELECT o.*, COUNT(u.id) as user_count
        FROM organizations o
        LEFT JOIN users u ON u.org_id = o.id
        GROUP BY o.id ORDER BY o.created_at DESC
    """).fetchall()

    # Load module status per org
    org_modules = {}
    for row in db.execute("SELECT org_id, module, enabled FROM org_modules").fetchall():
        org_modules.setdefault(row['org_id'], {})[row['module']] = row['enabled']

    # Load users per org — include is_active and created_at for full management
    org_users = {}
    for row in db.execute("""
        SELECT id, username, display_name, role, is_active, created_at, org_id
        FROM users ORDER BY role DESC, username
    """).fetchall():
        org_users.setdefault(row['org_id'], []).append(dict(row))

    db.close()
    return render_template('super/organizations.html',
        orgs=orgs,
        org_modules=org_modules,
        org_users=org_users,
        all_modules=ALL_MODULES,
    )


@super_bp.route('/organizations/<int:org_id>/add-user', methods=['POST'])
@superadmin_required
def add_user_to_org(org_id):
    username     = request.form.get('username', '').strip()
    password     = request.form.get('password', '').strip()
    display_name = request.form.get('display_name', '').strip() or username
    role         = request.form.get('role', 'user')

    if role not in ('admin', 'user'):
        role = 'user'

    if not username or not password:
        flash('Usuario y contraseña son requeridos.', 'danger')
        return redirect(url_for('super.organizations'))

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        db.close()
        flash(f'El usuario "{username}" ya existe. Elige otro nombre.', 'danger')
        return redirect(url_for('super.organizations'))

    db.execute(
        "INSERT INTO users (username, password_hash, display_name, role, org_id) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), display_name, role, org_id)
    )
    db.commit()
    new_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']
    db.executemany(
        "INSERT OR IGNORE INTO user_modules (user_id, module, enabled) VALUES (?, ?, 1)",
        [(new_id, m) for m in ALL_MODULE_KEYS]
    )
    db.commit()
    db.close()
    flash(f'Usuario "{username}" agregado a la organización.', 'success')
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/delete-user/<int:user_id>', methods=['POST'])
@superadmin_required
def delete_org_user(org_id, user_id):
    if user_id == session.get('user_id'):
        flash('No puedes eliminar tu propia cuenta.', 'danger')
        return redirect(url_for('super.organizations'))
    db   = get_db()
    user = db.execute("SELECT username, role FROM users WHERE id=? AND org_id=?", (user_id, org_id)).fetchone()
    if user and user['role'] != 'superadmin':
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        db.commit()
        flash(f'Usuario @{user["username"]} eliminado.', 'success')
    db.close()
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/users/<int:user_id>/reset-password', methods=['POST'])
@superadmin_required
def reset_user_password(org_id, user_id):
    new_password = request.form.get('new_password', '').strip()
    if not new_password or len(new_password) < 4:
        flash('La contraseña debe tener al menos 4 caracteres.', 'danger')
        return redirect(url_for('super.organizations'))

    db   = get_db()
    user = db.execute(
        "SELECT username FROM users WHERE id=? AND org_id=?", (user_id, org_id)
    ).fetchone()
    if not user:
        db.close()
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('super.organizations'))

    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), user_id),
    )
    db.commit()
    db.close()
    flash(f'Contraseña de @{user["username"]} actualizada.', 'success')
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/users/<int:user_id>/toggle-active', methods=['POST'])
@superadmin_required
def toggle_user_active(org_id, user_id):
    if user_id == session.get('user_id'):
        flash('No puedes desactivar tu propia cuenta.', 'danger')
        return redirect(url_for('super.organizations'))

    db   = get_db()
    user = db.execute(
        "SELECT username, role, is_active FROM users WHERE id=? AND org_id=?", (user_id, org_id)
    ).fetchone()
    if not user or user['role'] == 'superadmin':
        db.close()
        flash('No se puede modificar ese usuario.', 'danger')
        return redirect(url_for('super.organizations'))

    new_status = 0 if user['is_active'] else 1
    db.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, user_id))
    db.commit()
    db.close()
    action = 'activado' if new_status else 'desactivado'
    flash(f'Usuario @{user["username"]} {action}.', 'success')
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/users/<int:user_id>/change-role', methods=['POST'])
@superadmin_required
def change_user_role(org_id, user_id):
    new_role = request.form.get('role', '').strip()
    if new_role not in ('admin', 'user'):
        flash('Rol inválido.', 'danger')
        return redirect(url_for('super.organizations'))

    if user_id == session.get('user_id'):
        flash('No puedes cambiar tu propio rol.', 'danger')
        return redirect(url_for('super.organizations'))

    db   = get_db()
    user = db.execute(
        "SELECT username, role FROM users WHERE id=? AND org_id=?", (user_id, org_id)
    ).fetchone()
    if not user or user['role'] == 'superadmin':
        db.close()
        flash('No se puede modificar ese usuario.', 'danger')
        return redirect(url_for('super.organizations'))

    db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    db.commit()
    db.close()
    flash(f'Rol de @{user["username"]} cambiado a {new_role}.', 'success')
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/create', methods=['POST'])
@superadmin_required
def create_org():
    name         = request.form.get('name', '').strip()
    admin_user   = request.form.get('admin_username', '').strip()
    admin_pass   = request.form.get('admin_password', '').strip()
    slug         = name.lower().replace(' ', '-')

    if not name or not admin_user or not admin_pass:
        flash('Todos los campos son requeridos.', 'danger')
        return redirect(url_for('super.organizations'))

    db = get_db()

    # Check username uniqueness globally
    if db.execute("SELECT 1 FROM users WHERE username=?", (admin_user,)).fetchone():
        db.close()
        flash(f'El usuario "{admin_user}" ya existe. Elige otro nombre de usuario.', 'danger')
        return redirect(url_for('super.organizations'))

    # Check slug uniqueness
    if db.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone():
        slug = slug + '-' + str(db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0])

    # Create org
    db.execute("INSERT INTO organizations (name, slug) VALUES (?, ?)", (name, slug))
    db.commit()
    org_id = db.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()['id']

    # Create admin user for org
    db.execute(
        "INSERT INTO users (username, password_hash, display_name, role, org_id) VALUES (?, ?, ?, 'admin', ?)",
        (admin_user, generate_password_hash(admin_pass), admin_user, org_id)
    )
    db.commit()
    admin_id = db.execute("SELECT id FROM users WHERE username=? AND org_id=?", (admin_user, org_id)).fetchone()['id']

    # Enable all modules for the org (superadmin can toggle later)
    db.executemany(
        "INSERT OR IGNORE INTO org_modules (org_id, module, enabled) VALUES (?, ?, 1)",
        [(org_id, m) for m in ALL_MODULE_KEYS]
    )
    # Enable all modules for admin user
    db.executemany(
        "INSERT OR IGNORE INTO user_modules (user_id, module, enabled) VALUES (?, ?, 1)",
        [(admin_id, m) for m in ALL_MODULE_KEYS]
    )
    db.commit()
    db.close()
    flash(f'Organización "{name}" creada con admin "{admin_user}".', 'success')
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/view')
@superadmin_required
def view_as_org(org_id):
    session['viewed_org_id'] = org_id
    flash(f'Viendo como org #{org_id}', 'info')
    return redirect(url_for('dashboard'))


@super_bp.route('/organizations/exit-view')
@superadmin_required
def exit_view():
    session.pop('viewed_org_id', None)
    return redirect(url_for('super.organizations'))


@super_bp.route('/organizations/<int:org_id>/toggle', methods=['POST'])
@superadmin_required
def toggle_org(org_id):
    db = get_db()
    db.execute("UPDATE organizations SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (org_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})


@super_bp.route('/organizations/<int:org_id>/toggle-module', methods=['POST'])
@superadmin_required
def toggle_org_module(org_id):
    data    = request.get_json(silent=True) or {}
    module  = data.get('module', '')
    enabled = 1 if data.get('enabled') else 0

    if module not in ALL_MODULE_KEYS:
        return jsonify({'success': False, 'error': 'Módulo inválido'}), 400

    db = get_db()
    db.execute(
        "INSERT INTO org_modules (org_id, module, enabled) VALUES (?, ?, ?) "
        "ON CONFLICT(org_id, module) DO UPDATE SET enabled=excluded.enabled",
        (org_id, module, enabled),
    )
    db.commit()
    db.close()
    return jsonify({'success': True})
