import os
import re
import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import (Blueprint, request, jsonify, render_template,
                   redirect, url_for, flash, session, g)
from database import get_db
from models.proposal_email import get_email_draft

emails_bp = Blueprint('emails', __name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]


# ── Company settings helpers ──────────────────────────────────────────────────

def _get_settings(org_id=1):
    db   = get_db()
    rows = db.execute("SELECT key, value FROM settings WHERE org_id=?", (org_id,)).fetchall()
    db.close()
    return {r['key']: r['value'] for r in rows}


def _build_html_email(body_text, settings):
    logo_url   = settings.get('company_logo_url', '')
    sig_name   = settings.get('signature_name', '')
    sig_title  = settings.get('signature_title', '')
    sig_phone  = settings.get('signature_phone', '')
    sig_email  = settings.get('signature_email', '')
    sig_web    = settings.get('signature_website', '')

    logo_html = (
        f'<div style="text-align:center;padding:28px 0 20px 0;">'
        f'<img src="{logo_url}" alt="Papia Technology Solutions" '
        f'style="height:180px;max-width:560px;object-fit:contain;display:inline-block;">'
        f'</div>'
        if logo_url else
        '<div style="text-align:center;font-size:20px;font-weight:700;color:#2A5BFF;padding:28px 0 20px 0;">Papia Technology Solutions</div>'
    )

    phone_line   = f'<br><span style="color:#6B7280;">📞 {sig_phone}</span>' if sig_phone else ''
    email_line   = f'<br><span style="color:#6B7280;">✉️ {sig_email}</span>'  if sig_email else ''
    website_line = (
        f'<br><a href="{sig_web}" style="color:#2A5BFF;text-decoration:none;">{sig_web}</a>'
        if sig_web else ''
    )

    body_html = body_text.replace('\n', '<br>')

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden;">
        <tr>
          <td style="padding:0 40px;border-bottom:1px solid #e5e7eb;">
            {logo_html}
          </td>
        </tr>
        <tr>
          <td style="padding:32px 40px;font-size:14px;line-height:1.7;color:#111827;">
            {body_html}
          </td>
        </tr>
        <tr>
          <td style="padding:24px 40px;border-top:1px solid #e5e7eb;background:#f9fafb;">
            <div style="font-size:13px;line-height:1.6;">
              <strong style="color:#111827;">{sig_name}</strong>
              <br><span style="color:#6B7280;">{sig_title}</span>
              <br><span style="color:#6B7280;">Papia Technology Solutions LLC</span>
              {phone_line}{email_line}{website_line}
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Credentials ───────────────────────────────────────────────────────────────

def _get_gmail_creds_for_org(org_id):
    """Returns (client_id, client_secret) for the org, falling back to .env."""
    db  = get_db()
    org = db.execute(
        "SELECT gmail_client_id, gmail_client_secret FROM organizations WHERE id=?", (org_id,)
    ).fetchone()
    db.close()
    client_id     = (org['gmail_client_id']     if org and org['gmail_client_id']     else '') or os.getenv('GMAIL_CLIENT_ID', '')
    client_secret = (org['gmail_client_secret'] if org and org['gmail_client_secret'] else '') or os.getenv('GMAIL_CLIENT_SECRET', '')
    return client_id, client_secret


def _client_config(org_id=1):
    client_id, client_secret = _get_gmail_creds_for_org(org_id)
    redirect_uri = os.getenv('GMAIL_REDIRECT_URI', '')
    return {
        "web": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _load_creds(org_id=1):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    db  = get_db()
    row = db.execute("SELECT * FROM gmail_tokens WHERE org_id=?", (org_id,)).fetchone()
    db.close()

    if not row or not row['refresh_token']:
        return None

    client_id, client_secret = _get_gmail_creds_for_org(org_id)

    creds = Credentials(
        token=row['access_token'],
        refresh_token=row['refresh_token'],
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )

    if not creds.valid:
        try:
            creds.refresh(Request())
            db = get_db()
            db.execute(
                "UPDATE gmail_tokens SET access_token=?, updated_at=CURRENT_TIMESTAMP WHERE org_id=?",
                (creds.token, org_id),
            )
            db.commit()
            db.close()
        except Exception:
            return None

    return creds


def _gmail_service(org_id=1):
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None
    creds = _load_creds(org_id)
    return build('gmail', 'v1', credentials=creds) if creds else None


def _is_connected(org_id=1):
    db  = get_db()
    row = db.execute("SELECT refresh_token FROM gmail_tokens WHERE org_id=?", (org_id,)).fetchone()
    db.close()
    return bool(row and row['refresh_token'])


def _get_org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conversations_by_dir(direction, org_id=1):
    db   = get_db()
    rows = db.execute("""
        SELECT
            e.client_id,
            e.to_email,
            COALESCE(c.first_name, '') AS first_name,
            COALESCE(c.last_name, '')  AS last_name,
            e.subject    AS last_subject,
            e.created_at AS last_at,
            COUNT(e.id)  AS total
        FROM emails e
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE e.direction = ?
          AND e.org_id = ?
          AND e.client_id IS NOT NULL
          AND e.id = (
              SELECT id FROM emails e2
              WHERE e2.client_id = e.client_id AND e2.direction = ?
              ORDER BY e2.created_at DESC LIMIT 1
          )
        GROUP BY e.client_id
        ORDER BY e.created_at DESC
    """, (direction, org_id, direction)).fetchall()
    db.close()
    return rows


def _thread(client_id, org_id=1):
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM emails WHERE client_id=? AND org_id=? ORDER BY created_at ASC",
        (client_id, org_id)
    ).fetchall()
    db.close()
    return rows


