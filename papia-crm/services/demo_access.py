"""Lógica del control de acceso a demos.

Puerto del sistema de Groomingmiami (Supabase) al CRM: códigos NEGOCIO-XXXX,
visitas limitadas, vencimiento, amarre opcional a dispositivo, sesión firmada
de 45 minutos y registro de actividad. Además avisa al dueño en la primera
visita y en cada bloqueo.
"""
import hashlib
import logging
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from database import get_db

log = logging.getLogger(__name__)

SESSION_MINUTES = 45
DEFAULT_VISITS = 3
DEFAULT_DAYS = 7
CODE_RE = re.compile(r'^[A-Z0-9]{1,20}-[A-Z0-9]{4}$')
_SUFFIX_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # sin 0/O, 1/I/L
SECTION_RE = re.compile(r'^[\w\- áéíóúñÁÉÍÓÚÑ/]{1,60}$')
RATE_WINDOW = 600          # 10 minutos
RATE_MAX_ATTEMPTS = 20     # validaciones por IP en la ventana
MAX_EVENTS_PER_DAY = 400   # eventos por enlace por día
TZ = ZoneInfo('America/New_York')


# ── Fechas ────────────────────────────────────────────────────────────────────
def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_db(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def from_db(value):
    return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


# ── Códigos ───────────────────────────────────────────────────────────────────
def code_prefix(negocio):
    """'INNA Beauty Salon' -> 'INNA'; 'El Portón de la Flaca' -> 'PORTON'."""
    plain = unicodedata.normalize('NFKD', negocio or '').encode('ascii', 'ignore').decode()
    words = [w for w in re.findall(r'[A-Za-z0-9]+', plain.upper())
             if w not in {'EL', 'LA', 'LOS', 'LAS', 'THE', 'DE', 'DEL', 'Y', 'AND'}]
    prefix = words[0] if words else 'DEMO'
    if len(prefix) < 3 and len(words) > 1:
        prefix += words[1]
    return prefix[:10] or 'DEMO'


def new_code(db, negocio):
    prefix = code_prefix(negocio)
    for _ in range(20):
        code = f"{prefix}-{''.join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(4))}"
        if not db.execute('SELECT 1 FROM demo_links WHERE codigo=?', (code,)).fetchone():
            return code
    raise RuntimeError('No se pudo generar un código único')


def normalize_code(value):
    code = (value or '').strip().upper()
    return code if CODE_RE.match(code) else None


# ── Sesión firmada ────────────────────────────────────────────────────────────
def _serializer():
    secret = os.environ.get('DEMO_SESSION_SECRET') or os.environ.get('SECRET_KEY') or 'papia-crm-dev-secret-2024'
    return URLSafeTimedSerializer(secret, salt='papia-demo-session')


def sign_session(link):
    return _serializer().dumps({'lid': link['id'], 'c': link['codigo'], 'o': link['org_id']})


def verify_session(token):
    if not isinstance(token, str) or len(token) > 1000:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MINUTES * 60)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and 'lid' in data else None


# ── Dispositivo e IP ──────────────────────────────────────────────────────────
def describe_device(ua):
    u = ua or ''
    os_name = ('iOS' if re.search(r'iPhone|iPad', u) else 'Android' if 'Android' in u
               else 'macOS' if 'Mac OS' in u else 'Windows' if 'Windows' in u
               else 'Linux' if 'Linux' in u else 'Otro')
    browser = ('Edge' if 'Edg/' in u else 'Chrome' if re.search(r'CriOS|Chrome/', u)
               else 'Firefox' if re.search(r'FxiOS|Firefox/', u) else 'Safari' if 'Safari/' in u
               else 'Otro')
    return f'{browser} · {os_name}'


