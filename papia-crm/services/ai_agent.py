"""
Agente de IA para WhatsApp (Claude).

- Responde preguntas básicas sobre Papia Technology Solutions.
- Capta datos del cliente (nombre, email, servicio de interés) y los guarda en su ficha.
- Ofrece agendar una llamada y deja la solicitud como nota en el CRM.
- Se pausa en una conversación cuando un humano escribe en ella (desde el CRM o el celular).

Requiere ANTHROPIC_API_KEY en el .env. Modelo configurable con AI_BOT_MODEL.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime

import requests

from database import get_db
from services import google_calendar as gcal

log = logging.getLogger(__name__)

API_URL = 'https://api.anthropic.com/v1/messages'
DEFAULT_MODEL = 'claude-haiku-4-5'
HISTORY_LIMIT = 20          # mensajes previos que se envían como contexto
MAX_TOOL_ROUNDS = 4

DEFAULT_BUSINESS_INFO = """Papia Technology Solutions LLC (PapiaTech) — Miami, Florida.
Fundador y CEO: Henrry Martín. Atendemos en español e inglés.
Servicios: páginas web y landing pages, tiendas online (Shopify), CRMs a la medida,
apps móviles, chatbots con IA, automatizaciones (n8n, Make, Zapier), dashboards y hosting.
Web: https://www.papiatech.com — Formulario: https://www.papiatech.com/cuentanos-tu-proyecto
Planes de pago flexibles disponibles (AfterPay).
Los precios dependen del proyecto: Henrry prepara una cotización personalizada después de una llamada corta."""

SERVICE_MAP = {
    'website': 'website', 'web': 'website', 'landing': 'website', 'pagina': 'website',
    'página': 'website', 'tienda': 'website', 'shopify': 'website', 'ecommerce': 'website',
    'crm': 'crm', 'mobile_app': 'mobile_app', 'app': 'mobile_app', 'aplicacion': 'mobile_app',
    'aplicación': 'mobile_app', 'consulting': 'consulting', 'consultoria': 'consulting',
    'consultoría': 'consulting', 'automatizacion': 'other', 'automatización': 'other',
    'chatbot': 'other', 'other': 'other',
}

TOOLS = [
    {
        'name': 'guardar_datos_cliente',
        'description': ('Guarda en el CRM los datos que el cliente haya dado. Llama esta herramienta '
                        'en cuanto el cliente comparta su nombre, email o el servicio que le interesa. '
                        'Envía solo los campos que el cliente dijo explícitamente.'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'nombre': {'type': 'string', 'description': 'Nombre(s) del cliente'},
                'apellido': {'type': 'string', 'description': 'Apellido(s) del cliente'},
                'email': {'type': 'string', 'description': 'Correo electrónico'},
                'servicio': {'type': 'string',
                             'description': 'Servicio de interés: website, crm, mobile_app, consulting u other'},
                'detalles': {'type': 'string', 'description': 'Resumen breve de lo que necesita'},
            },
        },
    },
    {
        'name': 'agendar_llamada',
        'description': ('Agenda una llamada de 30 minutos con Henrry en su Google Calendar. Úsala cuando el cliente '
                        'acepte la llamada y haya dicho día y hora. Comprueba que el horario esté libre: '
                        'responde AGENDADA, OCUPADO (con horarios libres) o NO_AGENDADA. Si el cliente ya tenía '
                        'una cita, la mueve al nuevo horario (no crea duplicados).'),
        'input_schema': {
            'type': 'object',
            'properties': {
                'fecha_hora': {'type': 'string',
                               'description': 'Inicio de la llamada en hora de Miami, formato YYYY-MM-DDTHH:MM '
                                              '(ej. 2026-10-05T18:00)'},
                'motivo': {'type': 'string', 'description': 'Tema de la llamada'},
            },
            'required': ['fecha_hora'],
        },
    },
]


# ── Persistencia ────────────────────────────────────────────────────────────

def _ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_settings (
        org_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        business_info TEXT,
        pause_hours INTEGER NOT NULL DEFAULT 24,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_state (
        org_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        paused_until TIMESTAMP,
        last_bot_text TEXT,
        PRIMARY KEY (org_id, phone))""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_contacts (
        org_id INTEGER NOT NULL,
        digits TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (org_id, digits))""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_usage (
        org_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        model TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'whatsapp',
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (org_id, day, model, source))""")
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_bookings (
        org_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        client_id INTEGER,
        event_id TEXT NOT NULL,
        start_at TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (org_id, phone))""")


def get_settings(org_id=1):
    db = get_db()
    try:
        _ensure_tables(db)
        row = db.execute("SELECT enabled, business_info, pause_hours FROM ai_bot_settings WHERE org_id = ?",
                         (org_id,)).fetchone()
        db.commit()
    finally:
        db.close()
    if not row:
        return {'enabled': False, 'business_info': DEFAULT_BUSINESS_INFO, 'pause_hours': 24}
    return {
        'enabled': bool(row[0]),
        'business_info': row[1] or DEFAULT_BUSINESS_INFO,
        'pause_hours': int(row[2] or 24),
    }


def save_settings(org_id, enabled, business_info, pause_hours):
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("""INSERT OR REPLACE INTO ai_bot_settings
                      (org_id, enabled, business_info, pause_hours, updated_at)
                      VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                   (org_id, 1 if enabled else 0, business_info, pause_hours))
        db.commit()
    finally:
        db.close()


