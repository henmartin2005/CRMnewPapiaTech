import os
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import (Blueprint, request, jsonify, render_template,
                   redirect, url_for, flash)
from database import get_db

emails_bp = Blueprint('emails', __name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]


# ── Credentials ───────────────────────────────────────────────────────────────

def _client_config():
    return {
        "web": {
            "client_id":     os.getenv('GMAIL_CLIENT_ID', ''),
            "client_secret": os.getenv('GMAIL_CLIENT_SECRET', ''),
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv('GMAIL_REDIRECT_URI', '')],
        }
    }


def _load_creds():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    db  = get_db()
    row = db.execute("SELECT * FROM gmail_tokens WHERE id=1").fetchone()
    db.close()

    if not row or not row['refresh_token']:
        return None

    creds = Credentials(
        token=row['access_token'],
        refresh_token=row['refresh_token'],
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv('GMAIL_CLIENT_ID'),
        client_secret=os.getenv('GMAIL_CLIENT_SECRET'),
        scopes=SCOPES,
    )

    if not creds.valid:
        try:
            creds.refresh(Request())
            db = get_db()
            db.execute(
                "UPDATE gmail_tokens SET access_token=?, updated_at=CURRENT_TIMESTAMP WHERE id=1",
                (creds.token,),
            )
            db.commit()
            db.close()
        except Exception:
            return None

    return creds


def _gmail_service():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None
    creds = _load_creds()
    return build('gmail', 'v1', credentials=creds) if creds else None


def _is_connected():
    db  = get_db()
    row = db.execute("SELECT refresh_token FROM gmail_tokens WHERE id=1").fetchone()
    db.close()
    return bool(row and row['refresh_token'])


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conversations():
    db   = get_db()
    rows = db.execute("""
        SELECT
            e.client_id,
            c.first_name, c.last_name, c.email AS client_email,
            e.subject    AS last_subject,
            e.created_at AS last_at,
            COUNT(e.id)  AS total
        FROM emails e
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE e.id = (
            SELECT id FROM emails
            WHERE client_id = e.client_id ORDER BY created_at DESC LIMIT 1
        )
        GROUP BY e.client_id
        ORDER BY e.created_at DESC
    """).fetchall()
    db.close()
    return rows


def _thread(client_id):
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM emails WHERE client_id=? ORDER BY created_at ASC", (client_id,)
    ).fetchall()
    db.close()
    return rows


def _save_sent(client_id, to_email, subject, body, gmail_id):
    db = get_db()
    db.execute("""
        INSERT INTO emails (client_id, direction, to_email, subject, body, gmail_message_id, status)
        VALUES (?, 'sent', ?, ?, ?, ?, 'sent')
    """, (client_id, to_email, subject, body, gmail_id))
    db.commit()
    db.close()


def _all_templates():
    db   = get_db()
    rows = db.execute("SELECT * FROM email_templates ORDER BY id").fetchall()
    db.close()
    return rows


# ── OAuth2 ────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails/auth')
def gmail_auth():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash('Instala las dependencias de Google primero: pip install google-auth-oauthlib google-api-python-client', 'danger')
        return redirect(url_for('emails.index'))

    if not os.getenv('GMAIL_CLIENT_ID'):
        flash('Configura GMAIL_CLIENT_ID y GMAIL_CLIENT_SECRET en el archivo .env primero.', 'danger')
        return redirect(url_for('emails.index'))

    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = os.getenv('GMAIL_REDIRECT_URI',
                                  url_for('emails.oauth2callback', _external=True))
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    return redirect(auth_url)


@emails_bp.route('/emails/oauth2callback')
def oauth2callback():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash('Dependencias de Google no instaladas.', 'danger')
        return redirect(url_for('emails.index'))

    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = os.getenv('GMAIL_REDIRECT_URI',
                                  url_for('emails.oauth2callback', _external=True))
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        flash(f'Error al conectar Gmail: {exc}', 'danger')
        return redirect(url_for('emails.index'))

    creds = flow.credentials
    db    = get_db()
    db.execute("DELETE FROM gmail_tokens WHERE id=1")
    db.execute(
        "INSERT INTO gmail_tokens (id, access_token, refresh_token) VALUES (1, ?, ?)",
        (creds.token, creds.refresh_token),
    )
    db.commit()
    db.close()

    flash('Gmail conectado correctamente.', 'success')
    return redirect(url_for('emails.index'))


