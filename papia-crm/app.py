import os
from datetime import timedelta, datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template, session, redirect, url_for, request, g

# Load .env from the same folder as this file, regardless of cwd
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / '.env', override=True)
except ImportError:
    pass

from database import init_db
from models.client import (
    get_dashboard_stats,
    get_due_task_count,
    get_todays_followups,
    FOLLOW_UP_METHODS,
)
from flask_cors import CORS
from routes.clients import clients_bp
from routes.pipeline import pipeline_bp
from routes.followups import followups_bp
from routes.whatsapp import whatsapp_bp, get_unread_count
from routes.auth import auth_bp
from routes.leads import leads_bp
from routes.emails import emails_bp
from routes.proposals import proposals_bp
from routes.admin import admin_bp
from routes.super import super_bp
from routes.payments import payments_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'papia-crm-dev-secret-2024')
app.permanent_session_lifetime = timedelta(days=7)

# CORS solo para el endpoint público de leads
CORS(app, resources={r"/api/*": {"origins": [
    "https://www.papiatech.com",
    "https://papiatech.com",
]}})

app.register_blueprint(auth_bp)
app.register_blueprint(leads_bp)
app.register_blueprint(emails_bp)

# Init DB on every startup (safe: all statements use CREATE IF NOT EXISTS)
init_db()
app.register_blueprint(clients_bp)
app.register_blueprint(pipeline_bp)
app.register_blueprint(followups_bp)
app.register_blueprint(whatsapp_bp)
app.register_blueprint(proposals_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(super_bp)
app.register_blueprint(payments_bp)


# ── Set org context on each request ─────────────────────────────────────────
@app.before_request
def set_org_context():
    if session.get('logged_in'):
        # superadmin can "view as" another org
        g.org_id = session.get('viewed_org_id') or session.get('org_id', 1)
    else:
        g.org_id = 1


# ── Auth guard: protect every route except login, logout, static, and webhook ──
@app.before_request
def require_login():
    open_endpoints = {'auth.login', 'auth.logout', 'static', 'whatsapp.webhook', 'payments.stripe_webhook', 'payments.success'}
    if request.endpoint in open_endpoints:
        return
    if request.path.startswith('/api/'):
        return
    if not session.get('logged_in'):
        return redirect(url_for('auth.login', next=request.full_path))
    # Super routes only for superadmin
    if request.path.startswith('/super/') and session.get('user_role') != 'superadmin':
        return redirect(url_for('dashboard'))


_ALL_MODULES = {'whatsapp', 'emails', 'calendar', 'proposals', 'tasks'}


# ── Context processor: unread badge + enabled modules ──────────────────────
@app.context_processor
def inject_globals():
    org_id = g.org_id if hasattr(g, 'org_id') else 1

    # Badges & counts — always scoped to current org
    try:
        wa_unread      = get_unread_count(org_id)
        task_due_count = get_due_task_count(org_id)
    except Exception:
        wa_unread = task_due_count = 0

    try:
        from database import get_db as _gdb
        _cdb = _gdb()
        client_count = _cdb.execute(
            "SELECT COUNT(*) FROM clients WHERE org_id=?", (org_id,)
        ).fetchone()[0]
        org_name = (_cdb.execute(
            "SELECT name FROM organizations WHERE id=?", (org_id,)
        ).fetchone() or {}).get('name', '')
        _cdb.close()
    except Exception:
        client_count = 0
        org_name     = ''

    # Module permissions: org-level ∩ user-level
    role    = session.get('user_role', 'user')
    user_id = session.get('user_id')
    try:
        from database import get_db as _get_db
        _db = _get_db()
        # Modules enabled for this org
        _org_rows = _db.execute(
            "SELECT module FROM org_modules WHERE org_id=? AND enabled=1", (org_id,)
        ).fetchall()
        org_enabled = {r['module'] for r in _org_rows}

        if role in ('admin', 'superadmin'):
            # Admin sees all org-enabled modules
            enabled_modules = org_enabled
        elif user_id:
            # User sees intersection of org-enabled and user-enabled
            _usr_rows = _db.execute(
                "SELECT module FROM user_modules WHERE user_id=? AND enabled=1", (user_id,)
            ).fetchall()
            enabled_modules = org_enabled & {r['module'] for r in _usr_rows}
        else:
            enabled_modules = set()
        _db.close()
    except Exception:
        enabled_modules = _ALL_MODULES if role in ('admin', 'superadmin') else set()

    return {
        'wa_unread':        wa_unread,
        'task_due_count':   task_due_count,
        'enabled_modules':  enabled_modules,
        'is_admin':         role in ('admin', 'superadmin'),
        'is_superadmin':    role == 'superadmin',
        'current_org_id':   org_id,
        'client_count':     client_count,
        'current_org_name': org_name,
    }


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    stats = get_dashboard_stats(org_id)
    todays_followups = get_todays_followups(org_id)
    return render_template(
        'dashboard.html',
        stats=stats,
        todays_followups=todays_followups,
        method_labels=dict(FOLLOW_UP_METHODS),
    )


# ── Template filters ─────────────────────────────────────────────────────────
@app.template_filter('localtime')
def localtime_filter(value, fmt='%I:%M %p'):
    if not value:
        return ''
    try:
        dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo('America/New_York')).strftime(fmt)
    except Exception:
        return str(value)[11:16] if len(str(value)) > 16 else str(value)


@app.template_filter('localdatetime')
def localdatetime_filter(value):
    return localtime_filter(value, fmt='%b %d, %I:%M %p')


@app.template_filter('currency')
def currency_filter(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


@app.template_filter('stage_label')
def stage_label_filter(value):
    from models.client import PIPELINE_STAGES
    return dict(PIPELINE_STAGES).get(value, value)


@app.template_filter('project_label')
def project_label_filter(value):
    from models.client import PROJECT_TYPES
    return dict(PROJECT_TYPES).get(value, value)


@app.template_filter('note_label')
def note_label_filter(value):
    from models.client import NOTE_TYPES
    return dict(NOTE_TYPES).get(value, value)


@app.template_filter('method_label')
def method_label_filter(value):
    return dict(FOLLOW_UP_METHODS).get(value, value)


@app.template_filter('method_icon')
def method_icon_filter(value):
    icons = {
        'phone': 'telephone-fill',
        'email': 'envelope-fill',
        'whatsapp': 'whatsapp',
        'meeting': 'camera-video-fill',
        'other': 'three-dots',
    }
    return icons.get(value, 'chat-fill')


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