def _state(db, org_id, phone):
    return db.execute("SELECT paused_until, last_bot_text FROM ai_bot_state WHERE org_id = ? AND phone = ?",
                      (org_id, phone)).fetchone()


def is_paused(phone, org_id=1):
    db = get_db()
    try:
        _ensure_tables(db)
        row = db.execute("""SELECT 1 FROM ai_bot_state WHERE org_id = ? AND phone = ?
                            AND paused_until IS NOT NULL AND paused_until > CURRENT_TIMESTAMP""",
                         (org_id, phone)).fetchone()
        db.commit()
    finally:
        db.close()
    return bool(row)


def pause(phone, org_id=1, hours=None):
    """Un humano intervino: el bot deja de responder en esta conversación."""
    if hours is None:
        hours = get_settings(org_id)['pause_hours']
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("INSERT OR IGNORE INTO ai_bot_state (org_id, phone) VALUES (?, ?)", (org_id, phone))
        db.execute(f"UPDATE ai_bot_state SET paused_until = datetime('now', '+{int(hours)} hours') "
                   "WHERE org_id = ? AND phone = ?", (org_id, phone))
        db.commit()
    finally:
        db.close()


def resume(phone, org_id=1):
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("UPDATE ai_bot_state SET paused_until = NULL WHERE org_id = ? AND phone = ?",
                   (org_id, phone))
        db.commit()
    finally:
        db.close()


# ── Interruptor del bot por contacto (activado por defecto) ───────────────────

def _digits(phone):
    return re.sub(r'\D', '', phone or '')


def is_disabled(phone, org_id=1):
    """True si el bot fue desactivado manualmente para este contacto."""
    d = _digits(phone)
    if not d:
        return False
    db = get_db()
    try:
        _ensure_tables(db)
        row = db.execute("SELECT disabled FROM ai_bot_contacts WHERE org_id = ? AND digits = ?",
                         (org_id, d)).fetchone()
        db.commit()
    finally:
        db.close()
    return bool(row and row[0])


