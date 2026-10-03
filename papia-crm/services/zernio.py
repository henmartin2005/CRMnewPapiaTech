"""
services/zernio.py
Cliente mínimo de la API de Zernio para WhatsApp (Cloud API de Meta vía Zernio).

Flujo:
  - Entrantes: Zernio → POST /webhook/zernio (routes/whatsapp.py)
  - Salientes: POST /v1/inbox/conversations/{conversationId}/messages
  - Primer contacto / ventana de 24h cerrada: plantilla aprobada por Meta
    vía POST /v1/inbox/conversations

Variables de entorno:
  ZERNIO_API_KEY          API key (Settings → API Keys en Zernio), permisos read-write
  ZERNIO_WA_ACCOUNT_ID    accountId de la cuenta WhatsApp conectada en Zernio
  ZERNIO_WEBHOOK_SECRET   secreto HMAC del webhook (el mismo que pones en Zernio)
  ZERNIO_API_BASE_URL     opcional, default https://zernio.com/api
"""
import hashlib
import hmac
import os
import re
import uuid

import requests

TIMEOUT = 15


class ZernioError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class TemplateRequired(ZernioError):
    """No hay conversación abierta (o la ventana de 24h cerró): hay que enviar plantilla."""


# ── Config ───────────────────────────────────────────────────────────────────

def base_url() -> str:
    return os.getenv('ZERNIO_API_BASE_URL', 'https://zernio.com/api').rstrip('/')


def api_key() -> str:
    return os.getenv('ZERNIO_API_KEY', '').strip()


def account_id() -> str:
    return os.getenv('ZERNIO_WA_ACCOUNT_ID', '').strip()


def is_configured() -> bool:
    return bool(api_key() and account_id())


def _headers(idempotent=False):
    h = {'Authorization': f'Bearer {api_key()}', 'Content-Type': 'application/json'}
    if idempotent:
        h['Idempotency-Key'] = str(uuid.uuid4())
    return h


def _request(method, path, **kwargs):
    if not api_key():
        raise ZernioError('ZERNIO_API_KEY no configurada', status=503)
    resp = requests.request(method, f'{base_url()}{path}', timeout=TIMEOUT, **kwargs)
    try:
        data = resp.json()
    except ValueError:
        data = {'raw': resp.text[:500]}
    if resp.status_code >= 400:
        msg = data.get('error') or data.get('message') or str(data)
        if isinstance(msg, dict):
            msg = msg.get('message') or str(msg)
        code = str(data.get('code') or '')
        if code in ('TEMPLATE_REQUIRED', '131047'):
            raise TemplateRequired(msg, status=resp.status_code, code=code)
        raise ZernioError(msg, status=resp.status_code, code=code)
    return data


# ── Teléfonos ────────────────────────────────────────────────────────────────

def digits(phone: str) -> str:
    return re.sub(r'\D', '', phone or '')


def to_e164(phone: str) -> str:
    d = digits(phone)
    return f'+{d}' if d else ''


# ── Webhook ──────────────────────────────────────────────────────────────────

def verify_signature(raw_body: bytes, signature: str) -> bool:
    """X-Zernio-Signature = hex(HMAC-SHA256(secret, raw_body)), sin prefijo."""
    secret = os.getenv('ZERNIO_WEBHOOK_SECRET', '').strip()
    if not secret:
        # Sin secreto configurado no se verifica (solo para pruebas).
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# ── API ──────────────────────────────────────────────────────────────────────

def list_accounts():
    return _request('GET', '/v1/accounts', headers=_headers())


def number_info(acc_id=None):
    return _request('GET', '/v1/whatsapp/number-info',
                    headers=_headers(), params={'accountId': acc_id or account_id()})


def find_conversation_id(phone: str):
    """Busca en Zernio la conversación de WhatsApp de ese teléfono."""
    target = digits(phone)
    cursor = None
    for _ in range(10):  # hasta 1000 conversaciones
        params = {'accountId': account_id(), 'platform': 'whatsapp', 'limit': 100}
        if cursor:
            params['cursor'] = cursor
        data = _request('GET', '/v1/inbox/conversations', headers=_headers(), params=params)
        items = data.get('data') or data.get('conversations') or []
        for conv in items:
            pid = digits(conv.get('participantId', ''))
            if pid and (pid == target or pid[-10:] == target[-10:]):
                return conv.get('id') or conv.get('_id')
        pag = data.get('pagination') or {}
        cursor = pag.get('nextCursor') or data.get('nextCursor')
        if not (pag.get('hasMore') or data.get('hasMore')) or not cursor:
            break
    return None


def send_text(conversation_id: str, text: str) -> dict:
    """Mensaje libre dentro de la ventana de 24h. Devuelve {'messageId', 'conversationId'}."""
    data = _request(
        'POST', f'/v1/inbox/conversations/{conversation_id}/messages',
        headers=_headers(idempotent=True),
        json={'accountId': account_id(), 'message': text},
    )
    return data.get('data') or {}


