"""
Notificaciones push para la app móvil (iOS por APNs).

Variables de entorno (.env):
  APNS_KEY_ID      Key ID de la llave .p8 (Apple Developer → Keys)
  APNS_TEAM_ID     Team ID de la cuenta de Apple Developer
  APNS_BUNDLE_ID   Bundle ID de la app (ej. com.papiatech.crm)
  APNS_KEY_PATH    Ruta al archivo AuthKey_XXXX.p8   (o APNS_KEY con el contenido)

Cada dispositivo guarda su ambiente: 'sandbox' (build de Xcode) o 'production'
(TestFlight / App Store). APNs rechaza un token enviado al ambiente equivocado.

Si las variables no están configuradas, notify_* no hace nada (y no rompe el webhook).
"""
import json
import logging
import os
import time

import jwt

from database import get_db

log = logging.getLogger(__name__)

APNS_HOSTS = {
    'production': 'https://api.push.apple.com',
    'sandbox': 'https://api.sandbox.push.apple.com',
}
_TOKEN_TTL = 40 * 60          # Apple acepta tokens de hasta 60 min; se renuevan a los 40
_jwt_cache = {'token': None, 'at': 0}
_SCHEMA_OK = False


# ── dispositivos ─────────────────────────────────────────────────────────────

def _ensure_schema(db):
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS mobile_devices (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id       INTEGER NOT NULL DEFAULT 1,
            user_id      INTEGER,
            platform     TEXT    NOT NULL DEFAULT 'ios',
            token        TEXT    NOT NULL UNIQUE,
            environment  TEXT    NOT NULL DEFAULT 'production',
            app_version  TEXT    NOT NULL DEFAULT '',
            device_name  TEXT    NOT NULL DEFAULT '',
            notify_whatsapp INTEGER NOT NULL DEFAULT 1,
            last_error   TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mobile_devices_org ON mobile_devices(org_id, platform);
    """)
    _SCHEMA_OK = True


def register_device(org_id, user_id, token, platform='ios', environment='production',
                    app_version='', device_name=''):
    token = (token or '').strip()
    if not token or len(token) > 400:
        raise ValueError('Token de dispositivo inválido')
    environment = 'sandbox' if environment == 'sandbox' else 'production'
    platform = platform if platform in ('ios', 'android') else 'ios'
    db = get_db()
    try:
        _ensure_schema(db)
        db.execute("""
            INSERT INTO mobile_devices (org_id, user_id, platform, token, environment, app_version, device_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                org_id=excluded.org_id, user_id=excluded.user_id, platform=excluded.platform,
                environment=excluded.environment, app_version=excluded.app_version,
                device_name=excluded.device_name, last_error=NULL, updated_at=datetime('now')
        """, (org_id, user_id, platform, token, environment, (app_version or '')[:40], (device_name or '')[:80]))
        db.commit()
    finally:
        db.close()


def unregister_device(token):
    db = get_db()
    try:
        _ensure_schema(db)
        db.execute("DELETE FROM mobile_devices WHERE token=?", ((token or '').strip(),))
        db.commit()
    finally:
        db.close()


def set_device_prefs(token, notify_whatsapp):
    db = get_db()
    try:
        _ensure_schema(db)
        db.execute("UPDATE mobile_devices SET notify_whatsapp=?, updated_at=datetime('now') WHERE token=?",
                   (1 if notify_whatsapp else 0, (token or '').strip()))
        db.commit()
    finally:
        db.close()


def _devices(org_id, kind='whatsapp'):
    db = get_db()
    try:
        _ensure_schema(db)
        col = 'notify_whatsapp' if kind == 'whatsapp' else '1'
        return [dict(r) for r in db.execute(
            f"SELECT * FROM mobile_devices WHERE org_id=? AND platform='ios' AND {col}=1", (org_id,)).fetchall()]
    finally:
        db.close()


def _drop(token, reason):
    log.info('APNs: se elimina token inválido (%s)', reason)
    unregister_device(token)


def _mark_error(token, reason):
    db = get_db()
    try:
        db.execute("UPDATE mobile_devices SET last_error=? WHERE token=?", (str(reason)[:300], token))
        db.commit()
    finally:
        db.close()


# ── APNs ─────────────────────────────────────────────────────────────────────

def is_configured():
    return bool(os.getenv('APNS_KEY_ID') and os.getenv('APNS_TEAM_ID') and os.getenv('APNS_BUNDLE_ID')
                and (os.getenv('APNS_KEY') or os.getenv('APNS_KEY_PATH')))


def _private_key():
    key = os.getenv('APNS_KEY', '').replace('\\n', '\n').strip()
    if key:
        return key
    with open(os.path.expanduser(os.getenv('APNS_KEY_PATH', '')), 'r') as fh:
        return fh.read()


def _provider_token():
    now = time.time()
    if _jwt_cache['token'] and now - _jwt_cache['at'] < _TOKEN_TTL:
        return _jwt_cache['token']
    token = jwt.encode({'iss': os.getenv('APNS_TEAM_ID'), 'iat': int(now)}, _private_key(),
                       algorithm='ES256', headers={'kid': os.getenv('APNS_KEY_ID')})
    _jwt_cache.update(token=token, at=now)
    return token


def _http_client():
    import httpx    # requiere httpx[http2]: APNs solo habla HTTP/2
    return httpx.Client(http2=True, timeout=8.0)


def send(device, payload, collapse_id=None, thread_id=None):
    """Envía un push a un dispositivo. Devuelve True si APNs lo aceptó."""
    host = APNS_HOSTS.get(device.get('environment'), APNS_HOSTS['production'])
    headers = {
        'authorization': f'bearer {_provider_token()}',
        'apns-topic': os.getenv('APNS_BUNDLE_ID'),
        'apns-push-type': 'alert',
        'apns-priority': '10',
    }
    if collapse_id:
        headers['apns-collapse-id'] = collapse_id[:64]
    with _http_client() as client:
        res = client.post(f"{host}/3/device/{device['token']}", headers=headers,
                          content=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    if res.status_code == 200:
        return True
    try:
        reason = res.json().get('reason', '')
    except ValueError:
        reason = res.text[:200]
    if res.status_code == 410 or reason in ('BadDeviceToken', 'Unregistered', 'DeviceTokenNotForTopic'):
        _drop(device['token'], reason or res.status_code)
    else:
        if reason in ('ExpiredProviderToken', 'InvalidProviderToken'):
            _jwt_cache.update(token=None, at=0)
        log.warning('APNs %s: %s', res.status_code, reason)
        _mark_error(device['token'], f'{res.status_code} {reason}')
    return False


# ── eventos del CRM ──────────────────────────────────────────────────────────

def notify_whatsapp(org_id, phone, sender_name, text, client_id=None, unread=None):
    """Push cuando entra un WhatsApp. Nunca lanza excepciones (se llama desde el webhook)."""
    if not is_configured():
        return 0
    try:
        devices = _devices(org_id, 'whatsapp')
        if not devices:
            return 0
        body = (text or '').strip() or 'Nuevo mensaje'
        if len(body) > 180:
            body = body[:177] + '…'
        payload = {
            'aps': {
                'alert': {'title': sender_name or phone, 'subtitle': 'WhatsApp', 'body': body},
                'sound': 'default',
                'thread-id': f'wa:{phone}',
                'category': 'WHATSAPP_MESSAGE',
                'mutable-content': 1,
            },
            'type': 'whatsapp',
            'phone': phone,
            'client_id': client_id,
        }
        if unread is not None:
            payload['aps']['badge'] = int(unread)
        sent = 0
        for device in devices:
            try:
                if send(device, payload, thread_id=f'wa:{phone}'):
                    sent += 1
            except Exception:
                log.exception('APNs: error enviando a un dispositivo')
        return sent
    except Exception:
        log.exception('APNs: no se pudo notificar el WhatsApp entrante')
        return 0