@emails_bp.route('/emails/disconnect', methods=['POST'])
def disconnect():
    db = get_db()
    db.execute("DELETE FROM gmail_tokens WHERE id=1")
    db.commit()
    db.close()
    flash('Gmail desconectado.', 'success')
    return redirect(url_for('emails.index'))


# ── Views ─────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails')
def index():
    connected     = _is_connected()
    conversations = _conversations() if connected else []
    templates     = _all_templates()

    active_id     = request.args.get('client_id', type=int)
    active_thread = []
    active_client = None

    if active_id:
        active_thread = _thread(active_id)
        db            = get_db()
        active_client = db.execute("SELECT * FROM clients WHERE id=?", (active_id,)).fetchone()
        db.close()

    db          = get_db()
    all_clients = db.execute(
        "SELECT id, first_name, last_name, email FROM clients "
        "WHERE email IS NOT NULL AND email != '' ORDER BY first_name"
    ).fetchall()
    db.close()

    return render_template('emails/index.html',
        connected=connected,
        conversations=conversations,
        templates=templates,
        active_id=active_id,
        active_thread=active_thread,
        active_client=active_client,
        all_clients=all_clients,
    )


# ── Send ──────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails/send', methods=['POST'])
def send():
    data      = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    to_email  = (data.get('to_email')  or '').strip()
    subject   = (data.get('subject')   or '').strip()
    body      = (data.get('body')      or '').strip()

    if not to_email or not subject or not body:
        return jsonify({'success': False, 'error': 'to_email, subject y body son requeridos'}), 400

    service = _gmail_service()
    if not service:
        return jsonify({'success': False, 'error': 'Gmail no conectado. Ve a Emails → Conectar Gmail.'}), 503

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['To']      = to_email
        body_html      = body.replace('\n', '<br>')
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(
            f'<div style="font-family:sans-serif;font-size:14px;line-height:1.6;color:#0B0E14">{body_html}</div>',
            'html',
        ))

        raw  = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()

        _save_sent(client_id, to_email, subject, body, sent.get('id'))

        if client_id:
            db = get_db()
            db.execute(
                "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'email', ?)",
                (int(client_id), f"Email enviado: {subject}"),
            )
            db.commit()
            db.close()

        return jsonify({'success': True, 'id': sent.get('id')})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


# ── Templates CRUD ────────────────────────────────────────────────────────────

@emails_bp.route('/emails/templates')
def templates_page():
    return render_template('emails/templates.html', templates=_all_templates())


@emails_bp.route('/emails/templates', methods=['POST'])
def create_template():
    data    = request.get_json(silent=True) or {}
    name    = (data.get('name')    or '').strip()
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body')    or '').strip()

    if not name or not subject or not body:
        return jsonify({'success': False, 'error': 'name, subject y body son requeridos'}), 400

    db  = get_db()
    cur = db.execute(
        "INSERT INTO email_templates (name, subject, body) VALUES (?, ?, ?)",
        (name, subject, body),
    )
    db.commit()
    new_id = cur.lastrowid
    db.close()
    return jsonify({'success': True, 'id': new_id}), 201


@emails_bp.route('/emails/templates/<int:tid>', methods=['PUT'])
def update_template(tid):
    data    = request.get_json(silent=True) or {}
    name    = (data.get('name')    or '').strip()
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body')    or '').strip()

    if not name or not subject or not body:
        return jsonify({'success': False, 'error': 'name, subject y body son requeridos'}), 400

    db = get_db()
    db.execute(
        "UPDATE email_templates SET name=?, subject=?, body=? WHERE id=?",
        (name, subject, body, tid),
    )
    db.commit()
    db.close()
    return jsonify({'success': True})


@emails_bp.route('/emails/templates/<int:tid>', methods=['DELETE'])
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM email_templates WHERE id=?", (tid,))
    db.commit()
    db.close()
    return jsonify({'success': True})
