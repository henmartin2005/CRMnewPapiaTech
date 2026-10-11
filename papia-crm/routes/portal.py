"""Portal del cliente: el cliente entra con Google (su email) y ve el avance de su
proyecto (etapas de la Pizarra), el costo total / pagado / pendiente y su contrato."""
import json
import os
import secrets
import functools
import urllib.parse

from flask import (Blueprint, render_template, request, session, redirect, url_for,
                   send_file, abort, jsonify, g)

from database import get_db

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
PUBLIC_ENDPOINTS = {'portal.login', 'portal.google_start', 'portal.logout', 'portal.home',
                    'portal.open_doc'}


# ── Esquema ────────────────────────────────────────────────────────────────
def ensure_portal_schema():
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(client_documents)").fetchall()}
        if cols and 'portal_visible' not in cols:
            db.execute("ALTER TABLE client_documents ADD COLUMN portal_visible INTEGER NOT NULL DEFAULT 0")
            db.commit()
        cols = {r[1] for r in db.execute("PRAGMA table_info(contract_envelopes)").fetchall()}
        if cols and 'portal_visible' not in cols:
            db.execute("ALTER TABLE contract_envelopes ADD COLUMN portal_visible INTEGER NOT NULL DEFAULT 0")
            db.commit()
    finally:
        db.close()


@portal_bp.record_once
def _on_register(state):
    try:
        ensure_portal_schema()
    except Exception:
        pass


# ── Helpers ────────────────────────────────────────────────────────────────
def _admin_required(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.full_path))
        return f(*a, **kw)
    return wrapper


def _admin_org():
    return g.org_id if hasattr(g, 'org_id') else 1


def _clients_for_email(email):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id FROM clients WHERE lower(trim(email)) = ? ORDER BY id DESC",
            (email.strip().lower(),)).fetchall()
        return [r[0] for r in rows]
    finally:
        db.close()


def portal_finish_login(email):
    """Llamado desde auth.google_callback cuando el login es del portal."""
    ids = _clients_for_email(email)
    if not ids:
        return redirect(url_for('portal.login', err='no_client', email=email))
    session['portal_email'] = email
    session['portal_client_ids'] = ids
    session.permanent = True
    return redirect(url_for('portal.home'))


def _stage_state(s):
    st = (s.get('status') or '').lower()
    if s.get('done') or st == 'done':
        return 'done'
    if st in ('doing', 'progress', 'in_progress', 'wip', 'active'):
        return 'doing'
    return 'todo'


def _projects_for_client(db, client_id, org_id):
    from routes.boards import _ensure_table, _stages
    _ensure_table(db)
    rows = db.execute("SELECT id, name, data, updated_at FROM boards WHERE org_id = ? AND client_id = ? "
                      "ORDER BY position, id", (org_id, client_id)).fetchall()
    projects = []
    for r in rows:
        try:
            content = json.loads(r[2] or '{}')
        except ValueError:
            content = {}
        stages = _stages(content)
        for s in stages:
            s['state'] = _stage_state(s)
        if stages and not any(s['state'] == 'doing' for s in stages):
            nxt = next((s for s in stages if s['state'] != 'done'), None)
            if nxt:
                nxt['state'] = 'doing'
        done = sum(1 for s in stages if s['state'] == 'done')
        total = len(stages)
        projects.append({'id': r[0], 'name': r[1], 'stages': stages, 'done': done, 'total': total,
                         'pct': int(round(done * 100.0 / total)) if total else 0,
                         'updated_at': (r[3] or '')[:10]})
    return projects