def set_contact_enabled(phone, enabled, org_id=1):
    """Activa/desactiva el bot para un contacto. Al activarlo también quita la pausa."""
    d = _digits(phone)
    if not d:
        return
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("""INSERT OR REPLACE INTO ai_bot_contacts (org_id, digits, disabled, updated_at)
                      VALUES (?, ?, ?, CURRENT_TIMESTAMP)""", (org_id, d, 0 if enabled else 1))
        if enabled:
            db.execute("UPDATE ai_bot_state SET paused_until = NULL WHERE org_id = ? AND phone = ?",
                       (org_id, phone))
        db.commit()
    finally:
        db.close()


def contact_bot_state(phone, org_id=1):
    """Estado para la cabecera del chat: on / off (manual) / paused (intervención humana)."""
    try:
        settings = get_settings(org_id)
        if is_disabled(phone, org_id):
            status = 'off'
        elif is_paused(phone, org_id):
            status = 'paused'
        else:
            status = 'on'
        return {'status': status, 'global_enabled': settings['enabled']}
    except Exception:
        log.exception('Bot WhatsApp: no se pudo leer el estado de %s', phone)
        return None


def is_bot_echo(phone, text, org_id=1):
    """True si un 'message.sent' de Zernio es el eco de lo que acaba de mandar el bot."""
    db = get_db()
    try:
        _ensure_tables(db)
        row = _state(db, org_id, phone)
    finally:
        db.close()
    return bool(row and row[1] and (text or '').strip() == row[1].strip())


def _set_last_bot_text(phone, text, org_id):
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("INSERT OR IGNORE INTO ai_bot_state (org_id, phone) VALUES (?, ?)", (org_id, phone))
        db.execute("UPDATE ai_bot_state SET last_bot_text = ? WHERE org_id = ? AND phone = ?",
                   (text, org_id, phone))
        db.commit()
    finally:
        db.close()


# ── Contexto del cliente ────────────────────────────────────────────────────

def _client_summary(client_id):
    if not client_id:
        return 'Contacto nuevo, sin ficha todavía.'
    db = get_db()
    try:
        c = db.execute("SELECT first_name, last_name, email, project_type, pipeline_stage "
                       "FROM clients WHERE id = ?", (client_id,)).fetchone()
    finally:
        db.close()
    if not c:
        return 'Contacto nuevo, sin ficha todavía.'
    email = c[2] or ''
    return (f"Nombre en CRM: {(c[0] or '').strip()} {(c[1] or '').strip()} | "
            f"Email: {email or 'NO TENEMOS'} | Servicio: {c[3] or 'sin definir'} | Etapa: {c[4]}")


def _history(phone, org_id):
    db = get_db()
    try:
        rows = db.execute("""SELECT direction, message FROM whatsapp_messages
                             WHERE phone = ? AND org_id = ? ORDER BY id DESC LIMIT ?""",
                          (phone, org_id, HISTORY_LIMIT)).fetchall()
    finally:
        db.close()
    msgs = []
    for direction, text in reversed(rows):
        role = 'user' if direction == 'inbound' else 'assistant'
        text = (text or '').strip() or '[mensaje sin texto]'
        if msgs and msgs[-1]['role'] == role:
            msgs[-1]['content'] += '\n' + text
        else:
            msgs.append({'role': role, 'content': text})
    while msgs and msgs[0]['role'] != 'user':
        msgs.pop(0)
    return msgs


