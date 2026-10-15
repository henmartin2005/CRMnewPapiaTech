"""Notificaciones push a la app móvil (Expo Push Service).

La app registra su token con POST /api/mobile/push-token. Cuando entra un
WhatsApp, el webhook llama a notify_whatsapp() y se avisa a todos los
teléfonos registrados de esa organización.
"""
import json
import logging
import threading
import urllib.request

from database import get_db

log = logging.getLogger(__name__)

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'


def _ensure_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS mobile_push_tokens (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER,
            org_id     INTEGER NOT NULL DEFAULT 1,
            platform   TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def is_valid_token(token: str) -> bool:
    return bool(token) and (token.startswith('ExponentPushToken[') or token.startswith('ExpoPushToken['))


def register(token, user_id, org_id=1, platform=''):
    db = get_db()
    try:
        _ensure_table(db)
        db.execute("""INSERT OR REPLACE INTO mobile_push_tokens (token, user_id, org_id, platform, updated_at)
                      VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""", (token, user_id, org_id, platform))
        db.commit()
    finally:
        db.close()


def unregister(token):
    db = get_db()
    try:
        _ensure_table(db)
        db.execute("DELETE FROM mobile_push_tokens WHERE token = ?", (token,))
        db.commit()
    finally:
        db.close()


def _tokens(org_id):
    db = get_db()
    try:
        _ensure_table(db)
        rows = db.execute("SELECT token FROM mobile_push_tokens WHERE org_id = ?", (org_id,)).fetchall()
        db.commit()
    finally:
        db.close()
    return [r[0] for r in rows]


def _post(messages):
    req = urllib.request.Request(
        EXPO_PUSH_URL,
        data=json.dumps(messages).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode('utf-8') or '{}')
    # Tokens de apps desinstaladas: se borran para no seguir enviando
    for msg, ticket in zip(messages, body.get('data') or []):
        if ticket.get('status') == 'error' and (ticket.get('details') or {}).get('error') == 'DeviceNotRegistered':
            unregister(msg['to'])


def _send_async(messages):
    def run():
        try:
            _post(messages)
        except Exception:
            log.exception('Push: no se pudo enviar a Expo')
    threading.Thread(target=run, daemon=True).start()


def notify_whatsapp(org_id, phone, client_id, sender_name, text, badge=None):
    """Aviso de WhatsApp entrante. Nunca lanza excepción (no debe romper el webhook)."""
    try:
        tokens = _tokens(org_id)
        if not tokens:
            return
        title = sender_name or phone
        body = (text or '[mensaje]')[:180]
        messages = []
        for token in tokens:
            m = {
                'to': token,
                'title': title,
                'body': body,
                'sound': 'default',
                'channelId': 'whatsapp',
                'priority': 'high',
                'data': {'type': 'whatsapp', 'phone': phone, 'client_id': client_id},
            }
            if badge is not None:
                m['badge'] = int(badge)
            messages.append(m)
        _send_async(messages)
    except Exception:
        log.exception('Push: error preparando la notificación')


def notify_event(org_id, title, body, data=None):
    """Aviso genérico a la app móvil (p. ej. el cliente subió archivos al portal).
    Nunca lanza excepción."""
    try:
        tokens = _tokens(org_id)
        if not tokens:
            return
        _send_async([{
            'to': t, 'title': (title or '')[:120], 'body': (body or '')[:180],
            'sound': 'default', 'channelId': 'whatsapp', 'priority': 'high',
            'data': data or {},
        } for t in tokens])
    except Exception:
        log.exception('Push: error preparando la notificación')
