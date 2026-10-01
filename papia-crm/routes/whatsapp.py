import json
import logging
import os
import re
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash, g
from database import get_db
from services import zernio

log = logging.getLogger(__name__)

whatsapp_bp = Blueprint('whatsapp', __name__)

# ── Phone normalization ──────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """Strip whatsapp: prefix and whitespace. Keep leading +."""
    phone = raw.strip().replace('whatsapp:', '').strip()
    return phone


def phones_match(p1: str, p2: str) -> bool:
    """Compare only digits; handle stored numbers with spaces/dashes."""
    d1 = re.sub(r'\D', '', p1)
    d2 = re.sub(r'\D', '', p2)
    # Compare last 10 digits to avoid country-code mismatches
    return d1[-10:] == d2[-10:] if len(d1) >= 10 and len(d2) >= 10 else d1 == d2


# ── DB helpers ───────────────────────────────────────────────────────────────

def find_client_by_phone(phone: str, org_id=1):
    db = get_db()
    clients = db.execute(
        "SELECT * FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
        (org_id,)
    ).fetchall()
    db.close()
    for c in clients:
        if phones_match(phone, c['phone']):
            return c
    return None


def _split_profile_name(profile_name: str, phone: str):
    """Turn Twilio's ProfileName into (first_name, last_name)."""
    name = (profile_name or '').strip()
    if not name:
        return 'WhatsApp', phone
    parts = name.split(' ', 1)
    return parts[0], (parts[1] if len(parts) > 1 else '')


def get_or_create_client_from_whatsapp(phone: str, profile_name: str = '',
                                       first_message: str = '', org_id: int = 1):
    """
    Find the client for this phone or create it as a new lead in the pipeline.

    Runs inside a BEGIN IMMEDIATE transaction so two messages arriving at the
    same time from an unknown number can't create duplicate leads.
    Returns (client_id, created).
    """
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT id, phone FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
            (org_id,)
        ).fetchall()
        for r in rows:
            if phones_match(phone, r['phone']):
                db.execute("COMMIT")
                return r['id'], False

        first_name, last_name = _split_profile_name(profile_name, phone)
        cur = db.execute("""
            INSERT INTO clients
                (first_name, last_name, email, phone, company, project_type,
                 project_details, pipeline_stage, source, total_cost, amount_paid, org_id)
            VALUES (?, ?, '', ?, '', 'other', ?, 'new_lead', 'whatsapp', 0, 0, ?)
        """, (first_name, last_name, phone, first_message[:500], org_id))
        client_id = cur.lastrowid

        note = 'Lead creado automáticamente desde WhatsApp'
        if first_message:
            note += f' | Primer mensaje: {first_message[:300]}'
        db.execute(
            "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'whatsapp_lead', ?)",
            (client_id, note),
        )

        # Link any earlier orphan messages from this number to the new client
        db.execute(
            "UPDATE whatsapp_messages SET client_id=? WHERE phone=? AND org_id=? AND client_id IS NULL",
            (client_id, phone, org_id),
        )
        db.execute("COMMIT")
        return client_id, True
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        db.close()


def _resolve_client_id(phone: str, name: str = '', first_message: str = '', org_id: int = 1):
    """Existing client id, or a new lead auto-created in the pipeline. Never raises."""
    try:
        client_id, _created = get_or_create_client_from_whatsapp(
            phone, profile_name=name, first_message=first_message, org_id=org_id)
        return client_id
    except Exception:
        log.exception('WhatsApp: no se pudo auto-crear el lead para %s', phone)
        client = find_client_by_phone(phone, org_id)
        return client['id'] if client else None


