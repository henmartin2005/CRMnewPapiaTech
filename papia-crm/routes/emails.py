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
    'https://www.googleapis.com/auth/calendar',   # agenda del bot de WhatsApp
]


# ── Company settings helpers ──────────────────────────────────────────────────

_LOGO_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')


def _get_settings(org_id=1):
    db   = get_db()
    rows = db.execute("SELECT key, value FROM settings WHERE org_id=?", (org_id,)).fetchall()
    try:
        org = db.execute("SELECT logo_url FROM organizations WHERE id=?", (org_id,)).fetchone()
    except Exception:
        org = None
    db.close()
    data = {r['key']: r['value'] for r in rows}
    data['_org_logo'] = (org['logo_url'] or '') if org else ''
    return data


def _abs_url(url):
    """Convierte rutas relativas (/static/...) en URLs absolutas para que funcionen en Gmail."""
    url = (url or '').strip()
    if not url:
        return ''
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        base = ''
        try:
            from flask import has_request_context
            if has_request_context():
                base = request.url_root.rstrip('/')
        except Exception:
            base = ''
        base = base or os.getenv('APP_BASE_URL', 'https://datos.papiatech.com').rstrip('/')
        return base + url
    return url


def _is_image_url(url):
    path = (url or '').split('?')[0].split('#')[0].lower()
    return path.endswith(_LOGO_EXTS) or '/static/logos/' in path


def _resolve_logo_url(settings):
    """Logo configurado si es una imagen válida; si no, el logo de la organización."""
    url = (settings.get('company_logo_url') or '').strip()
    if url and _is_image_url(url):
        return _abs_url(url)
    org_logo = (settings.get('_org_logo') or '').strip()
    if org_logo and _is_image_url(org_logo):
        return _abs_url(org_logo)
    return ''


