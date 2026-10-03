"""
Google Calendar de PapiaTech para el asistente de WhatsApp.

- Usa la misma conexión OAuth de Gmail del CRM (tabla gmail_tokens).
- Consulta disponibilidad con freeBusy para no encimar citas.
- Crea (o mueve) la cita del cliente en el calendario principal.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

TZ_NAME = 'America/New_York'          # hora de Miami
TZ = ZoneInfo(TZ_NAME)
API = 'https://www.googleapis.com/calendar/v3'
CALENDAR_ID = 'primary'
SCOPE = 'https://www.googleapis.com/auth/calendar'

CALL_MINUTES = 30                     # duración de cada llamada
WORK_START = 9                        # primera hora agendable
WORK_END = 19                         # la llamada debe terminar antes de esta hora
WORK_DAYS = {0, 1, 2, 3, 4, 5}        # lunes a sábado
MIN_NOTICE_MIN = 60                   # no agendar con menos de 1 h de anticipación

DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']


def now():
    return datetime.now(TZ)


def human(dt):
    """'lunes 5 de octubre de 2026, 6:00 PM'"""
    dt = dt.astimezone(TZ)
    hora = dt.strftime('%I:%M %p').lstrip('0')
    return f'{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month - 1]} de {dt.year}, {hora}'


def parse_local(value):
    """ISO 'YYYY-MM-DDTHH:MM' (hora de Miami si no trae zona)."""
    value = (value or '').strip().replace(' ', 'T')
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _headers(org_id=1):
    from routes.emails import _load_creds   # import tardío: evita ciclos
    creds = _load_creds(org_id)
    if not creds or not creds.token:
        raise RuntimeError('Google no está conectado: conecta Gmail en el CRM.')
    return {'Authorization': f'Bearer {creds.token}', 'Content-Type': 'application/json'}


def status(org_id=1):
    """(ok, detalle) probando acceso real al calendario."""
    try:
        r = requests.get(f'{API}/calendars/{CALENDAR_ID}', headers=_headers(org_id), timeout=10)
    except Exception as exc:
        return False, str(exc)[:200]
    if r.status_code == 200:
        return True, r.json().get('id', '')
    if r.status_code in (401, 403):
        if 'accessNotConfigured' in r.text or 'has not been used' in r.text:
            return False, 'Activa la Google Calendar API en Google Cloud Console.'
        return False, 'Falta el permiso de Calendar: pulsa «Reconectar Google» y acepta el acceso al calendario.'
    return False, f'Calendar API {r.status_code}: {r.text[:150]}'


def busy_ranges(start, end, org_id=1):
    body = {'timeMin': start.isoformat(), 'timeMax': end.isoformat(),
            'timeZone': TZ_NAME, 'items': [{'id': CALENDAR_ID}]}
    r = requests.post(f'{API}/freeBusy', headers=_headers(org_id), json=body, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f'freeBusy {r.status_code}: {r.text[:200]}')
    cal = (r.json().get('calendars') or {}).get(CALENDAR_ID) or {}
    if cal.get('errors'):
        raise RuntimeError(f"freeBusy: {cal['errors']}")
    return [(parse_local(b['start']), parse_local(b['end'])) for b in cal.get('busy', [])]


def _overlaps(start, end, ranges, ignore=None):
    for b_start, b_end in ranges:
        if ignore and b_start == ignore[0] and b_end == ignore[1]:
            continue
        if start < b_end and end > b_start:
            return True
    return False


def in_hours(start, minutes=CALL_MINUTES):
    end = start + timedelta(minutes=minutes)
    return (start.weekday() in WORK_DAYS and start.hour >= WORK_START
            and (end.hour < WORK_END or (end.hour == WORK_END and end.minute == 0))
            and end.date() == start.date())


def free_slots(around, count=3, minutes=CALL_MINUTES, org_id=1, ignore=None):
    """Próximos huecos libres a partir de 'around' (o de ahora), en pasos de 30 min."""
    earliest = now() + timedelta(minutes=MIN_NOTICE_MIN)
    t = max(around, earliest).replace(second=0, microsecond=0)
    if t.minute % 30:
        t += timedelta(minutes=30 - t.minute % 30)
    horizon = t + timedelta(days=10)
    ranges = busy_ranges(t, horizon, org_id)
    out = []
    while t < horizon and len(out) < count:
        end = t + timedelta(minutes=minutes)
        if in_hours(t, minutes) and not _overlaps(t, end, ranges, ignore):
            out.append(t)
        t += timedelta(minutes=30)
    return out


def check(start, minutes=CALL_MINUTES, org_id=1, ignore=None):
    """('ok'|'pasado'|'fuera_horario'|'ocupado', alternativas)"""
    if start < now() + timedelta(minutes=MIN_NOTICE_MIN):
        return 'pasado', free_slots(now(), org_id=org_id, ignore=ignore)
    if not in_hours(start, minutes):
        base = start.replace(hour=WORK_START, minute=0) if start.hour < WORK_START else start
        return 'fuera_horario', free_slots(base, org_id=org_id, ignore=ignore)
    end = start + timedelta(minutes=minutes)
    if _overlaps(start, end, busy_ranges(start, end, org_id), ignore):
        # alternativas más cercanas a la hora pedida (antes o después), en orden cronológico
        day_start = start.replace(hour=WORK_START, minute=0)
        pool = free_slots(day_start, count=40, org_id=org_id, ignore=ignore)
        near = sorted(pool, key=lambda s: abs((s - start).total_seconds()))[:3]
        return 'ocupado', sorted(near)
    return 'ok', []


def create_or_move(start, summary, description, minutes=CALL_MINUTES, event_id=None, org_id=1):
    """Crea el evento; si event_id existe, lo mueve (reagendar sin duplicar). Devuelve (id, link)."""
    end = start + timedelta(minutes=minutes)
    body = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start.isoformat(), 'timeZone': TZ_NAME},
        'end': {'dateTime': end.isoformat(), 'timeZone': TZ_NAME},
        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 15}]},
    }
    h = _headers(org_id)
    if event_id:
        r = requests.patch(f'{API}/calendars/{CALENDAR_ID}/events/{event_id}', headers=h, json=body, timeout=15)
        if r.status_code == 200:
            d = r.json()
            return d['id'], d.get('htmlLink', '')
        log.warning('No se pudo mover el evento %s (%s); se crea uno nuevo', event_id, r.status_code)
    r = requests.post(f'{API}/calendars/{CALENDAR_ID}/events', headers=h, json=body, timeout=15)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'Calendar insert {r.status_code}: {r.text[:200]}')
    d = r.json()
    return d['id'], d.get('htmlLink', '')


def get_event_range(event_id, org_id=1):
    """(start, end) de un evento existente, o None si ya no existe / fue cancelado."""
    r = requests.get(f'{API}/calendars/{CALENDAR_ID}/events/{event_id}', headers=_headers(org_id), timeout=10)
    if r.status_code != 200:
        return None
    d = r.json()
    if d.get('status') == 'cancelled' or 'dateTime' not in (d.get('start') or {}):
        return None
    return parse_local(d['start']['dateTime']), parse_local(d['end']['dateTime'])