def save_message(phone, direction, message, wa_message_id=None, client_id=None,
                 status=None, org_id=1):
    if status is None:
        status = 'received' if direction == 'inbound' else 'sent'
    db = get_db()
    db.execute(
        """INSERT INTO whatsapp_messages
               (client_id, phone, direction, message, status, wa_message_id, org_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (client_id, phone, direction, message, status, wa_message_id, org_id),
    )
    db.commit()
    db.close()


def get_conversations(org_id=1):
    """Return one row per unique phone: last message + unread count."""
    db = get_db()
    rows = db.execute("""
        SELECT
            w.phone,
            w.message          AS last_message,
            w.direction        AS last_direction,
            w.status           AS last_status,
            w.created_at       AS last_at,
            w.client_id,
            c.first_name, c.last_name,
            COUNT(CASE WHEN w2.direction='inbound' AND w2.status='received' THEN 1 END) AS unread
        FROM whatsapp_messages w
        LEFT JOIN whatsapp_messages w2 ON w2.phone = w.phone
        LEFT JOIN clients c ON c.id = w.client_id
        WHERE w.org_id = ?
          AND w.id = (
            SELECT id FROM whatsapp_messages
            WHERE phone = w.phone AND org_id = ?
            ORDER BY created_at DESC LIMIT 1
          )
        GROUP BY w.phone
        ORDER BY w.created_at DESC
    """, (org_id, org_id)).fetchall()
    db.close()
    return rows


def get_conversation(phone: str, org_id=1):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM whatsapp_messages WHERE phone = ? AND org_id = ? ORDER BY created_at ASC",
        (phone, org_id),
    ).fetchall()
    db.close()
    return rows


def mark_read(phone: str, org_id=1):
    db = get_db()
    db.execute(
        "UPDATE whatsapp_messages SET status='read' WHERE phone=? AND org_id=? AND direction='inbound' AND status='received'",
        (phone, org_id),
    )
    db.commit()
    db.close()


def get_unread_count(org_id=None) -> int:
    db = get_db()
    if org_id is not None:
        count = db.execute(
            "SELECT COUNT(*) FROM whatsapp_messages WHERE direction='inbound' AND status='received' AND org_id=?",
            (org_id,)
        ).fetchone()[0]
    else:
        count = db.execute(
            "SELECT COUNT(*) FROM whatsapp_messages WHERE direction='inbound' AND status='received'"
        ).fetchone()[0]
    db.close()
    return count


def save_client_note(client_id: int, content: str):
    """Registers a WhatsApp message in the client's activity history."""
    db = get_db()
    db.execute(
        "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'whatsapp', ?)",
        (client_id, content),
    )
    db.commit()
    db.close()


def messages_to_dicts(rows):
    return [
        {
            'id':           r['id'],
            'phone':        r['phone'],
            'direction':    r['direction'],
            'message':      r['message'],
            'status':       r['status'],
            'wa_message_id': r['wa_message_id'],
            'created_at':   r['created_at'],
        }
        for r in rows
    ]


def _get_org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


# ── Proveedor de envío (Zernio por defecto, Twilio como respaldo) ────────────

def _provider() -> str:
    forced = os.getenv('WHATSAPP_PROVIDER', '').strip().lower()
    if forced in ('zernio', 'twilio'):
        return forced
    return 'zernio' if zernio.is_configured() else 'twilio'


def _get_conv_id(phone: str, org_id=1):
    db = get_db()
    row = db.execute(
        "SELECT zernio_conversation_id FROM whatsapp_conversations WHERE org_id=? AND phone=?",
        (org_id, zernio.to_e164(phone)),
    ).fetchone()
    db.close()
    return row['zernio_conversation_id'] if row else None


def _save_conv_id(phone: str, conversation_id: str, org_id=1, inbound=False):
    if not conversation_id:
        return
    db = get_db()
    db.execute(
        """INSERT INTO whatsapp_conversations (org_id, phone, zernio_conversation_id, last_inbound_at)
               VALUES (?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP END)
           ON CONFLICT(org_id, phone) DO UPDATE SET
               zernio_conversation_id = excluded.zernio_conversation_id,
               last_inbound_at = COALESCE(excluded.last_inbound_at, whatsapp_conversations.last_inbound_at),
               updated_at = CURRENT_TIMESTAMP""",
        (org_id, zernio.to_e164(phone), conversation_id, 1 if inbound else 0),
    )
    db.commit()
    db.close()


def _message_exists(wa_message_id: str) -> bool:
    if not wa_message_id:
        return False
    db = get_db()
    row = db.execute("SELECT 1 FROM whatsapp_messages WHERE wa_message_id=? LIMIT 1",
                     (wa_message_id,)).fetchone()
    db.close()
    return row is not None


def _update_status(wa_message_id: str, status: str):
    if not wa_message_id:
        return
    rank = {'sent': 1, 'delivered': 2, 'read': 3, 'failed': 9}
    db = get_db()
    row = db.execute("SELECT id, status FROM whatsapp_messages WHERE wa_message_id=? AND direction='outbound'",
                     (wa_message_id,)).fetchone()
    # No retroceder (un 'delivered' tardío no pisa un 'read')
    if row and rank.get(status, 0) > rank.get(row['status'], 0):
        db.execute("UPDATE whatsapp_messages SET status=? WHERE id=?", (status, row['id']))
        db.commit()
    db.close()


def deliver(phone: str, message: str, org_id=1, template=None) -> str:
    """
    Envía un WhatsApp y devuelve el id del mensaje.
    template = {'name': ..., 'language': 'es', 'params': [...]} para primer contacto.
    Lanza zernio.TemplateRequired si no hay ventana abierta y no se pasó plantilla.
    """
    phone = zernio.to_e164(phone)

    if _provider() == 'zernio':
        if template and template.get('name'):
            data = zernio.start_with_template(phone, template['name'],
                                              template.get('language') or 'es',
                                              template.get('params'))
            _save_conv_id(phone, data.get('conversationId'), org_id)
            return data.get('messageId', '')

        conv_id = _get_conv_id(phone, org_id) or zernio.find_conversation_id(phone)
        if not conv_id:
            raise zernio.TemplateRequired(
                'Este contacto no ha escrito todavía. WhatsApp exige una plantilla aprobada para el primer mensaje.',
                code='TEMPLATE_REQUIRED')
        _save_conv_id(phone, conv_id, org_id)
        data = zernio.send_text(conv_id, message)
        return data.get('messageId', '')

    # ── Twilio (legacy) ──
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token  = os.getenv('TWILIO_AUTH_TOKEN')
    from_number = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    if not account_sid or not auth_token:
        raise RuntimeError('No hay proveedor de WhatsApp configurado (Zernio ni Twilio).')
    from twilio.rest import Client as TwilioClient
    msg = TwilioClient(account_sid, auth_token).messages.create(
        from_=from_number, to=f'whatsapp:{phone}', body=message)
    return msg.sid


def _record_outbound(phone, message, wa_id, org_id, client_id=None):
    resolved = client_id
    if not resolved:
        matched = find_client_by_phone(phone, org_id)
        resolved = matched['id'] if matched else None
    save_message(phone=zernio.to_e164(phone), direction='outbound', message=message,
                 wa_message_id=wa_id, client_id=resolved, status='sent', org_id=org_id)
    if resolved:
        try:
            save_client_note(int(resolved), message)
        except Exception:
            pass
    return resolved


# ── Routes ───────────────────────────────────────────────────────────────────

@whatsapp_bp.route('/whatsapp')
def index():
    org_id        = _get_org_id()
    conversations = get_conversations(org_id)
    active_phone  = request.args.get('phone', '').strip()
    active_msgs   = []
    active_client = None

    if active_phone:
        mark_read(active_phone, org_id)
        active_msgs   = get_conversation(active_phone, org_id)
        active_client = find_client_by_phone(active_phone, org_id)

    return render_template(
        'whatsapp/index.html',
        conversations=conversations,
        conversations_data=conversations_to_dicts(conversations, org_id),
        active_phone=active_phone,
        active_msgs=active_msgs,
        active_msgs_data=messages_to_dicts(active_msgs),
        active_client=active_client,
    )


def _clients_by_digits(org_id=1):
    """Mapa últimos-10-dígitos → cliente, para nombrar chats cuyo mensaje no quedó enlazado."""
    db = get_db()
    rows = db.execute(
        "SELECT id, first_name, last_name, phone FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
        (org_id,)).fetchall()
    db.close()
    out = {}
    for c in rows:
        d = re.sub(r'\D', '', c['phone'] or '')
        if len(d) >= 7:
            out[d[-10:]] = c
    return out


def conversations_to_dicts(rows, org_id=1):
    clients = _clients_by_digits(org_id)
    out = []
    for r in rows:
        client_id, first, last = r['client_id'], r['first_name'], r['last_name']
        if not first:
            c = clients.get(re.sub(r'\D', '', r['phone'] or '')[-10:])
            if c:
                client_id, first, last = c['id'], c['first_name'], c['last_name']
        name = ' '.join(x for x in (first, last) if x) if first else ''
        out.append({
            'phone':      r['phone'],
            'name':       name,
            'client_id':  client_id,
            'last_message':   r['last_message'],
            'last_direction': r['last_direction'],
            'last_status':    r['last_status'],
            'last_at':    r['last_at'],
            'unread':     r['unread'] or 0,
        })
    return out


@whatsapp_bp.route('/whatsapp/conversations.json')
def conversations_json():
    """Lista de chats para refrescar el panel izquierdo sin recargar."""
    org_id = _get_org_id()
    return jsonify({'conversations': conversations_to_dicts(get_conversations(org_id), org_id)})


@whatsapp_bp.route('/webhook/whatsapp', methods=['POST'])
def webhook():
    """Legacy: mensajes entrantes desde Twilio."""
    raw_from = request.form.get('From', '')
    body      = request.form.get('Body', '').strip()
    wa_id     = request.form.get('MessageSid', '')

    profile   = request.form.get('ProfileName', '').strip()

    phone  = normalize_phone(raw_from)
    org_id = 1
    if not phone or _message_exists(wa_id):   # Twilio reintenta webhooks
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

    client_id = _resolve_client_id(phone, profile, body, org_id)
    save_message(phone=phone, direction='inbound', message=body or '[mensaje sin texto]',
                 wa_message_id=wa_id, client_id=client_id, org_id=org_id)
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


@whatsapp_bp.route('/webhook/zernio', methods=['POST'])
def zernio_webhook():
    """Eventos de Zernio: message.received / sent / delivered / read / failed."""
    raw = request.get_data()
    if not zernio.verify_signature(raw, request.headers.get('X-Zernio-Signature', '')):
        log.warning('Zernio webhook: firma inválida')
        return jsonify({'error': 'invalid signature'}), 401

    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        return jsonify({'error': 'invalid json'}), 400

    event   = payload.get('event') or request.headers.get('X-Zernio-Event', '')
    msg     = payload.get('message') or {}
    conv    = payload.get('conversation') or {}
    account = payload.get('account') or {}
    platform = msg.get('platform') or account.get('platform')

    if event == 'webhook.test':
        return jsonify({'ok': True})
    if platform and platform != 'whatsapp':
        return jsonify({'ignored': platform})   # IG/Messenger siguen por Meta directo

    org_id = 1  # PapiaTech (single-tenant para este número)
    wa_id  = msg.get('platformMessageId') or msg.get('id')

    try:
        if event == 'message.received':
            sender = msg.get('sender') or {}
            phone = zernio.to_e164(sender.get('phoneNumber') or conv.get('participantId') or sender.get('id'))
            if not phone or _message_exists(wa_id):
                return jsonify({'ok': True})
            text = msg.get('text') or ''
            if not text and msg.get('attachments'):
                kinds = ', '.join(a.get('type', 'archivo') for a in msg['attachments'])
                text = f'[{kinds}]'
            _save_conv_id(phone, conv.get('id') or msg.get('conversationId'), org_id, inbound=True)
            name = (sender.get('name') or sender.get('displayName')
                    or conv.get('participantName') or conv.get('name') or '')
            # Número desconocido → se crea como cliente en "Nuevo Lead" (pipeline)
            client_id = _resolve_client_id(phone, name, text, org_id)
            save_message(phone=phone, direction='inbound', message=text or '[mensaje]',
                         wa_message_id=wa_id, client_id=client_id,
                         org_id=org_id)

        elif event == 'message.sent':
            # Enviado desde el celular (coexistence) o desde el panel de Zernio
            phone = zernio.to_e164(conv.get('participantId') or '')
            if phone and not _message_exists(wa_id):
                _save_conv_id(phone, conv.get('id'), org_id)
                _record_outbound(phone, msg.get('text') or '[mensaje]', wa_id, org_id)

        elif event in ('message.delivered', 'message.read', 'message.failed'):
            _update_status(wa_id, event.split('.', 1)[1])
            if event == 'message.failed':
                log.warning('Zernio message.failed: %s', payload.get('error'))
    except Exception:
        log.exception('Error procesando webhook de Zernio')
        # 200 igual: el error es nuestro, reintentar no lo arregla
    return jsonify({'ok': True})


@whatsapp_bp.route('/whatsapp/send', methods=['POST'])
def send_message():
    """Enviar WhatsApp desde el CRM."""
    org_id    = _get_org_id()
    data      = request.get_json(silent=True) or {}
    phone     = data.get('phone', '').strip()
    message   = data.get('message', '').strip()
    client_id = data.get('client_id')
    template  = data.get('template')  # opcional {'name','language','params'}

    if not phone or (not message and not template):
        return jsonify({'success': False, 'error': 'phone and message required'}), 400

    try:
        wa_id = deliver(phone, message, org_id, template=template)
        shown = message or f"[plantilla: {template.get('name')}]"
        _record_outbound(phone, shown, wa_id, org_id, client_id)
        return jsonify({'success': True, 'sid': wa_id})
    except zernio.TemplateRequired as exc:
        return jsonify({'success': False, 'template_required': True, 'error': str(exc)}), 409
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@whatsapp_bp.route('/whatsapp/conversation/<path:phone>')
def conversation_json(phone):
    """Return conversation as JSON for polling."""
    org_id = _get_org_id()
    phone = normalize_phone(phone)
    mark_read(phone, org_id)
    msgs   = get_conversation(phone, org_id)
    client = find_client_by_phone(phone, org_id)
    return jsonify({
        'messages': messages_to_dicts(msgs),
        'client': {
            'id':         client['id'],
            'first_name': client['first_name'],
            'last_name':  client['last_name'],
        } if client else None,
    })


@whatsapp_bp.route('/whatsapp/templates.json')
def templates_json():
    """Plantillas aprobadas en Zernio/Meta, para el formulario de nueva conversación."""
    if _provider() != 'zernio':
        return jsonify({'templates': []})
    try:
        data = zernio.list_templates()
        items = data.get('templates') or data.get('data') or []
        out = [{'name': t.get('name'), 'language': t.get('language'),
                'status': t.get('status'), 'category': t.get('category')}
               for t in items if str(t.get('status', '')).upper() == 'APPROVED']
        return jsonify({'templates': out})
    except Exception as exc:
        return jsonify({'templates': [], 'error': str(exc)})


@whatsapp_bp.route('/whatsapp/status')
def provider_status():
    """Diagnóstico rápido: proveedor activo y estado del número en Meta."""
    info = {'provider': _provider(), 'zernio_configured': zernio.is_configured()}
    if _provider() == 'zernio':
        try:
            info['number'] = zernio.number_info()
        except Exception as exc:
            info['number_error'] = str(exc)
    return jsonify(info)


@whatsapp_bp.route('/whatsapp/new-conversation', methods=['GET', 'POST'])
def new_conversation():
    """Iniciar conversación con cualquier número."""
    org_id = _get_org_id()
    if request.method == 'POST':
        phone    = zernio.to_e164(normalize_phone(request.form.get('phone', '')))
        message  = request.form.get('message', '').strip()
        tpl_name = request.form.get('template_name', '').strip()
        tpl_lang = request.form.get('template_language', '').strip() or 'es'
        tpl_params = [p.strip() for p in request.form.get('template_params', '').split('|') if p.strip()]

        if not phone:
            flash('Número de teléfono requerido.', 'danger')
            return redirect(url_for('whatsapp.new_conversation'))

        if message or tpl_name:
            template = {'name': tpl_name, 'language': tpl_lang, 'params': tpl_params} if tpl_name else None
            try:
                wa_id = deliver(phone, message, org_id, template=template)
                _record_outbound(phone, message if not template else f'[plantilla: {tpl_name}]',
                                 wa_id, org_id)
            except zernio.TemplateRequired as exc:
                flash(f'{exc} Elige una plantilla aprobada abajo.', 'danger')
                return redirect(url_for('whatsapp.new_conversation', phone=phone, need_template=1))
            except Exception as exc:
                flash(f'Error al enviar: {exc}', 'danger')
                return redirect(url_for('whatsapp.new_conversation', phone=phone))

        return redirect(url_for('whatsapp.index', phone=phone))

    return render_template('whatsapp/new_conversation.html')