def _system_prompt(settings, client_id):
    return f"""Eres el asistente virtual de WhatsApp de Papia Technology Solutions.
Hablas en nombre del equipo, de forma cálida, breve y profesional (mensajes cortos, estilo WhatsApp, máximo 3–4 líneas).
Responde en el idioma del cliente (español o inglés).

FECHA Y HORA ACTUAL: {gcal.human(gcal.now())} (hora de Miami).

INFORMACIÓN DEL NEGOCIO:
{settings['business_info']}

DATOS ACTUALES DEL CLIENTE EN EL CRM:
{_client_summary(client_id)}

TU OBJETIVO:
1. Responder preguntas básicas usando SOLO la información del negocio. Si no sabes algo (precios exactos,
   plazos concretos, temas técnicos complejos), dilo con honestidad y ofrece que Henrry lo contacte.
2. Conseguir, de forma natural y sin interrogar, estos datos si faltan: nombre completo, email y servicio de interés.
   Pide uno o dos a la vez. Cuando el cliente dé un dato, llama a guardar_datos_cliente.
3. Cuando haya interés real, ofrecer una llamada corta (30 min) con Henrry y preguntar día y hora.
   Convierte lo que diga el cliente ("el lunes", "mañana a las 6") en una fecha exacta usando la FECHA ACTUAL.
   Si el día de la semana no coincide con la fecha que dio (ej. "lunes 4" cuando el 4 es domingo), pregúntale cuál es antes de agendar.
   Luego llama a agendar_llamada con fecha_hora en formato YYYY-MM-DDTHH:MM (hora de Miami):
   - AGENDADA → confírmale día y hora exactos.
   - OCUPADO o NO_AGENDADA → explícaselo y ofrécele los horarios libres que devuelve la herramienta.
   Nunca digas que la cita quedó agendada si la herramienta no respondió AGENDADA.

REGLAS:
- Nunca inventes precios, promociones, garantías ni compromisos.
- No pidas datos sensibles (tarjetas, contraseñas, documentos).
- Si el cliente pide hablar con una persona, está molesto o el tema es delicado, dile que Henrry le responderá pronto.
- No menciones que eres un modelo de IA de un proveedor concreto; si preguntan, eres el asistente virtual de PapiaTech."""


# ── Herramientas ────────────────────────────────────────────────────────────

def _add_note(client_id, content):
    if not client_id:
        return
    db = get_db()
    try:
        db.execute("INSERT INTO notes (client_id, note_type, content) VALUES (?, 'whatsapp', ?)",
                   (client_id, content))
        db.commit()
    finally:
        db.close()


def _tool_guardar_datos(args, client_id, phone):
    if not client_id:
        return 'No hay ficha de cliente para este número.'
    fields, values = [], []
    nombre = (args.get('nombre') or '').strip()
    apellido = (args.get('apellido') or '').strip()
    email = (args.get('email') or '').strip().lower()
    servicio = (args.get('servicio') or '').strip().lower()
    detalles = (args.get('detalles') or '').strip()

    if nombre:
        fields.append('first_name = ?'); values.append(nombre[:80])
    if apellido:
        fields.append('last_name = ?'); values.append(apellido[:80])
    if email:
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            return f'El email "{email}" no parece válido; pídele que lo confirme.'
        fields.append('email = ?'); values.append(email[:255])
    if servicio:
        fields.append('project_type = ?'); values.append(SERVICE_MAP.get(servicio, 'other'))
    if detalles:
        fields.append("project_details = TRIM(COALESCE(project_details, '') || ' ' || ?)")
        values.append(detalles[:500])

    if fields:
        db = get_db()
        try:
            db.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", (*values, client_id))
            db.commit()
        finally:
            db.close()
    resumen = ', '.join(f'{k}: {v}' for k, v in args.items() if v)
    _add_note(client_id, f'🤖 Bot WhatsApp captó datos — {resumen}')
    return 'Datos guardados en el CRM.'


def _tool_solicitar_llamada(args, client_id, phone):
    horario = (args.get('horario_preferido') or '').strip()
    motivo = (args.get('motivo') or '').strip()
    _add_note(client_id, f'📞 Solicitud de llamada (bot WhatsApp) — Horario preferido: {horario}'
                         + (f' | Motivo: {motivo}' if motivo else '') + f' | Tel: {phone}')
    if client_id:
        db = get_db()
        try:
            db.execute("UPDATE clients SET pipeline_stage = 'contacted' "
                       "WHERE id = ? AND pipeline_stage = 'new_lead'", (client_id,))
            db.commit()
        finally:
            db.close()
    return 'Solicitud de llamada registrada. Henrry confirmará el horario.'


