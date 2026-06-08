"""
routes/meta_webhook.py
Webhook de entrada para Messenger e Instagram Direct.
- GET  /webhook/meta  → verificación del hub (Meta llama esto 1 vez al registrar)
- POST /webhook/meta  → mensajes entrantes en tiempo real
- POST /meta/send     → envío saliente desde el CRM (llamado por el agente)
"""
import os
import hmac
import hashlib
from flask import Blueprint, request, jsonify, session, g
from database import get_db
from services.meta_send import send_message

meta_webhook_bp = Blueprint('meta_webhook', __name__)


# ── Signature verification ────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    secret = os.getenv('META_APP_SECRET', '')
    if not secret or not signature.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_page_token(page_id: str, org_id: int = 1) -> str | None:
    db  = get_db()
    row = db.execute(
        "SELECT page_access_token FROM meta_channel_connections "
        "WHERE page_id=? AND org_id=? AND is_active=1",
        (page_id, org_id),
    ).fetchone()
    db.close()
    return row['page_access_token'] if row else None


def _find_client_by_sender(sender_id: str) -> int | None:
    db  = get_db()
    row = db.execute(
        "SELECT client_id FROM meta_messages "
        "WHERE sender_id=? AND direction='inbound' AND client_id IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (sender_id,),
    ).fetchone()
    db.close()
    return row['client_id'] if row else None


def _save_message(channel, sender_id, page_id, direction, text,
                  meta_msg_id=None, client_id=None, org_id=1):
    db = get_db()
    db.execute("""
        INSERT INTO meta_messages
            (org_id, channel, sender_id, page_id, direction, message, meta_message_id, client_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (org_id, channel, sender_id, page_id, direction, text, meta_msg_id, client_id))
    db.commit()
    db.close()


def _get_unread_count(org_id: int = 1) -> int:
    db    = get_db()
    count = db.execute(
        "SELECT COUNT(*) FROM meta_messages "
        "WHERE org_id=? AND direction='inbound' AND status='received'",
        (org_id,),
    ).fetchone()[0]
    db.close()
    return count


# ── Webhook verification (GET) ────────────────────────────────────────────────

@meta_webhook_bp.route('/webhook/meta', methods=['GET'])
def verify():
    mode      = request.args.get('hub.mode', '')
    token     = request.args.get('hub.verify_token', '')
    challenge = request.args.get('hub.challenge', '')

    if mode == 'subscribe' and token == os.getenv('META_VERIFY_TOKEN', ''):
        return challenge, 200
    return 'Forbidden', 403


# ── Incoming messages (POST) ──────────────────────────────────────────────────

@meta_webhook_bp.route('/webhook/meta', methods=['POST'])
def receive():
    payload   = request.get_data()
    signature = request.headers.get('X-Hub-Signature-256', '')

    if not _verify_signature(payload, signature):
        return 'Invalid signature', 403

    data = request.get_json(silent=True) or {}

    for entry in data.get('entry', []):
        page_id = entry.get('id', '')

        # ── Messenger ──
        for event in entry.get('messaging', []):
            sender_id = event.get('sender', {}).get('id', '')
            msg       = event.get('message', {})
            text      = msg.get('text', '')
            msg_id    = msg.get('mid', '')
            if not sender_id or not text:
                continue
            client_id = _find_client_by_sender(sender_id)
            _save_message('messenger', sender_id, page_id, 'inbound', text, msg_id, client_id)

        # ── Instagram ──
        for ig in entry.get('instagram', []):
            for event in ig.get('messaging', []):
                sender_id = event.get('sender', {}).get('id', '')
                msg       = event.get('message', {})
                text      = msg.get('text', '')
                msg_id    = msg.get('mid', '')
                if not sender_id or not text:
                    continue
                client_id = _find_client_by_sender(sender_id)
                _save_message('instagram', sender_id, page_id, 'inbound', text, msg_id, client_id)

    return jsonify({'status': 'ok'})


# ── Send outbound message ─────────────────────────────────────────────────────

@meta_webhook_bp.route('/meta/send', methods=['POST'])
def send():
    data        = request.get_json(silent=True) or {}
    sender_id   = (data.get('sender_id')  or '').strip()
    text        = (data.get('message')    or '').strip()
    page_id     = (data.get('page_id')    or '').strip()
    client_id   = data.get('client_id')
    channel     = (data.get('channel')    or 'messenger').strip()

    if not sender_id or not text or not page_id:
        return jsonify({'success': False, 'error': 'sender_id, page_id y message son requeridos'}), 400

    org_id = getattr(g, 'org_id', None) or session.get('org_id', 1)
    token  = _get_page_token(page_id, org_id)

    if not token:
        return jsonify({'success': False, 'error': 'Página no conectada. Agrega el token en Configuración.'}), 503

    try:
        result = send_message(token, sender_id, text)
        meta_msg_id = result.get('message_id')

        _save_message(channel, sender_id, page_id, 'outbound', text, meta_msg_id, client_id, org_id)

        # Register in client activity notes
        if client_id:
            db = get_db()
            db.execute(
                "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
                (int(client_id), f"Mensaje enviado por {channel.capitalize()}: {text[:80]}"),
            )
            db.commit()
            db.close()

        return jsonify({'success': True, 'meta_message_id': meta_msg_id})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


# ── Conversations JSON (for future panel) ─────────────────────────────────────

@meta_webhook_bp.route('/meta/conversations')
def conversations():
    org_id = getattr(g, 'org_id', None) or session.get('org_id', 1)
    db     = get_db()
    rows   = db.execute("""
        SELECT
            m.sender_id, m.channel, m.page_id,
            m.message   AS last_message,
            m.direction AS last_direction,
            m.created_at AS last_at,
            m.client_id,
            c.first_name, c.last_name,
            COUNT(CASE WHEN m2.direction='inbound' AND m2.status='received' THEN 1 END) AS unread
        FROM meta_messages m
        LEFT JOIN meta_messages m2 ON m2.sender_id = m.sender_id
        LEFT JOIN clients c ON c.id = m.client_id
        WHERE m.id = (
            SELECT id FROM meta_messages
            WHERE sender_id = m.sender_id ORDER BY created_at DESC LIMIT 1
        )
          AND m.org_id = ?
        GROUP BY m.sender_id
        ORDER BY m.created_at DESC
    """, (org_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@meta_webhook_bp.route('/meta/conversation/<sender_id>')
def conversation(sender_id):
    org_id = getattr(g, 'org_id', None) or session.get('org_id', 1)
    db     = get_db()
    rows   = db.execute(
        "SELECT * FROM meta_messages WHERE sender_id=? AND org_id=? ORDER BY created_at ASC",
        (sender_id, org_id),
    ).fetchall()
    # Mark as read
    db.execute(
        "UPDATE meta_messages SET status='read' "
        "WHERE sender_id=? AND org_id=? AND direction='inbound' AND status='received'",
        (sender_id, org_id),
    )
    db.commit()
    db.close()
    return jsonify([dict(r) for r in rows])