def _save_sent(client_id, to_email, subject, body, gmail_id, org_id=1):
    db = get_db()
    db.execute("""
        INSERT INTO emails (client_id, direction, to_email, subject, body, gmail_message_id, status, org_id)
        VALUES (?, 'sent', ?, ?, ?, ?, 'sent', ?)
    """, (client_id, to_email, subject, body, gmail_id, org_id))
    db.commit()
    db.close()


def _all_templates(org_id=1):
    db   = get_db()
    rows = db.execute("SELECT * FROM email_templates WHERE org_id=? ORDER BY id", (org_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


# ── Gmail sync helpers ────────────────────────────────────────────────────────

def _extract_email(addr):
    if not addr:
        return ''
    m = re.search(r'<([^>]+)>', addr)
    return m.group(1).strip() if m else addr.strip()


def _extract_body(payload):
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data', '')
    if data:
        try:
            text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            if 'html' in mime:
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
            return text
        except Exception:
            pass
    for part in payload.get('parts', []):
        if part.get('mimeType') == 'text/plain':
            body = _extract_body(part)
            if body:
                return body
    for part in payload.get('parts', []):
        body = _extract_body(part)
        if body:
            return body
    return ''


def _sync_gmail(org_id=1):
    service = _gmail_service(org_id)
    if not service:
        return 0, 'Gmail no conectado'

    db = get_db()
    contacts = db.execute(
        "SELECT id, email FROM clients WHERE email IS NOT NULL AND email != '' AND org_id=?",
        (org_id,)
    ).fetchall()
    email_to_client = {r['email'].lower(): r['id'] for r in contacts}

    if not email_to_client:
        db.close()
        return 0, 'No hay clientes con email registrado'

    synced = 0

    for label, direction in [('INBOX', 'received'), ('SENT', 'sent')]:
        try:
            result = _gmail_service(org_id).users().messages().list(
                userId='me', labelIds=[label], maxResults=100
            ).execute()
            messages = result.get('messages', [])
        except Exception:
            continue

        for msg_ref in messages:
            msg_id = msg_ref['id']
            if db.execute("SELECT 1 FROM emails WHERE gmail_message_id=? AND org_id=?", (msg_id, org_id)).fetchone():
                continue

            try:
                msg = _gmail_service(org_id).users().messages().get(
                    userId='me', id=msg_id, format='full'
                ).execute()
            except Exception:
                continue

            hdrs = {h['name'].lower(): h['value']
                    for h in msg['payload'].get('headers', [])}
            from_addr = _extract_email(hdrs.get('from', ''))
            to_addr   = _extract_email(hdrs.get('to', ''))
            subject   = hdrs.get('subject', '(sin asunto)')

            internal_ms = int(msg.get('internalDate', 0))
            created_at  = datetime.fromtimestamp(
                internal_ms / 1000, tz=timezone.utc
            ).strftime('%Y-%m-%d %H:%M:%S')

            if direction == 'received':
                contact_email = from_addr.lower()
                other_email   = from_addr
            else:
                contact_email = to_addr.lower()
                other_email   = to_addr

            client_id = email_to_client.get(contact_email)
            if not client_id:
                continue

            body = _extract_body(msg['payload'])[:4000]

            db.execute("""
                INSERT INTO emails
                    (client_id, direction, to_email, subject, body, gmail_message_id, status, created_at, org_id)
                VALUES (?, ?, ?, ?, ?, ?, 'synced', ?, ?)
            """, (client_id, direction, other_email, subject, body, msg_id, created_at, org_id))
            synced += 1

    db.commit()
    db.close()
    return synced, None


# ── OAuth2 ────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails/auth')
def gmail_auth():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash('Instala las dependencias de Google primero: pip install google-auth-oauthlib google-api-python-client', 'danger')
        return redirect(url_for('emails.index'))

    org_id = _get_org_id()
    client_id, _ = _get_gmail_creds_for_org(org_id)
    if not client_id:
        flash('Configura las credenciales de Gmail en Usuarios & Módulos → Configuración de Gmail.', 'danger')
        return redirect(url_for('emails.index'))

    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    flow = Flow.from_client_config(_client_config(org_id), scopes=SCOPES)
    flow.redirect_uri = os.getenv('GMAIL_REDIRECT_URI',
                                  url_for('emails.oauth2callback', _external=True))
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    session['oauth_state']    = state
    session['code_verifier']  = flow.code_verifier
    return redirect(auth_url)


@emails_bp.route('/emails/oauth2callback')
def oauth2callback():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash('Dependencias de Google no instaladas.', 'danger')
        return redirect(url_for('emails.index'))

    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    org_id = _get_org_id()

    flow = Flow.from_client_config(_client_config(org_id), scopes=SCOPES,
                                   state=session.get('oauth_state'))
    flow.redirect_uri    = os.getenv('GMAIL_REDIRECT_URI',
                                     url_for('emails.oauth2callback', _external=True))
    flow.code_verifier   = session.get('code_verifier')
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        flash(f'Error al conectar Gmail: {exc}', 'danger')
        return redirect(url_for('emails.index'))

    creds = flow.credentials
    db    = get_db()
    db.execute("DELETE FROM gmail_tokens WHERE org_id=?", (org_id,))
    db.execute(
        "INSERT INTO gmail_tokens (access_token, refresh_token, org_id) VALUES (?, ?, ?)",
        (creds.token, creds.refresh_token, org_id),
    )
    db.commit()
    db.close()

    flash('Gmail conectado correctamente.', 'success')
    return redirect(url_for('emails.index'))


@emails_bp.route('/emails/disconnect', methods=['POST'])
def disconnect():
    org_id = _get_org_id()
    db = get_db()
    db.execute("DELETE FROM gmail_tokens WHERE org_id=?", (org_id,))
    db.commit()
    db.close()
    flash('Gmail desconectado.', 'success')
    return redirect(url_for('emails.index'))


# ── Views ─────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails')
def index():
    org_id = _get_org_id()
    connected    = _is_connected(org_id)
    sent_convos  = _conversations_by_dir('sent', org_id)     if connected else []
    recv_convos  = _conversations_by_dir('received', org_id) if connected else []
    templates    = _all_templates(org_id)
    proposal_draft = None
    draft_id = request.args.get('proposal_draft', type=int)
    if draft_id:
        proposal_draft = get_email_draft(draft_id)

    active_id     = request.args.get('client_id', type=int)
    active_thread = []
    active_client = None

    if active_id:
        active_thread = _thread(active_id, org_id)
        db            = get_db()
        active_client = db.execute(
            "SELECT * FROM clients WHERE id=? AND org_id=?", (active_id, org_id)
        ).fetchone()
        db.close()

    db          = get_db()
    all_clients = db.execute(
        "SELECT id, first_name, last_name, email FROM clients "
        "WHERE email IS NOT NULL AND email != '' AND org_id=? ORDER BY first_name",
        (org_id,)
    ).fetchall()
    db.close()

    # Pre-filled compose data (eg. from Stripe payment link)
    compose_preset = None
    if request.args.get('compose') == '1' and active_client:
        compose_preset = {
            'client_id':    active_client['id'],
            'client_name':  f"{active_client['first_name']} {active_client['last_name']}",
            'client_email': active_client['email'] or '',
            'subject':      request.args.get('subject', ''),
            'body':         request.args.get('body', ''),
        }

    email_settings = _get_settings(org_id)
    return render_template('emails/index.html',
        connected=connected,
        sent_convos=sent_convos,
        recv_convos=recv_convos,
        templates=templates,
        email_settings=email_settings,
        active_id=active_id,
        active_thread=active_thread,
        active_client=active_client,
        all_clients=all_clients,
        proposal_draft=proposal_draft,
        compose_preset=compose_preset,
    )


# ── Send ──────────────────────────────────────────────────────────────────────

@emails_bp.route('/emails/send', methods=['POST'])
def send():
    org_id    = _get_org_id()
    data      = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    to_email  = (data.get('to_email')  or '').strip()
    subject   = (data.get('subject')   or '').strip()
    body      = (data.get('body')      or '').strip()

    if not to_email or not subject or not body:
        return jsonify({'success': False, 'error': 'to_email, subject y body son requeridos'}), 400

    service = _gmail_service(org_id)
    if not service:
        return jsonify({'success': False, 'error': 'Gmail no conectado. Ve a Emails → Conectar Gmail.'}), 503

    try:
        settings   = _get_settings(org_id)
        html_body  = _build_html_email(body, settings)

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['To']      = to_email
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        raw  = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()

        _save_sent(client_id, to_email, subject, body, sent.get('id'), org_id)

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
    org_id = _get_org_id()
    return render_template('emails/templates.html', templates=_all_templates(org_id))


@emails_bp.route('/emails/templates', methods=['POST'])
def create_template():
    org_id  = _get_org_id()
    data    = request.get_json(silent=True) or {}
    name    = (data.get('name')    or '').strip()
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body')    or '').strip()

    if not name or not subject or not body:
        return jsonify({'success': False, 'error': 'name, subject y body son requeridos'}), 400

    db  = get_db()
    cur = db.execute(
        "INSERT INTO email_templates (name, subject, body, org_id) VALUES (?, ?, ?, ?)",
        (name, subject, body, org_id),
    )
    db.commit()
    new_id = cur.lastrowid
    db.close()
    return jsonify({'success': True, 'id': new_id}), 201


@emails_bp.route('/emails/templates/<int:tid>', methods=['PUT'])
def update_template(tid):
    org_id  = _get_org_id()
    data    = request.get_json(silent=True) or {}
    name    = (data.get('name')    or '').strip()
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body')    or '').strip()

    if not name or not subject or not body:
        return jsonify({'success': False, 'error': 'name, subject y body son requeridos'}), 400

    db = get_db()
    db.execute(
        "UPDATE email_templates SET name=?, subject=?, body=? WHERE id=? AND org_id=?",
        (name, subject, body, tid, org_id),
    )
    db.commit()
    db.close()
    return jsonify({'success': True})


@emails_bp.route('/emails/templates/<int:tid>', methods=['DELETE'])
def delete_template(tid):
    org_id = _get_org_id()
    db = get_db()
    db.execute("DELETE FROM email_templates WHERE id=? AND org_id=?", (tid, org_id))
    db.commit()
    db.close()
    return jsonify({'success': True})


# ── Company / Signature settings ──────────────────────────────────────────────

@emails_bp.route('/emails/settings')
def email_settings():
    org_id = _get_org_id()
    settings = _get_settings(org_id)
    return render_template('emails/settings.html', settings=settings)


@emails_bp.route('/emails/settings', methods=['POST'])
def save_email_settings():
    org_id = _get_org_id()
    keys = ['company_logo_url', 'signature_name', 'signature_title',
            'signature_phone', 'signature_email', 'signature_website']
    db = get_db()
    for key in keys:
        value = request.form.get(key, '').strip()
        db.execute(
            "INSERT INTO settings (key, value, org_id) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value, org_id),
        )
    db.commit()
    db.close()
    flash('Configuración guardada correctamente.', 'success')
    return redirect(url_for('emails.email_settings'))


@emails_bp.route('/emails/sync', methods=['POST'])
def sync_emails():
    org_id = _get_org_id()
    synced, error = _sync_gmail(org_id)
    if error:
        return jsonify({'success': False, 'error': error}), 503
    return jsonify({'success': True, 'synced': synced})


@emails_bp.route('/emails/templates/<int:tid>/preview')
def template_preview(tid):
    org_id = _get_org_id()
    db  = get_db()
    row = db.execute("SELECT * FROM email_templates WHERE id=? AND org_id=?", (tid, org_id)).fetchone()
    db.close()
    if not row:
        return 'Plantilla no encontrada', 404
    settings = _get_settings(org_id)
    html = _build_html_email(row['body'], settings)
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@emails_bp.route('/emails/settings/preview')
def signature_preview():
    org_id = _get_org_id()
    settings = _get_settings(org_id)
    html = _build_html_email(
        'Hola {nombre},\n\nEste es un ejemplo de cómo se verán tus emails con la firma y el logo de la empresa.\n\n¡Saludos!',
        settings,
    )
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