def client_ip(request):
    # PythonAnywhere pone la IP real del visitante en X-Real-IP.
    return (request.headers.get('X-Real-IP')
            or (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            or request.remote_addr or '')


def rate_limited(db, ip):
    """True si la IP superó el límite. Guarda el intento actual."""
    ip_hash = hashlib.sha256(f'demo:{ip}'.encode()).hexdigest()[:32]
    now = time.time()
    db.execute('DELETE FROM demo_rate WHERE creado_el < ?', (now - RATE_WINDOW,))
    count = db.execute('SELECT COUNT(*) FROM demo_rate WHERE ip_hash=? AND creado_el>=?',
                       (ip_hash, now - RATE_WINDOW)).fetchone()[0]
    if count >= RATE_MAX_ATTEMPTS:
        return True
    db.execute('INSERT INTO demo_rate(ip_hash, creado_el) VALUES (?,?)', (ip_hash, now))
    return False


# ── Consumir visita (atómico) ─────────────────────────────────────────────────
def consume_visit(db, codigo, device_id):
    """Devuelve la fila del enlace si la visita es válida, o None.

    Debe llamarse dentro de BEGIN IMMEDIATE para que dos visitas simultáneas
    no se salten el límite."""
    link = db.execute(
        '''SELECT l.*, d.slug AS nicho, d.color AS demo_color, d.url AS demo_url, d.activo AS demo_activo
             FROM demo_links l JOIN demos d ON d.id = l.demo_id
            WHERE l.codigo=?''', (codigo,)).fetchone()
    if not link:
        return None, None
    if not link['activo'] or not link['demo_activo'] or from_db(link['vence_el']) < now_utc():
        return None, link
    if (link['amarrar_dispositivo'] and link['dispositivo_amarrado']
            and link['dispositivo_amarrado'] != device_id):
        return None, link
    if not link['ilimitado'] and link['visitas_usadas'] >= link['max_visitas']:
        return None, link
    bind = device_id if link['amarrar_dispositivo'] and not link['dispositivo_amarrado'] else link['dispositivo_amarrado']
    db.execute('UPDATE demo_links SET visitas_usadas = visitas_usadas + 1, dispositivo_amarrado=? WHERE id=?',
               (bind, link['id']))
    link = db.execute(
        '''SELECT l.*, d.slug AS nicho, d.color AS demo_color, d.url AS demo_url, d.activo AS demo_activo
             FROM demo_links l JOIN demos d ON d.id = l.demo_id WHERE l.id=?''', (link['id'],)).fetchone()
    return link, link


def add_log(db, link_id, tipo, seccion=None, dispositivo=None, duracion=None):
    db.execute('INSERT INTO demo_logs(link_id, tipo, seccion, dispositivo, duracion_segundos) VALUES (?,?,?,?,?)',
               (link_id, tipo, seccion, dispositivo, duracion))


def events_today(db, link_id):
    return db.execute(
        "SELECT COUNT(*) FROM demo_logs WHERE link_id=? AND fecha_hora >= datetime('now','-1 day')",
        (link_id,)).fetchone()[0]


# ── Avisos al dueño ───────────────────────────────────────────────────────────
def _visit_label(link):
    return 'ilimitado' if link['ilimitado'] else f"{link['visitas_usadas']} de {link['max_visitas']}"


def notify_owner(link, evento, dispositivo):
    """Primera visita o bloqueo. Nunca lanza: si un canal falla, el demo sigue."""
    fecha = now_utc().astimezone(TZ).strftime('%b %d, %I:%M %p')
    accion = 'abrió el demo' if evento == 'primera_visita' else 'intentó entrar y fue bloqueado'
    texto = f"{link['negocio']} {accion} · {_visit_label(link)} · {dispositivo} · {fecha} · {link['codigo']}"
    payload = {
        'evento': evento, 'texto': texto, 'codigo': link['codigo'],
        'prospecto': link['nombre_prospecto'], 'negocio': link['negocio'],
        'telefono': link['telefono'], 'visita': _visit_label(link),
        'dispositivo': dispositivo, 'fecha': fecha, 'cliente_id': link['cliente_id'],
    }

    # 1) Tarea + nota en el perfil del cliente (si el enlace está ligado a un lead)
    if link['cliente_id']:
        try:
            db = get_db()
            try:
                db.execute("INSERT INTO notes(client_id, note_type, content) VALUES (?, 'note', ?)",
                           (link['cliente_id'], f'Demo: {texto}'))
                if evento == 'primera_visita':
                    # Mismo formato que models.client._now_str() para que salga en Tasks ya vencida.
                    next_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    db.execute(
                        '''INSERT INTO follow_ups(client_id, method, summary, result, next_date, next_at,
                                                  reminder_comment, org_id)
                           VALUES (?, 'whatsapp', ?, '', ?, ?, ?, ?)''',
                        (link['cliente_id'], f"Llamar a {link['negocio']}: abrió el demo",
                         next_at[:10], next_at, 'Aviso automático del módulo Demos', link['org_id']))
                db.commit()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            log.warning('demos: no se pudo crear tarea/nota (%s)', exc)

    # 2) Webhook (n8n → WhatsApp/SMS al dueño)
    url = os.environ.get('DEMO_NOTIFY_WEBHOOK', '').strip()
    if url:
        headers = {}
        if os.environ.get('DEMO_NOTIFY_SECRET'):
            headers['X-Papia-Demo-Secret'] = os.environ['DEMO_NOTIFY_SECRET']
        try:
            requests.post(url, json=payload, headers=headers, timeout=5)
        except requests.RequestException as exc:
            log.warning('demos: webhook falló (%s)', exc)

    # 3) Correo por el Gmail conectado de la organización
    to_email = os.environ.get('DEMO_NOTIFY_EMAIL', '').strip()
    if to_email:
        try:
            from services.mailer import send_email
            asunto = f"Demo · {link['negocio']} {accion}"
            send_email(link['org_id'], to_email, asunto,
                       f'<p>{texto}</p><p>Prospecto: {link["nombre_prospecto"]}'
                       f'{" · " + link["telefono"] if link["telefono"] else ""}</p>')
        except Exception as exc:  # noqa: BLE001
            log.warning('demos: correo de aviso falló (%s)', exc)
    return payload


def default_expiry(days=DEFAULT_DAYS):
    return to_db(now_utc() + timedelta(days=days))
