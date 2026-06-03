from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from database import get_db
from werkzeug.security import generate_password_hash as _gen_hash
from functools import wraps

super_bp = Blueprint('super', __name__, url_prefix='/super')

ALL_MODULES = ['whatsapp', 'emails', 'calendar', 'proposals', 'tasks']


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
    db = get_db()
    orgs = db.execute("""
        SELECT o.*, COUNT(u.id) as user_count
        FROM organizations o
        LEFT JOIN users u ON u.org_id = o.id
        GROUP BY o.id ORDER BY o.created_at DESC
    """).fetchall()
    db.close()
    return render_template('super/organizations.html', orgs=orgs)


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

    # Enable all modules for admin
    db.executemany(
        "INSERT OR IGNORE INTO user_modules (user_id, module, enabled) VALUES (?, ?, 1)",
        [(admin_id, m) for m in ALL_MODULES]
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