def _client_bundle(client_id):
    db = get_db()
    try:
        c = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not c:
            return None
        c = dict(c)
        try:
            from models.client import PROJECT_TYPES
            c['project_type'] = dict(PROJECT_TYPES).get(c.get('project_type'), c.get('project_type'))
        except Exception:
            pass
        org_id = c.get('org_id') or 1
        org = db.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        org = dict(org) if org else {'name': 'Papia Tech'}
        projects = _projects_for_client(db, client_id, org_id)
        docs = []
        try:
            docs = [dict(r) for r in db.execute(
                "SELECT id, kind, title, original_name, mime_type, size_bytes, url, created_at "
                "FROM client_documents WHERE client_id = ? AND org_id = ? AND portal_visible = 1 "
                "ORDER BY created_at DESC, id DESC", (client_id, org_id)).fetchall()]
        except Exception:
            docs = []
        contracts = []
        try:
            contracts = [dict(r) for r in db.execute(
                "SELECT id, title, updated_at, created_at FROM contract_envelopes "
                "WHERE client_id = ? AND org_id = ? AND status = 'completed' AND portal_visible = 1 "
                "ORDER BY updated_at DESC, id DESC", (client_id, org_id)).fetchall()]
        except Exception:
            contracts = []
        schedule = []
        try:
            schedule = [dict(r) for r in db.execute(
                "SELECT * FROM payment_schedule WHERE client_id = ? ORDER BY due_date, id",
                (client_id,)).fetchall()]
        except Exception:
            schedule = []
    finally:
        db.close()
    total = float(c.get('total_cost') or 0)
    paid = float(c.get('amount_paid') or 0)
    money = {'total': total, 'paid': paid, 'balance': max(total - paid, 0),
             'pct': int(round(min(paid / total, 1) * 100)) if total else 0}
    overall_total = sum(p['total'] for p in projects)
    overall_done = sum(p['done'] for p in projects)
    return {'client': c, 'org': org, 'projects': projects, 'docs': docs, 'money': money,
            'contracts': contracts,
            'schedule': schedule,
            'overall_pct': int(round(overall_done * 100.0 / overall_total)) if overall_total else 0}


def _render_portal(client_ids, email, preview=False):
    bundles = [b for b in (_client_bundle(i) for i in client_ids) if b]
    if not bundles:
        return redirect(url_for('portal.login', err='no_client'))
    sel = request.args.get('c', type=int)
    current = next((b for b in bundles if b['client']['id'] == sel), bundles[0])
    return render_template('portal/home.html', b=current, bundles=bundles, email=email,
                           preview=preview)


# ── Rutas públicas (cliente) ───────────────────────────────────────────────
@portal_bp.route('/')
def home():
    ids = session.get('portal_client_ids')
    if not ids:
        return redirect(url_for('portal.login'))
    return _render_portal(ids, session.get('portal_email', ''))


@portal_bp.route('/login')
def login():
    if session.get('portal_client_ids'):
        return redirect(url_for('portal.home'))
    errors = {
        'no_client': 'No encontramos un proyecto asociado a ese correo. Usa el mismo email que registraste con nosotros.',
        'google': 'No se pudo iniciar sesión con Google. Intenta de nuevo.',
    }
    return render_template('portal/login.html', error=errors.get(request.args.get('err', '')),
                           email=request.args.get('email', ''))


@portal_bp.route('/login/google')
def google_start():
    from routes.auth import _google_creds, _google_redirect_uri
    client_id, _ = _google_creds()
    if not client_id:
        return redirect(url_for('portal.login', err='google'))
    state = secrets.token_urlsafe(24)
    session['google_oauth_state'] = state
    session['google_oauth_next'] = ''
    session['google_oauth_mode'] = 'portal'
    params = {
        'client_id': client_id,
        'redirect_uri': _google_redirect_uri(),
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'prompt': 'select_account',
    }
    return redirect(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params))


@portal_bp.route('/logout')
def logout():
    session.pop('portal_email', None)
    session.pop('portal_client_ids', None)
    return redirect(url_for('portal.login'))


