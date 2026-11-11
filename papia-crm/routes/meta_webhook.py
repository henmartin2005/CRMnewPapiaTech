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
from flask import Blueprint, request, jsonify, session, g, render_template, abort
from database import get_db
from services.meta_send import send_message, GRAPH, GRAPH_IG
import requests

meta_webhook_bp = Blueprint('meta_webhook', __name__)


# ── Signature verification ────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    # Messenger firma con el secreto de la app de Facebook;
    # Instagram Login firma con el secreto de la app de Instagram.
    if not signature.startswith('sha256='):
        return False
    for var in ('META_APP_SECRET', 'IG_APP_SECRET'):
        secret = os.getenv(var, '')
        if not secret:
            continue
        expected = 'sha256=' + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True
    return False


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_page_token(page_id: str, channel: str, org_id: int = 1) -> str | None:
    db  = get_db()
    row = db.execute(
        "SELECT page_access_token FROM meta_channel_connections "
        "WHERE page_id=? AND channel=? AND org_id=? AND is_active=1",
        (page_id, channel, org_id),
    ).fetchone()
    db.close()
    return row['page_access_token'] if row else None


def _get_connection_org_id(page_id: str, channel: str) -> int:
    db = get_db()
    row = db.execute(
        "SELECT org_id FROM meta_channel_connections "
        "WHERE page_id=? AND channel=? AND is_active=1 "
        "ORDER BY id DESC LIMIT 1",
        (page_id, channel),
    ).fetchone()
    db.close()
    return row['org_id'] if row else 1