PRICES_PER_MTOK = {                  # USD por millón de tokens (entrada, salida)
    'haiku': (1.0, 5.0),
    'sonnet': (3.0, 15.0),
    'opus': (5.0, 25.0),
}
_usage_ctx = threading.local()


def _price_for(model):
    try:
        p_in = float(os.getenv('AI_BOT_PRICE_IN') or 0)
        p_out = float(os.getenv('AI_BOT_PRICE_OUT') or 0)
    except ValueError:
        p_in = p_out = 0
    if p_in and p_out:
        return p_in, p_out
    for key, prices in PRICES_PER_MTOK.items():
        if key in (model or ''):
            return prices
    return PRICES_PER_MTOK['sonnet']


def _record_usage(model, usage, org_id=1):
    """Suma el consumo real (tokens que devuelve la API) por día de Miami."""
    try:
        t_in = int(usage.get('input_tokens') or 0)
        t_out = int(usage.get('output_tokens') or 0)
        c_write = int(usage.get('cache_creation_input_tokens') or 0)
        c_read = int(usage.get('cache_read_input_tokens') or 0)
        p_in, p_out = _price_for(model)
        cost = (t_in * p_in + c_write * p_in * 1.25 + c_read * p_in * 0.1 + t_out * p_out) / 1_000_000
        source = getattr(_usage_ctx, 'source', None) or 'whatsapp'
        day = gcal.now().strftime('%Y-%m-%d')
        db = get_db()
        try:
            _ensure_tables(db)
            db.execute("""INSERT INTO ai_bot_usage
                            (org_id, day, model, source, calls, input_tokens, output_tokens,
                             cache_write_tokens, cache_read_tokens, cost_usd)
                          VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                          ON CONFLICT(org_id, day, model, source) DO UPDATE SET
                            calls = calls + 1,
                            input_tokens = input_tokens + excluded.input_tokens,
                            output_tokens = output_tokens + excluded.output_tokens,
                            cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
                            cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,
                            cost_usd = cost_usd + excluded.cost_usd""",
                       (org_id, day, model, source, t_in, t_out, c_write, c_read, cost))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception('Bot WhatsApp: no se pudo registrar el consumo')


def usage_summary(org_id=1):
    """Totales para el panel: hoy, ayer, mes en curso, proyección mensual y últimos 14 días."""
    import calendar as _cal
    from datetime import timedelta
    today = gcal.now().date()
    month_start = today.replace(day=1)
    db = get_db()
    try:
        _ensure_tables(db)
        rows = db.execute("""SELECT day, source, SUM(calls), SUM(input_tokens + cache_write_tokens + cache_read_tokens),
                                    SUM(output_tokens), SUM(cost_usd)
                             FROM ai_bot_usage WHERE org_id = ? AND day >= ?
                             GROUP BY day, source ORDER BY day DESC""",
                          (org_id, (today - timedelta(days=62)).isoformat())).fetchall()
        first = db.execute("SELECT MIN(day), SUM(cost_usd) FROM ai_bot_usage WHERE org_id = ?",
                           (org_id,)).fetchone()
        db.commit()
    finally:
        db.close()

    by_day = {}
    for day, source, calls, t_in, t_out, cost in rows:
        d = by_day.setdefault(day, {'day': day, 'calls': 0, 'tokens_in': 0, 'tokens_out': 0,
                                    'cost': 0.0, 'test_cost': 0.0})
        d['calls'] += calls or 0
        d['tokens_in'] += t_in or 0
        d['tokens_out'] += t_out or 0
        d['cost'] += cost or 0
        if source == 'prueba':
            d['test_cost'] += cost or 0

    def total(since, until=None):
        return sum(v['cost'] for k, v in by_day.items() if k >= since and (until is None or k <= until))

    month_cost = total(month_start.isoformat())
    tracking_since = first[0] if first and first[0] else None
    start_for_avg = max(month_start, datetime.strptime(tracking_since, '%Y-%m-%d').date()) \
        if tracking_since else today
    days_counted = (today - start_for_avg).days + 1
    days_in_month = _cal.monthrange(today.year, today.month)[1]
    daily_avg = month_cost / days_counted if days_counted else 0
    yesterday = (today - timedelta(days=1)).isoformat()
    return {
        'today': total(today.isoformat()),
        'yesterday': total(yesterday, yesterday),
        'month': month_cost,
        'month_label': f"{gcal.MESES[today.month - 1]} {today.year}",
        'daily_avg': daily_avg,
        'projection': daily_avg * days_in_month,
        'all_time': (first[1] or 0) if first else 0,
        'tracking_since': tracking_since,
        'days': [by_day[k] for k in sorted(by_day, reverse=True)][:14],
    }