def _build_html_email(body_text, settings):
    logo_url   = _resolve_logo_url(settings)
    logo_pos   = settings.get('logo_position') or 'header'
    if logo_pos not in ('header', 'signature', 'both', 'none'):
        logo_pos = 'header'
    logo_align = settings.get('logo_align') or 'center'
    if logo_align not in ('left', 'center', 'right'):
        logo_align = 'center'
    try:
        logo_h = max(24, min(200, int(settings.get('logo_height') or 80)))
    except (TypeError, ValueError):
        logo_h = 80
    sig_name   = settings.get('signature_name', '')
    sig_title  = settings.get('signature_title', '')
    sig_phone  = settings.get('signature_phone', '')
    sig_email  = settings.get('signature_email', '')
    sig_web    = settings.get('signature_website', '')

    logo_img = (
        f'<img src="{logo_url}" alt="Logo" height="{logo_h}" '
        f'style="height:{logo_h}px;max-width:100%;width:auto;display:inline-block;border:0;">'
    ) if logo_url else ''
    if logo_pos in ('header', 'both'):
        logo_html = (
            f'<div style="text-align:{logo_align};padding:24px 0 20px 0;">{logo_img}</div>'
            if logo_url else
            '<div style="text-align:center;font-size:20px;font-weight:700;color:#2A5BFF;padding:28px 0 20px 0;">Papia Technology Solutions</div>'
        )
    else:
        logo_html = ''
    sig_logo_html = (
        f'<div style="text-align:{logo_align};margin-bottom:12px;">{logo_img}</div>'
        if logo_url and logo_pos in ('signature', 'both') else ''
    )

    phone_line   = f'<tr><td style="color:#6B7280;padding:0;">📞 {sig_phone}</td></tr>' if sig_phone else ''
    email_line   = f'<tr><td style="color:#6B7280;padding:0;">✉️ {sig_email}</td></tr>'  if sig_email else ''
    website_line = (
        f'<tr><td style="padding:0;"><a href="{sig_web}" style="color:#2A5BFF;text-decoration:none;">{sig_web}</a></td></tr>'
        if sig_web else ''
    )
    title_line   = f'<tr><td style="color:#6B7280;padding:0;">{sig_title}</td></tr>' if sig_title else ''

    body_html = body_text.replace('\n', '<br>')

    return f"""<!DOCTYPE html>
<html lang="es">
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
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="notranslate" translate="no"
                   style="font-size:13px;line-height:1.6;border-collapse:collapse;">
              <tr><td style="padding:0;">{sig_logo_html}<strong style="color:#111827;">{sig_name}</strong></td></tr>
              {title_line}
              <tr><td style="color:#6B7280;padding:0;">Papia Technology Solutions LLC</td></tr>
              {phone_line}{email_line}{website_line}
            </table>
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
        scopes=None,   # refresca con los permisos ya concedidos (Gmail y/o Calendar)
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
    """Conversaciones agrupadas por cliente; en 'sent' también incluye emails a direcciones nuevas."""
    db   = get_db()
    include_unlinked = 1 if direction == 'sent' else 0
    rows = db.execute("""
        SELECT e.client_id, e.to_email,
               COALESCE(c.first_name, '') AS first_name,
               COALESCE(c.last_name, '')  AS last_name,
               e.subject    AS last_subject,
               e.created_at AS last_at
        FROM emails e
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE e.direction = ?
          AND e.org_id = ?
          AND (e.client_id IS NOT NULL OR ? = 1)
        ORDER BY e.created_at DESC, e.id DESC
    """, (direction, org_id, include_unlinked)).fetchall()
    db.close()
    convos, seen = [], {}
    for r in rows:
        key = ('c', r['client_id']) if r['client_id'] else ('e', (r['to_email'] or '').lower())
        if key in seen:
            seen[key]['total'] += 1
            continue
        name = f"{r['first_name']} {r['last_name']}".strip()
        conv = {
            'client_id': r['client_id'], 'to_email': r['to_email'],
            'first_name': r['first_name'], 'last_name': r['last_name'],
            'last_subject': r['last_subject'], 'last_at': r['last_at'], 'total': 1,
            # nombres que usa la plantilla
            'name': name or (r['to_email'] or ''), 'email': r['to_email'], 'subject': r['last_subject'],
            'last_time': str(r['last_at'])[:10] if r['last_at'] else '',
        }
        seen[key] = conv
        convos.append(conv)
    return convos


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
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

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
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
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

    active_email = (request.args.get('to') or '').strip()
    if active_email and not active_id:
        db = get_db()
        active_thread = db.execute(
            "SELECT * FROM emails WHERE client_id IS NULL AND org_id=? AND lower(to_email)=? "
            "ORDER BY created_at ASC",
            (org_id, active_email.lower()),
        ).fetchall()
        db.close()

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
        active_email=active_email,
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

    # Permite cualquier dirección (una o varias separadas por coma), no solo clientes
    _addrs = [a.strip() for a in re.split(r'[,;]', to_email) if a.strip()]
    if not _addrs or any(not re.fullmatch(r'[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+', a) for a in _addrs):
        return jsonify({'success': False, 'error': 'Dirección de correo no válida'}), 400
    to_email = ', '.join(_addrs)

    # Si el correo pertenece a un cliente, vincularlo para registrar la nota
    if not client_id:
        try:
            _db = get_db()
            _row = _db.execute(
                "SELECT id FROM clients WHERE org_id = ? AND lower(email) = ?",
                (org_id, _addrs[0].lower()),
            ).fetchone()
            _db.close()
            if _row:
                client_id = _row['id']
        except Exception:
            pass

    service = _gmail_service(org_id)
    if not service:
        return jsonify({'success': False, 'error': 'Gmail no conectado. Ve a Emails → Conectar Gmail.'}), 503

    try:
        from services.mailer import send_email
        settings  = _get_settings(org_id)
        html_body = _build_html_email(body, settings)
        # send_email guarda siempre la copia en Enviados y la nota en el cliente
        gmail_id, _ = send_email(
            org_id, to_email, subject, html_body, text=body,
            client_id=int(client_id) if client_id else None,
            note=f"Email enviado: {subject}",
        )
        return jsonify({'success': True, 'id': gmail_id})

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
            'signature_phone', 'signature_email', 'signature_website',
            'logo_position', 'logo_align', 'logo_height']
    choices = {
        'logo_position': ('header', 'signature', 'both', 'none'),
        'logo_align': ('left', 'center', 'right'),
    }
    uploaded_url = ''
    f = request.files.get('logo_file')
    if f and f.filename:
        ext = os.path.splitext(f.filename)[1].lower()
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if ext not in _LOGO_EXTS:
            flash('Formato de logo no soportado. Usa PNG, JPG, GIF o WEBP (Gmail no muestra SVG).', 'error')
        elif size > 2 * 1024 * 1024:
            flash('El logo pesa más de 2 MB. Usa una imagen más liviana.', 'error')
        else:
            import uuid
            from flask import current_app
            folder = os.path.join(current_app.root_path, 'static', 'logos')
            os.makedirs(folder, exist_ok=True)
            fname = f'email_org{org_id}_{uuid.uuid4().hex[:8]}{ext}'
            f.save(os.path.join(folder, fname))
            uploaded_url = _abs_url(url_for('static', filename='logos/' + fname))
    db = get_db()
    for key in keys:
        value = request.form.get(key, '').strip()
        if key == 'company_logo_url' and uploaded_url:
            value = uploaded_url
        if key in choices and value not in choices[key]:
            value = choices[key][0] if key == 'logo_position' else 'center'
        if key == 'logo_height':
            try:
                value = str(max(24, min(200, int(value or 80))))
            except ValueError:
                value = '80'
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
    # Vista previa en vivo: el formulario manda los valores sin guardar
    for k in ('company_logo_url', 'logo_position', 'logo_align', 'logo_height',
              'signature_name', 'signature_title', 'signature_phone',
              'signature_email', 'signature_website'):
        if k in request.args:
            settings[k] = request.args.get(k, '')
    html = _build_html_email(
        'Hola {nombre},\n\nEste es un ejemplo de cómo se verán tus emails con la firma y el logo de la empresa.\n\n¡Saludos!',
        settings,
    )
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