def start_with_template(phone: str, template_name: str, language: str = 'es',
                        params=None) -> dict:
    """Abre conversación con una plantilla aprobada. Devuelve {'messageId','conversationId'}."""
    body = {
        'accountId': account_id(),
        'participantId': digits(phone),
        'templateName': template_name,
        'templateLanguage': language,
    }
    if params:
        body['templateParams'] = list(params)
    data = _request('POST', '/v1/inbox/conversations',
                    headers=_headers(idempotent=True), json=body)
    return data.get('data') or {}


def list_templates():
    return _request('GET', '/v1/whatsapp/templates', headers=_headers(),
                    params={'accountId': account_id()})


def create_webhook(url: str, secret: str, name='CRM WhatsApp'):
    return _request('POST', '/v1/webhooks/settings', headers=_headers(), json={
        'name': name,
        'url': url,
        'secret': secret,
        'events': ['message.received', 'message.sent', 'message.delivered',
                   'message.read', 'message.failed'],
    })


# ── Media (stickers, fotos, audios…) ─────────────────────────────────────────

def download_media(url: str, max_bytes: int = 16 * 1024 * 1024):
    """
    Descarga un adjunto. Los de WhatsApp entrantes apuntan a
    GET /v1/whatsapp/media/{mediaId} y exigen el Bearer; Meta los borra a los
    ~7 días, así que hay que bajarlos al recibirlos.
    Devuelve (bytes, content_type). El API key solo se envía a Zernio.
    """
    if not url:
        raise ZernioError('adjunto sin url')
    from urllib.parse import urlparse
    if url.startswith('/v1/'):
        url = base_url() + url
    elif url.startswith('/'):
        b = urlparse(base_url())
        url = f'{b.scheme}://{b.netloc}{url}'
    host = (urlparse(url).hostname or '').lower()
    headers = {}
    if host == 'zernio.com' or host.endswith('.zernio.com') or url.startswith(base_url()):
        headers['Authorization'] = f'Bearer {api_key()}'
    resp = requests.get(url, headers=headers, timeout=TIMEOUT, stream=True)
    if resp.status_code >= 400:
        raise ZernioError(f'media HTTP {resp.status_code}', status=resp.status_code)
    chunks, size = [], 0
    for chunk in resp.iter_content(64 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise ZernioError('adjunto demasiado grande')
        chunks.append(chunk)
    ctype = (resp.headers.get('Content-Type') or 'application/octet-stream').split(';')[0].strip()
    return b''.join(chunks), ctype


# ── Envío de adjuntos (audios, stickers, imágenes, archivos) ────────────────

def upload_media(data: bytes, filename: str, content_type: str) -> str:
    """Sube un archivo a Zernio (POST /v1/media/upload-direct) y devuelve su URL pública.
    Los archivos se borran solos a los 7 días; solo sirven para enviarlos."""
    if not api_key():
        raise ZernioError('ZERNIO_API_KEY no configurada', status=503)
    resp = requests.post(
        f'{base_url()}/v1/media/upload-direct',
        headers={'Authorization': f'Bearer {api_key()}'},
        files={'file': (filename, data, content_type)},
        data={'contentType': content_type},
        timeout=60,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {'raw': resp.text[:500]}
    if resp.status_code >= 400:
        msg = body.get('error') or body.get('message') or str(body)
        if isinstance(msg, dict):
            msg = msg.get('message') or str(msg)
        raise ZernioError(f'No se pudo subir el archivo: {msg}', status=resp.status_code)
    url = body.get('url') or (body.get('data') or {}).get('url')
    if not url:
        raise ZernioError('Zernio no devolvió la URL del archivo subido')
    return url


def send_attachment(conversation_id: str, attachment_url: str, attachment_type: str,
                    voice_note: bool = False, caption: str = '', name: str = None) -> dict:
    """Envía un adjunto dentro de la ventana de 24h.
    attachment_type: image | video | audio | file (y 'sticker' si la cuenta lo soporta)."""
    body = {
        'accountId': account_id(),
        'attachmentUrl': attachment_url,
        'attachmentType': attachment_type,
    }
    if voice_note:
        body['voiceNote'] = True
    if caption:
        body['message'] = caption
    if name:
        body['attachmentName'] = name
    data = _request(
        'POST', f'/v1/inbox/conversations/{conversation_id}/messages',
        headers=_headers(idempotent=True),
        json=body,
    )
    return data.get('data') or {}


def list_messages(conversation_id: str, limit: int = 100, sort_order: str = 'desc') -> list:
    """Últimos mensajes de una conversación (incluye adjuntos con su url)."""
    data = _request(
        'GET', f'/v1/inbox/conversations/{conversation_id}/messages',
        headers=_headers(),
        params={'accountId': account_id(), 'limit': limit, 'sortOrder': sort_order},
    )
    return data.get('messages') or (data.get('data') or {}).get('messages') or []
