import os
from datetime import timedelta
from flask import Flask, render_template, session, redirect, url_for, request

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


# ── Auth guard: protect every route except login, logout, static, and webhook ──
@app.before_request
def require_login():
    open_endpoints = {'auth.login', 'auth.logout', 'static', 'whatsapp.webhook'}
    if request.endpoint in open_endpoints:
        return
    if request.path.startswith('/api/'):
        return
    if not session.get('logged_in'):
        return redirect(url_for('auth.login', next=request.full_path))


# ── Context processor: unread WhatsApp badge available in every template ──
@app.context_processor
def inject_wa_unread():
    try:
        return {
            'wa_unread': get_unread_count(),
            'task_due_count': get_due_task_count(),
        }
    except Exception:
        return {'wa_unread': 0, 'task_due_count': 0}


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    stats = get_dashboard_stats()
    todays_followups = get_todays_followups()
    return render_template(
        'dashboard.html',
        stats=stats,
        todays_followups=todays_followups,
        method_labels=dict(FOLLOW_UP_METHODS),
    )


# ── Template filters ─────────────────────────────────────────────────────────
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