def _find_client_by_sender(sender_id: str, channel: str, org_id: int = 1) -> int | None:
    db  = get_db()
    row = db.execute(
        "SELECT client_id FROM meta_messages "
        "WHERE sender_id=? AND channel=? AND org_id=? AND direction='inbound' AND client_id IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (sender_id, channel, org_id),
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


# ── Perfiles de contactos (username / nombre / foto) ─────────────────────────

def _ensure_contacts_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta_contacts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id      INTEGER NOT NULL DEFAULT 1,
            channel     TEXT NOT NULL,
            page_id     TEXT NOT NULL,
            sender_id   TEXT NOT NULL,
            username    TEXT,
            name        TEXT,
            profile_pic TEXT,
            fetched_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(channel, page_id, sender_id)
        )
    """)


def _fetch_profile(channel: str, sender_id: str, token: str) -> dict:
    """Pide a Meta el perfil de quien escribe (IGSID / PSID)."""
    try:
        if channel == 'instagram':
            base = GRAPH_IG if (token or '').startswith('IG') else GRAPH
            fields = 'name,username,profile_pic'
        else:
            base = GRAPH
            fields = 'first_name,last_name,name,profile_pic'
        resp = requests.get(
            f"{base}/{sender_id}",
            params={'fields': fields, 'access_token': token},
            timeout=8,
        )
        if not resp.ok:
            print(f'[meta_contacts] perfil {channel} {sender_id} -> {resp.status_code} {resp.text[:300]}', flush=True)
            return {}
        d = resp.json() or {}
        name = d.get('name') or ' '.join(x for x in (d.get('first_name'), d.get('last_name')) if x)
        return {
            'username': d.get('username') or None,
            'name': name or None,
            'profile_pic': d.get('profile_pic') or None,
        }
    except Exception as exc:
        print(f'[meta_contacts] error perfil {channel} {sender_id}: {exc!r}', flush=True)
        return {}


def _ensure_contact_profile(org_id, channel, page_id, sender_id, force=False):
    db = get_db()
    try:
        _ensure_contacts_table(db)
        row = db.execute(
            "SELECT id FROM meta_contacts WHERE channel=? AND page_id=? AND sender_id=?",
            (channel, page_id, sender_id),
        ).fetchone()
        if row and not force:
            return
        tok = db.execute(
            "SELECT page_access_token FROM meta_channel_connections "
            "WHERE page_id=? AND channel=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (page_id, channel),
        ).fetchone()
        token = tok['page_access_token'] if tok else None
        if not token:
            print(f'[meta_contacts] sin token para {channel} {page_id}', flush=True)
        prof = _fetch_profile(channel, sender_id, token) if token else {}
        db.execute("""
            INSERT INTO meta_contacts
                (org_id, channel, page_id, sender_id, username, name, profile_pic, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(channel, page_id, sender_id) DO UPDATE SET
                username    = COALESCE(excluded.username, meta_contacts.username),
                name        = COALESCE(excluded.name, meta_contacts.name),
                profile_pic = COALESCE(excluded.profile_pic, meta_contacts.profile_pic),
                fetched_at  = CURRENT_TIMESTAMP
        """, (org_id, channel, page_id, sender_id,
              prof.get('username'), prof.get('name'), prof.get('profile_pic')))
        db.commit()
    finally:
        db.close()


def _backfill_profiles(conversations, org_id, channel, force=False, limit=8):
    pending = [c for c in conversations
               if not c['username'] and not c['contact_name']
               and (force or not c['profile_fetched_at'])][:limit]
    for c in pending:
        try:
            _ensure_contact_profile(org_id, channel, c['page_id'], c['sender_id'], force=True)
        except Exception:
            pass
    return bool(pending)


def _get_accounts(org_id: int, channel: str):
    db = get_db()
    rows = db.execute(
        "SELECT page_id, page_name FROM meta_channel_connections "
        "WHERE org_id=? AND channel=? AND is_active=1 ORDER BY id",
        (org_id, channel),
    ).fetchall()
    db.close()
    return rows


def get_meta_unread_count(org_id: int = 1, channel: str | None = None) -> int:
    db    = get_db()
    if channel:
        count = db.execute(
            "SELECT COUNT(*) FROM meta_messages "
            "WHERE org_id=? AND channel=? AND direction='inbound' AND status='received'",
            (org_id, channel),
        ).fetchone()[0]
    else:
        count = db.execute(
            "SELECT COUNT(*) FROM meta_messages "
            "WHERE org_id=? AND direction='inbound' AND status='received'",
            (org_id,),
        ).fetchone()[0]
    db.close()
    return count


def _get_org_id():
    return getattr(g, 'org_id', None) or session.get('org_id', 1)


def _get_conversations(org_id: int, channel: str):
    db = get_db()
    _ensure_contacts_table(db)
    rows = db.execute("""
        SELECT
            m.sender_id, m.channel, m.page_id,
            m.message    AS last_message,
            m.direction  AS last_direction,
            m.created_at AS last_at,
            m.client_id,
            c.first_name, c.last_name,
            conn.page_name,
            mc.username,
            mc.name       AS contact_name,
            mc.profile_pic,
            mc.fetched_at AS profile_fetched_at,
            COUNT(CASE WHEN m2.direction='inbound' AND m2.status='received' THEN 1 END) AS unread
        FROM meta_messages m
        LEFT JOIN meta_messages m2
          ON m2.sender_id = m.sender_id
         AND m2.channel = m.channel
         AND m2.org_id = m.org_id
        LEFT JOIN clients c ON c.id = m.client_id
        LEFT JOIN meta_channel_connections conn
          ON conn.org_id = m.org_id
         AND conn.channel = m.channel
         AND conn.page_id = m.page_id
        LEFT JOIN meta_contacts mc
          ON mc.channel = m.channel
         AND mc.page_id = m.page_id
         AND mc.sender_id = m.sender_id
        WHERE m.org_id = ?
          AND m.channel = ?
          AND m.id = (
            SELECT id FROM meta_messages
            WHERE sender_id = m.sender_id
              AND channel = m.channel
              AND org_id = m.org_id
            ORDER BY created_at DESC LIMIT 1
          )
        GROUP BY m.sender_id, m.channel
        ORDER BY m.created_at DESC
    """, (org_id, channel)).fetchall()
    db.close()
    return rows


def _get_conversation(org_id: int, channel: str, sender_id: str):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM meta_messages WHERE sender_id=? AND channel=? AND org_id=? ORDER BY created_at ASC",
        (sender_id, channel, org_id),
    ).fetchall()
    db.close()
    return rows


def _mark_read(org_id: int, channel: str, sender_id: str):
    db = get_db()
    db.execute(
        "UPDATE meta_messages SET status='read' "
        "WHERE sender_id=? AND channel=? AND org_id=? AND direction='inbound' AND status='received'",
        (sender_id, channel, org_id),
    )
    db.commit()
    db.close()


def _find_client_by_sender_for_org(sender_id: str, channel: str, org_id: int):
    db = get_db()
    row = db.execute("""
        SELECT c.*
        FROM meta_messages m
        JOIN clients c ON c.id = m.client_id
        WHERE m.sender_id=? AND m.channel=? AND m.org_id=? AND m.client_id IS NOT NULL
        ORDER BY m.created_at DESC LIMIT 1
    """, (sender_id, channel, org_id)).fetchone()
    db.close()
    return row


def _row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def _messages_to_dicts(rows):
    return [_row_to_dict(row) for row in rows]


@meta_webhook_bp.route('/<channel>')
def inbox(channel):
    if channel not in ('messenger', 'instagram'):
        abort(404)

    org_id = _get_org_id()
    active_sender = request.args.get('sender_id', '').strip()
    conversations = _get_conversations(org_id, channel)
    if _backfill_profiles(conversations, org_id, channel,
                          force=request.args.get('refresh_profiles') == '1'):
        conversations = _get_conversations(org_id, channel)
    accounts = _get_accounts(org_id, channel)
    active_account = request.args.get('account', '').strip()
    if active_account:
        conversations = [c for c in conversations if c['page_id'] == active_account]
    active_msgs = []
    active_client = None
    active_conversation = None

    unread_total = sum((c['unread'] or 0) for c in conversations)
    view = request.args.get('view', '').strip()
    if view == 'unread':
        conversations = [c for c in conversations
                         if (c['unread'] or 0) > 0 or c['sender_id'] == active_sender]

    ai_ready = bool(os.getenv('ANTHROPIC_API_KEY'))
    ai_channel_enabled = False
    ai_contact = {'disabled': False, 'paused': False, 'paused_until': None}
    ai_instructions = ''
    try:
        from services import meta_ai
        ai_channel_enabled = meta_ai.is_channel_enabled(org_id, channel)
        ai_instructions = meta_ai.get_instructions(org_id, channel)
        if active_sender:
            ai_contact = meta_ai.contact_state(org_id, channel, active_sender)
    except Exception:
        pass

    if active_sender:
        _mark_read(org_id, channel, active_sender)
        active_msgs = _get_conversation(org_id, channel, active_sender)
        active_client = _find_client_by_sender_for_org(active_sender, channel, org_id)
        active_conversation = next((c for c in conversations if c['sender_id'] == active_sender), None)

    return render_template(
        'meta/inbox.html',
        channel=channel,
        accounts=accounts,
        active_account=active_account,
        conversations=conversations,
        active_sender=active_sender,
        active_msgs=active_msgs,
        active_client=active_client,
        active_conversation=active_conversation,
        view=view,
        unread_total=unread_total,
        ai_ready=ai_ready,
        ai_channel_enabled=ai_channel_enabled,
        ai_contact=ai_contact,
        ai_instructions=ai_instructions,
    )


def _handle_echo(channel, page_id, event, msg):
    """Mensaje enviado desde la cuenta (app de Instagram/Messenger, el CRM o la IA).
    Si lo escribió Henrry, se guarda en el chat y se pausa la IA en esa conversación."""
    try:
        recipient = (event.get('recipient') or {}).get('id', '')
        text = (msg.get('text') or '').strip()
        mid = msg.get('mid', '')
        if not recipient or recipient == page_id or not text:
            return
        from services import meta_ai
        org_id = _get_connection_org_id(page_id, channel)
        if meta_ai.is_bot_echo(org_id, channel, recipient, text):
            return
        meta_ai.pause(org_id, channel, recipient)
        db = get_db()
        try:
            dup = db.execute(
                "SELECT 1 FROM meta_messages WHERE org_id=? AND channel=? AND sender_id=? "
                "AND direction='outbound' AND (meta_message_id=? OR (message=? "
                "AND created_at > datetime('now', '-3 minutes'))) LIMIT 1",
                (org_id, channel, recipient, mid, text),
            ).fetchone()
        finally:
            db.close()
        if not dup:
            client_id = _find_client_by_sender(recipient, channel, org_id)
            _save_message(channel, recipient, page_id, 'outbound', text, mid, client_id, org_id)
    except Exception:
        pass


# ── Asistente IA: switches ────────────────────────────────────────────────────

@meta_webhook_bp.route('/meta/ai/channel', methods=['POST'])
def ai_channel_toggle():
    from services import meta_ai
    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip()
    if channel not in ('messenger', 'instagram'):
        return jsonify({'success': False, 'error': 'Canal inválido'}), 400
    org_id = _get_org_id()
    meta_ai.set_channel_enabled(org_id, channel, bool(data.get('enabled')))
    return jsonify({'success': True, 'enabled': meta_ai.is_channel_enabled(org_id, channel)})


@meta_webhook_bp.route('/meta/ai/instructions', methods=['POST'])
def ai_instructions_save():
    from services import meta_ai
    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip()
    if channel not in ('messenger', 'instagram'):
        return jsonify({'success': False, 'error': 'Canal inválido'}), 400
    text = meta_ai.set_instructions(_get_org_id(), channel, data.get('instructions') or '')
    return jsonify({'success': True, 'instructions': text})


@meta_webhook_bp.route('/meta/ai/contact', methods=['POST'])
def ai_contact_toggle():
    from services import meta_ai
    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip()
    sender_id = (data.get('sender_id') or '').strip()
    if channel not in ('messenger', 'instagram') or not sender_id:
        return jsonify({'success': False, 'error': 'Datos inválidos'}), 400
    org_id = _get_org_id()
    meta_ai.set_contact_enabled(org_id, channel, sender_id, bool(data.get('enabled')))
    return jsonify({'success': True, 'state': meta_ai.contact_state(org_id, channel, sender_id)})


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

    # Meta manda object='page' para Messenger y object='instagram' para IG Direct.
    # En ambos casos los eventos vienen en entry[].messaging[]
    obj     = data.get('object', 'page')
    channel = 'instagram' if obj == 'instagram' else 'messenger'

    for entry in data.get('entry', []):
        page_id = entry.get('id', '')   # Page ID (Messenger) o IG account ID (Instagram)

        # Los mensajes reales llegan en entry.messaging; las pruebas del panel
        # de Meta llegan en entry.changes[{field: 'messages', value: {...}}]
        events = list(entry.get('messaging', []))
        events += [c.get('value', {}) for c in entry.get('changes', [])
                   if c.get('field') == 'messages']

        for event in events:
            sender_id = event.get('sender', {}).get('id', '')
            msg       = event.get('message', {}) or {}
            if msg.get('is_echo'):
                _handle_echo(channel, page_id, event, msg)   # respuesta enviada por la cuenta
                continue
            if msg.get('is_deleted'):
                continue
            if sender_id == page_id:
                continue
            text   = msg.get('text', '')
            msg_id = msg.get('mid', '')
            if not text and msg.get('attachments'):
                kinds = ', '.join(sorted({(x.get('type') or 'archivo') for x in msg['attachments']}))
                text  = f'[Adjunto: {kinds}]'
            if not sender_id or not text:
                continue
            org_id    = _get_connection_org_id(page_id, channel)
            client_id = _find_client_by_sender(sender_id, channel, org_id)
            _save_message(channel, sender_id, page_id, 'inbound', text, msg_id, client_id, org_id)
            try:
                _ensure_contact_profile(org_id, channel, page_id, sender_id)
            except Exception:
                pass
            try:
                from services import meta_ai
                meta_ai.handle_incoming(org_id, channel, page_id, sender_id, text,
                                        _get_page_token(page_id, channel, org_id))
            except Exception:
                pass

    # ── Instagram Studio: comentarios (field=comments) → agente con Claude ──
    try:
        from services import ig_studio
        ig_studio.handle_webhook(data)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Instagram Studio: fallo al procesar el webhook')

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

    if channel not in ('messenger', 'instagram'):
        return jsonify({'success': False, 'error': 'Canal inválido'}), 400

    org_id = _get_org_id()
    token  = _get_page_token(page_id, channel, org_id)

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

        ai_state = None
        try:
            from services import meta_ai
            meta_ai.pause(org_id, channel, sender_id)   # intervención humana
            ai_state = meta_ai.contact_state(org_id, channel, sender_id)
        except Exception:
            pass

        return jsonify({'success': True, 'meta_message_id': meta_msg_id, 'ai_state': ai_state})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


# ── Conversations JSON (for future panel) ─────────────────────────────────────

@meta_webhook_bp.route('/meta/conversations')
def conversations():
    org_id = _get_org_id()
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
    org_id = _get_org_id()
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


@meta_webhook_bp.route('/meta/<channel>/conversation/<sender_id>')
def conversation_by_channel(channel, sender_id):
    if channel not in ('messenger', 'instagram'):
        return jsonify({'error': 'Canal inválido'}), 400

    org_id = _get_org_id()
    _mark_read(org_id, channel, sender_id)
    rows = _get_conversation(org_id, channel, sender_id)
    client = _find_client_by_sender_for_org(sender_id, channel, org_id)
    try:
        from services import meta_ai
        ai_state = meta_ai.contact_state(org_id, channel, sender_id)
    except Exception:
        ai_state = None
    return jsonify({
        'ai_state': ai_state,
        'messages': _messages_to_dicts(rows),
        'client': {
            'id': client['id'],
            'first_name': client['first_name'],
            'last_name': client['last_name'],
        } if client else None,
    })
