import os
import re
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from database import get_db

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

def find_client_by_phone(phone: str):
    db = get_db()
    clients = db.execute("SELECT * FROM clients WHERE phone IS NOT NULL AND phone != ''").fetchall()
    db.close()
    for c in clients:
        if phones_match(phone, c['phone']):
            return c
    return None


def save_message(phone, direction, message, wa_message_id=None, client_id=None,
                 status=None):
    if status is None:
        status = 'received' if direction == 'inbound' else 'sent'
    db = get_db()
    db.execute(
        """INSERT INTO whatsapp_messages
               (client_id, phone, direction, message, status, wa_message_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, phone, direction, message, status, wa_message_id),
    )
    db.commit()
    db.close()


def get_conversations():
    """Return one row per unique phone: last message + unread count."""
    db = get_db()
    rows = db.execute("""
        SELECT
            w.phone,
            w.message          AS last_message,
            w.direction        AS last_direction,
            w.created_at       AS last_at,
            w.client_id,
            c.first_name, c.last_name,
            COUNT(CASE WHEN w2.direction='inbound' AND w2.status='received' THEN 1 END) AS unread
        FROM whatsapp_messages w
        LEFT JOIN whatsapp_messages w2 ON w2.phone = w.phone
        LEFT JOIN clients c ON c.id = w.client_id
        WHERE w.id = (
            SELECT id FROM whatsapp_messages
            WHERE phone = w.phone ORDER BY created_at DESC LIMIT 1
        )
        GROUP BY w.phone
        ORDER BY w.created_at DESC
    """).fetchall()
    db.close()
    return rows


def get_conversation(phone: str):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM whatsapp_messages WHERE phone = ? ORDER BY created_at ASC",
        (phone,),
    ).fetchall()
    db.close()
    return rows


def mark_read(phone: str):
    db = get_db()
    db.execute(
        "UPDATE whatsapp_messages SET status='read' WHERE phone=? AND direction='inbound' AND status='received'",
        (phone,),
    )
    db.commit()
    db.close()


def get_unread_count() -> int:
    db = get_db()
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


# ── Routes ───────────────────────────────────────────────────────────────────

@whatsapp_bp.route('/whatsapp')
def index():
    conversations = get_conversations()
    active_phone  = request.args.get('phone', '').strip()
    active_msgs   = []
    active_client = None

    if active_phone:
        mark_read(active_phone)
        active_msgs   = get_conversation(active_phone)
        active_client = find_client_by_phone(active_phone)

    return render_template(
        'whatsapp/index.html',
        conversations=conversations,
        active_phone=active_phone,
        active_msgs=active_msgs,
        active_client=active_client,
    )


@whatsapp_bp.route('/webhook/whatsapp', methods=['POST'])
def webhook():
    """Receive inbound WhatsApp messages from Twilio."""
    raw_from = request.form.get('From', '')
    body      = request.form.get('Body', '').strip()
    wa_id     = request.form.get('MessageSid', '')

    phone  = normalize_phone(raw_from)
    client = find_client_by_phone(phone)

    save_message(
        phone=phone,
        direction='inbound',
        message=body,
        wa_message_id=wa_id,
        client_id=client['id'] if client else None,
    )

    # Empty TwiML — no auto-reply
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


@whatsapp_bp.route('/whatsapp/send', methods=['POST'])
def send_message():
    """Send a WhatsApp message from the CRM."""
    data      = request.get_json(silent=True) or {}
    phone     = data.get('phone', '').strip()
    message   = data.get('message', '').strip()
    client_id = data.get('client_id')     # optional: provided from client detail page

    if not phone or not message:
        return jsonify({'success': False, 'error': 'phone and message required'}), 400

    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token  = os.getenv('TWILIO_AUTH_TOKEN')
    from_number = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

    if not account_sid or not auth_token:
        return jsonify({'success': False, 'error': 'Twilio credentials not configured'}), 503

    try:
        from twilio.rest import Client as TwilioClient
        tw = TwilioClient(account_sid, auth_token)
        msg = tw.messages.create(
            from_=from_number,
            to=f'whatsapp:{phone}',
            body=message,
        )

        # Resolve client_id from phone if not provided explicitly
        resolved_client_id = client_id
        if not resolved_client_id:
            matched = find_client_by_phone(phone)
            resolved_client_id = matched['id'] if matched else None

        save_message(
            phone=phone,
            direction='outbound',
            message=message,
            wa_message_id=msg.sid,
            client_id=resolved_client_id,
            status='sent',
        )

        # Register in client activity history when client_id is known
        if resolved_client_id:
            try:
                save_client_note(int(resolved_client_id), message)
            except Exception:
                pass  # note failure must not break the send response

        return jsonify({'success': True, 'sid': msg.sid})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@whatsapp_bp.route('/whatsapp/conversation/<path:phone>')
def conversation_json(phone):
    """Return conversation as JSON for polling."""
    phone = normalize_phone(phone)
    mark_read(phone)
    msgs   = get_conversation(phone)
    client = find_client_by_phone(phone)
    return jsonify({
        'messages': messages_to_dicts(msgs),
        'client': {
            'id':         client['id'],
            'first_name': client['first_name'],
            'last_name':  client['last_name'],
        } if client else None,
    })


@whatsapp_bp.route('/whatsapp/new-conversation', methods=['GET', 'POST'])
def new_conversation():
    """Start a conversation with any phone number."""
    if request.method == 'POST':
        phone   = normalize_phone(request.form.get('phone', ''))
        message = request.form.get('message', '').strip()
        if not phone:
            flash('Número de teléfono requerido.', 'danger')
            return redirect(url_for('whatsapp.new_conversation'))

        if message:
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token  = os.getenv('TWILIO_AUTH_TOKEN')
            from_number = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

            if account_sid and auth_token:
                try:
                    from twilio.rest import Client as TwilioClient
                    tw = TwilioClient(account_sid, auth_token)
                    msg = tw.messages.create(
                        from_=from_number,
                        to=f'whatsapp:{phone}',
                        body=message,
                    )
                    client = find_client_by_phone(phone)
                    save_message(
                        phone=phone, direction='outbound', message=message,
                        wa_message_id=msg.sid,
                        client_id=client['id'] if client else None,
                        status='sent',
                    )
                except Exception as exc:
                    flash(f'Error al enviar: {exc}', 'danger')
                    return redirect(url_for('whatsapp.new_conversation'))
            else:
                flash('Credenciales de Twilio no configuradas.', 'danger')
                return redirect(url_for('whatsapp.new_conversation'))

        return redirect(url_for('whatsapp.index', phone=phone))

    return render_template('whatsapp/new_conversation.html')