@portal_bp.route('/doc/<int:doc_id>')
def open_doc(doc_id):
    ids = session.get('portal_client_ids') or []
    is_admin = bool(session.get('logged_in'))
    if not ids and not is_admin:
        return redirect(url_for('portal.login'))
    db = get_db()
    try:
        row = db.execute("SELECT * FROM client_documents WHERE id = ?", (doc_id,)).fetchone()
    finally:
        db.close()
    if not row:
        abort(404)
    doc = dict(row)
    if not is_admin and (doc['client_id'] not in ids or not doc.get('portal_visible')):
        abort(404)
    if doc['kind'] == 'link':
        return redirect(doc.get('url') or url_for('portal.home'))
    from models.client_document import file_path
    path = file_path(doc)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype=doc.get('mime_type') or None,
                     as_attachment=bool(request.args.get('dl')),
                     download_name=doc.get('original_name') or doc.get('title') or 'documento')


@portal_bp.route('/contrato/<int:env_id>')
def open_contract(env_id):
    """PDF firmado (Papia Sign) marcado como visible en el portal."""
    ids = session.get('portal_client_ids') or []
    is_admin = bool(session.get('logged_in'))
    if not ids and not is_admin:
        return redirect(url_for('portal.login'))
    db = get_db()
    try:
        row = db.execute("SELECT * FROM contract_envelopes WHERE id = ?", (env_id,)).fetchone()
    finally:
        db.close()
    if not row:
        abort(404)
    env = dict(row)
    if env.get('status') != 'completed':
        abort(404)
    if not is_admin and (env.get('client_id') not in ids or not env.get('portal_visible')):
        abort(404)
    from models.contract import final_path
    path = final_path(env)
    if not os.path.isfile(path):
        abort(404)
    name = (env.get('title') or 'contrato').strip()
    if not name.lower().endswith('.pdf'):
        name += ' (firmado).pdf'
    return send_file(path, mimetype='application/pdf', as_attachment=bool(request.args.get('dl')),
                     download_name=name)


# ── Rutas de administrador ─────────────────────────────────────────────────
@portal_bp.route('/admin/preview/<int:client_id>')
@_admin_required
def admin_preview(client_id):
    db = get_db()
    try:
        c = db.execute("SELECT id, email FROM clients WHERE id = ? AND org_id = ?",
                       (client_id, _admin_org())).fetchone()
    finally:
        db.close()
    if not c:
        abort(404)
    return _render_portal([c[0]], c[1] or '', preview=True)


@portal_bp.route('/admin/doc/<int:doc_id>/toggle', methods=['POST'])
@_admin_required
def admin_toggle_doc(doc_id):
    ensure_portal_schema()
    db = get_db()
    try:
        row = db.execute("SELECT client_id, portal_visible FROM client_documents WHERE id = ? AND org_id = ?",
                         (doc_id, _admin_org())).fetchone()
        if not row:
            abort(404)
        new = 0 if row[1] else 1
        db.execute("UPDATE client_documents SET portal_visible = ? WHERE id = ?", (new, doc_id))
        db.commit()
        client_id = row[0]
    finally:
        db.close()
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': True, 'visible': bool(new)})
    return redirect(url_for('clients.detail', client_id=client_id) + '#documentos')


@portal_bp.route('/admin/contract/<int:env_id>/toggle', methods=['POST'])
@_admin_required
def admin_toggle_contract(env_id):
    ensure_portal_schema()
    db = get_db()
    try:
        row = db.execute("SELECT client_id, portal_visible FROM contract_envelopes WHERE id = ? AND org_id = ?",
                         (env_id, _admin_org())).fetchone()
        if not row:
            abort(404)
        new = 0 if row[1] else 1
        db.execute("UPDATE contract_envelopes SET portal_visible = ? WHERE id = ?", (new, env_id))
        db.commit()
        client_id = row[0]
    finally:
        db.close()
    if not client_id:
        return redirect(url_for('contracts.detail', env_id=env_id))
    return redirect(url_for('clients.detail', client_id=client_id) + '#contratos')