def _bookings_get(org_id, phone):
    db = get_db()
    try:
        _ensure_tables(db)
        row = db.execute("SELECT event_id, start_at FROM ai_bot_bookings WHERE org_id = ? AND phone = ?",
                         (org_id, phone)).fetchone()
    finally:
        db.close()
    return row


def _bookings_set(org_id, phone, client_id, event_id, start_at):
    db = get_db()
    try:
        _ensure_tables(db)
        db.execute("""INSERT OR REPLACE INTO ai_bot_bookings (org_id, phone, client_id, event_id, start_at, updated_at)
                      VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                   (org_id, phone, client_id, event_id, start_at))
        db.commit()
    finally:
        db.close()


def _client_label(client_id, phone):
    if not client_id:
        return phone, ''
    db = get_db()
    try:
        row = db.execute("SELECT first_name, last_name, email FROM clients WHERE id = ?", (client_id,)).fetchone()
    finally:
        db.close()
    if not row:
        return phone, ''
    name = ' '.join(x for x in (row[0], row[1]) if x).strip() or phone
    return name, (row[2] or '')


def _tool_agendar_llamada(args, client_id, phone):
    org_id = 1
    motivo = (args.get('motivo') or '').strip()
    try:
        start = gcal.parse_local(args.get('fecha_hora') or '')
    except ValueError:
        return ('ERROR: fecha_hora no válida. Usa el formato YYYY-MM-DDTHH:MM (hora de Miami) '
                'y vuelve a llamar la herramienta.')

    def fmt(slots):
        return '; '.join(gcal.human(s) for s in slots) or 'sin huecos en los próximos días'

    try:
        previous = _bookings_get(org_id, phone) if phone != 'preview' else None
        ignore = None
        if previous:
            rng = gcal.get_event_range(previous[0], org_id)
            if rng and rng[0] > gcal.now():
                ignore = rng                      # su propia cita no cuenta como ocupada
            else:
                previous = None
        state, alternatives = gcal.check(start, org_id=org_id, ignore=ignore)
    except Exception as exc:
        log.exception('Bot WhatsApp: Google Calendar no disponible')
        _add_note(client_id, f'📞 Solicitud de llamada (bot WhatsApp, SIN agendar: calendario no disponible) — '
                             f'{gcal.human(start)}' + (f' | Motivo: {motivo}' if motivo else '') + f' | Tel: {phone}')
        return (f'NO_AGENDADA: el calendario no está disponible ({str(exc)[:80]}). Dile al cliente que '
                'Henrry le confirmará el horario personalmente.')

    if state == 'pasado':
        return f'NO_AGENDADA: esa fecha/hora ya pasó o es demasiado pronto. Horarios libres: {fmt(alternatives)}.'
    if state == 'fuera_horario':
        return (f'NO_AGENDADA: fuera del horario de llamadas (lunes a sábado, {gcal.WORK_START}:00 a '
                f'{gcal.WORK_END}:00, hora de Miami). Horarios libres: {fmt(alternatives)}.')
    if state == 'ocupado':
        return f'OCUPADO: Henrry ya tiene algo a esa hora. Horarios libres cercanos: {fmt(alternatives)}.'

    if phone == 'preview':
        return f'AGENDADA (simulación de prueba, no se creó evento): {gcal.human(start)}.'

    name, email = _client_label(client_id, phone)
    description = '\n'.join(x for x in (
        f'Cliente: {name}', f'WhatsApp: {phone}', f'Email: {email}' if email else '',
        f'Motivo: {motivo}' if motivo else '', 'Agendada por el asistente de WhatsApp del CRM.') if x)
    try:
        event_id, _link = gcal.create_or_move(start, f'📞 Llamada con {name} (PapiaTech)', description,
                                              event_id=previous[0] if previous else None, org_id=org_id)
    except Exception as exc:
        log.exception('Bot WhatsApp: no se pudo crear el evento')
        return f'NO_AGENDADA: error al crear el evento ({str(exc)[:80]}). Dile que Henrry le confirmará.'

    _bookings_set(org_id, phone, client_id, event_id, start.isoformat())
    verb = 'Reagendada' if previous else 'Agendada'
    _add_note(client_id, f'📅 {verb} llamada en Google Calendar (bot WhatsApp) — {gcal.human(start)}'
                         + (f' | Motivo: {motivo}' if motivo else '') + f' | Tel: {phone}')
    if client_id:
        try:
            from models.client import add_followup
            add_followup(client_id=client_id, method='phone', summary=f'Llamada con {name}', result='',
                         next_at=start.strftime('%Y-%m-%d %H:%M'), reminder_comment=motivo)
        except Exception:
            log.exception('Bot WhatsApp: no se pudo crear el seguimiento en el CRM')
        db = get_db()
        try:
            db.execute("UPDATE clients SET pipeline_stage = 'contacted' "
                       "WHERE id = ? AND pipeline_stage = 'new_lead'", (client_id,))
            db.commit()
        finally:
            db.close()
    extra = ' (se movió su cita anterior, no hay duplicado)' if previous else ''
    return f'AGENDADA: {gcal.human(start)} (hora de Miami), 30 minutos{extra}. Confírmale al cliente día y hora.'


def _fallback_after_tool(out):
    """Texto de respaldo si Claude no redacta respuesta después de usar una herramienta."""
    out = out or ''
    if 'NO_AGENDADA' in out or 'OCUPADO' in out:
        alt = ''
        if 'Horarios libres' in out:
            alt = out.split('Horarios libres', 1)[1].split(':', 1)[-1].strip().rstrip('.')
        return ('Ese horario no está disponible 😕.'
                + (f' Tengo libre: {alt}. ¿Cuál te va mejor?' if alt
                   else ' Henrry te escribirá para coordinar otro horario.'))
    if 'AGENDADA:' in out:
        when = out.split('AGENDADA:', 1)[1].split('(hora de Miami)')[0].strip()
        return f'¡Listo! ✅ Tu llamada con Henrry quedó agendada para el {when} (hora de Miami). 📞'
    if 'Datos guardados' in out or 'guardad' in out.lower():
        return '¡Gracias! Ya lo tengo anotado ✅ ¿En qué más te puedo ayudar?'
    return ''


def calendar_status(org_id=1):
    try:
        ok, detail = gcal.status(org_id)
    except Exception as exc:
        ok, detail = False, str(exc)[:200]
    return {'ok': ok, 'detail': detail, 'hours': f'{gcal.WORK_START}:00–{gcal.WORK_END}:00',
            'minutes': gcal.CALL_MINUTES}


TOOL_HANDLERS = {
    'guardar_datos_cliente': _tool_guardar_datos,
    'solicitar_llamada': _tool_solicitar_llamada,
    'agendar_llamada': _tool_agendar_llamada,
}


# ── Llamada a Claude ────────────────────────────────────────────────────────

def _call_claude(system, messages):
    resp = requests.post(
        API_URL,
        headers={
            'x-api-key': os.getenv('ANTHROPIC_API_KEY', ''),
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json={
            'model': os.getenv('AI_BOT_MODEL', DEFAULT_MODEL),
            'max_tokens': 500,
            'system': system,
            'tools': TOOLS,
            'messages': messages,
        },
        timeout=25,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'Claude API {resp.status_code}: {resp.text[:300]}')
    data = resp.json()
    _record_usage(data.get('model') or os.getenv('AI_BOT_MODEL', DEFAULT_MODEL), data.get('usage') or {})
    return data


def generate_reply(phone, client_id, org_id=1, messages=None):
    settings = get_settings(org_id)
    system = _system_prompt(settings, client_id)
    if messages is None:
        messages = _history(phone, org_id)
    if not messages:
        return ''

    last_tool_out = ''
    for _ in range(MAX_TOOL_ROUNDS):
        data = _call_claude(system, messages)
        content = data.get('content') or []
        if data.get('stop_reason') != 'tool_use':
            text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text').strip()
            if not text:
                log.warning('Bot WhatsApp: Claude no devolvió texto para %s (stop_reason=%s)', phone, data.get('stop_reason'))
                text = _fallback_after_tool(last_tool_out)
            return text

        messages.append({'role': 'assistant', 'content': content})
        results = []
        for block in content:
            if block.get('type') != 'tool_use':
                continue
            handler = TOOL_HANDLERS.get(block.get('name'))
            try:
                out = handler(block.get('input') or {}, client_id, phone) if handler else 'Herramienta desconocida.'
            except Exception as exc:  # no romper la conversación por un fallo de herramienta
                log.exception('Bot WhatsApp: error en herramienta %s', block.get('name'))
                out = f'Error: {exc}'
            results.append({'type': 'tool_result', 'tool_use_id': block.get('id'), 'content': out})
        last_tool_out = '\n'.join(str(r.get('content', '')) for r in results)
        messages.append({'role': 'user', 'content': results})
    log.warning('Bot WhatsApp: se agotaron las rondas de herramientas para %s', phone)
    return _fallback_after_tool(last_tool_out)


def handle_incoming(phone, client_id, org_id=1):
    """Punto de entrada desde el webhook. Nunca lanza excepciones."""
    try:
        if not os.getenv('ANTHROPIC_API_KEY'):
            return
        settings = get_settings(org_id)
        if not settings['enabled'] or is_disabled(phone, org_id) or is_paused(phone, org_id):
            return
        reply = generate_reply(phone, client_id, org_id)
        if not reply:
            return
        from routes.whatsapp import deliver, _record_outbound  # import tardío: evita ciclo
        _set_last_bot_text(phone, reply, org_id)
        wa_id = deliver(phone, reply, org_id)
        _record_outbound(phone, reply, wa_id, org_id, client_id)
    except Exception:
        log.exception('Bot WhatsApp: no se pudo responder a %s', phone)


def preview(text, org_id=1):
    """Prueba desde el CRM: responde a un mensaje sin enviar nada ni tocar clientes."""
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise RuntimeError('Falta ANTHROPIC_API_KEY en el .env')
    _usage_ctx.source = 'prueba'
    try:
        return generate_reply('preview', None, org_id, messages=[{'role': 'user', 'content': text}])
    finally:
        _usage_ctx.source = None


def paused_list(org_id=1):
    db = get_db()
    try:
        _ensure_tables(db)
        rows = db.execute("""SELECT phone, paused_until FROM ai_bot_state
                             WHERE org_id = ? AND paused_until > CURRENT_TIMESTAMP
                             ORDER BY paused_until DESC""", (org_id,)).fetchall()
        db.commit()
    finally:
        db.close()
    return [{'phone': r[0], 'paused_until': r[1]} for r in rows]
