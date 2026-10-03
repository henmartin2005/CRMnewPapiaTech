"""
Aprendizaje del asistente de WhatsApp a partir de las conversaciones reales del dueño del negocio
(nombre en instance/brand.json → owner_name).

- Lee los chats de WhatsApp guardados en el CRM y separa lo que escribió el dueño (desde el CRM
  o desde su celular) de lo que respondió el bot.
- Le pide a Claude que extraiga: cómo escribe el dueño, qué explica, cómo maneja objeciones y
  cómo cierra, más ejemplos reales (cliente → dueño) con los precios tachados.
- El resultado se guarda en ai_bot_learning y se inyecta en el prompt del bot.
- Se vuelve a aprender solo cada RELEARN_DAYS días (o con el botón del panel).

Los precios que el dueño menciona se guardan aparte SOLO como referencia interna: nunca entran
al prompt del bot ni se envían al cliente.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta

import requests

from database import get_db
import instance_config as cfg

log = logging.getLogger(__name__)

API_URL = 'https://api.anthropic.com/v1/messages'
LEARN_MODEL_DEFAULT = 'claude-sonnet-4-5'
MAX_CONVERSATIONS = 80        # chats más recientes que se analizan
MAX_MSGS_PER_CONV = 40        # últimos mensajes por chat
MAX_MSG_CHARS = 600           # recorte por mensaje
MAX_TOTAL_CHARS = 140000      # tope del material enviado a Claude
RELEARN_DAYS = 7
BOT_REPLY_SECONDS = 40       # mensajes sin marcar enviados tan rápido se asumen del bot
RUNNING_STALE_MIN = 15        # un 'running' más viejo que esto se considera colgado

PRICE_RE = re.compile(
    r'(?:US)?\$\s?\d[\d.,]*\d(?:\s?(?:k|K|mil)\b)?|(?:US)?\$\s?\d(?:\s?(?:k|K|mil)\b)?'
    r'|\b\d[\d.,]*(?:\s?(?:k|K|mil))?\s?(?:d[oó]lares|dolares|usd|USD|bucks|pesos)\b',
)

_thread_lock = threading.Lock()


# ── Persistencia ────────────────────────────────────────────────────────────

def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_bot_learning (
        org_id INTEGER PRIMARY KEY,
        status TEXT,
        style_guide TEXT,
        examples TEXT,
        price_notes TEXT,
        stats TEXT,
        error TEXT,
        learned_at TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    cols = {r[1] for r in db.execute("PRAGMA table_info(whatsapp_messages)").fetchall()}
    if cols and 'sent_by' not in cols:
        db.execute("ALTER TABLE whatsapp_messages ADD COLUMN sent_by TEXT")


def _row_to_dict(row):
    if not row:
        return {'status': 'never', 'style_guide': '', 'examples': [], 'price_notes': '',
                'stats': {}, 'error': '', 'learned_at': None, 'updated_at': None}
    try:
        examples = json.loads(row[2] or '[]')
    except ValueError:
        examples = []
    try:
        stats = json.loads(row[4] or '{}')
    except ValueError:
        stats = {}
    return {'status': row[0] or 'never', 'style_guide': row[1] or '', 'examples': examples,
            'price_notes': row[3] or '', 'stats': stats, 'error': row[5] or '',
            'learned_at': row[6], 'updated_at': row[7]}


def get_learning(org_id=1):
    db = get_db()
    try:
        ensure_tables(db)
        row = db.execute("""SELECT status, style_guide, examples, price_notes, stats, error,
                                   learned_at, updated_at
                            FROM ai_bot_learning WHERE org_id = ?""", (org_id,)).fetchone()
        db.commit()
    finally:
        db.close()
    data = _row_to_dict(row)
    # Un proceso que quedó colgado (reinicio del servidor, etc.) no bloquea para siempre
    if data['status'] == 'running' and data['updated_at']:
        try:
            upd = datetime.strptime(str(data['updated_at'])[:19], '%Y-%m-%d %H:%M:%S')
            if datetime.utcnow() - upd > timedelta(minutes=RUNNING_STALE_MIN):
                data['status'] = 'error'
                data['error'] = data['error'] or 'El aprendizaje anterior no terminó. Vuelve a intentarlo.'
        except ValueError:
            pass
    return data


def _save(org_id, **fields):
    db = get_db()
    try:
        ensure_tables(db)
        db.execute("INSERT OR IGNORE INTO ai_bot_learning (org_id, status) VALUES (?, 'never')", (org_id,))
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f'{k} = ?')
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
        sets.append('updated_at = CURRENT_TIMESTAMP')
        db.execute(f"UPDATE ai_bot_learning SET {', '.join(sets)} WHERE org_id = ?", (*vals, org_id))
        db.commit()
    finally:
        db.close()


def save_style_guide(org_id, text):
    """El dueño corrige a mano lo aprendido desde el panel."""
    _save(org_id, style_guide=(text or '').strip())


def mark_bot_message(text, org_id=1):
    """Marca como 'bot' el último mensaje saliente con ese texto (para no aprender del bot)."""
    try:
        db = get_db()
        try:
            ensure_tables(db)
            db.execute("""UPDATE whatsapp_messages SET sent_by = 'bot'
                          WHERE id = (SELECT id FROM whatsapp_messages
                                      WHERE org_id = ? AND direction = 'outbound' AND message = ?
                                      ORDER BY id DESC LIMIT 1)""", (org_id, text))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception('Aprendizaje: no se pudo marcar el mensaje del bot')


# ── Utilidades ──────────────────────────────────────────────────────────────

def strip_prices(text):
    return PRICE_RE.sub('[precio]', text or '')


def has_price(text):
    return bool(PRICE_RE.search(text or ''))


def _looks_like_bot(text):
    """Mensajes viejos del bot (antes de marcarlos): hablan del dueño en tercera persona."""
    t = (text or '').lower()
    owner = re.escape((cfg.brand().get('owner_first_name') or '').lower().strip())
    return bool((owner and re.search(r'(?<!soy )\b' + owner + r'\b', t))   # el dueño no habla de sí mismo en tercera persona
                or 'asistente' in t or 'no puedo escuchar audios' in t)


def _ts(value):
    try:
        return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


# ── Recolección de conversaciones ───────────────────────────────────────────

def _collect(org_id):
    db = get_db()
    try:
        ensure_tables(db)
        phones = db.execute(
            """SELECT phone, MAX(id) AS last_id FROM whatsapp_messages
               WHERE org_id = ?
               GROUP BY phone
               HAVING SUM(CASE WHEN direction = 'outbound' AND COALESCE(sent_by, '') <> 'bot'
                               THEN 1 ELSE 0 END) > 0
               ORDER BY last_id DESC LIMIT ?""", (org_id, MAX_CONVERSATIONS)).fetchall()
        convs, total, mine, used = [], 0, 0, 0
        for phone, _ in phones:
            rows = db.execute(
                """SELECT direction, message, COALESCE(sent_by, ''), created_at FROM whatsapp_messages
                   WHERE org_id = ? AND phone = ? ORDER BY id DESC LIMIT ?""",
                (org_id, phone, MAX_MSGS_PER_CONV)).fetchall()
            lines, has_mine, last_in = [], False, None
            for direction, msg, sent_by, created in reversed(rows):
                msg = re.sub(r'\s+', ' ', (msg or '').strip())[:MAX_MSG_CHARS]
                if not msg:
                    continue
                ts = _ts(created)
                # respuesta en menos de BOT_REPLY_SECONDS tras un mensaje del cliente = bot (historial sin marcar)
                too_fast = bool(ts and last_in and 0 <= (ts - last_in).total_seconds() <= BOT_REPLY_SECONDS)
                if direction == 'inbound':
                    who = 'CLIENTE'
                    last_in = ts
                elif sent_by == 'bot' or (not sent_by and too_fast) or _looks_like_bot(msg):
                    who = 'BOT'
                else:
                    who = 'DUEÑO'
                    has_mine = True
                    mine += 1
                lines.append(f'{who}: {msg}')
            if not has_mine:
                continue
            block = '\n'.join(lines)
            if total + len(block) > MAX_TOTAL_CHARS:
                break
            used += 1
            total += len(block)
            convs.append(f'### Conversación {used}\n{block}')
    finally:
        db.close()
    return convs, {'conversaciones': used, 'mensajes_dueno': mine, 'caracteres': total}


# ── Análisis con Claude ─────────────────────────────────────────────────────

def analysis_system():
    b = cfg.brand()
    return f"""Analizas conversaciones reales de WhatsApp entre {b['owner_name']} ({b['owner_role']} de {b['legal_name']}, {b['city']}) y sus clientes o prospectos.
Las líneas DUEÑO las escribió {b['owner_first_name']}. Las líneas BOT son de un asistente automático: IGNÓRALAS para el estilo y el conocimiento.
Tu trabajo es destilar cómo vende y cómo escribe {b['owner_first_name']} para que otro asistente pueda responder exactamente como él o ella.
Reglas:
- Usa solo lo que aparece en las conversaciones. No inventes servicios, plazos ni políticas.
- En guia_estilo, conocimiento, objeciones y ejemplos NO incluyas ningún precio, monto ni rango: reemplázalos por [precio].
- Los precios solo pueden aparecer en precios_internos.
- Responde ÚNICAMENTE con un objeto JSON válido, sin texto antes ni después."""


ANALYSIS_USER = """Estas son las conversaciones ({n} chats). Devuelve este JSON:
{{
  "guia_estilo": "Viñetas (- ...) concretas sobre cómo escribe el DUEÑO: saludo típico, tuteo o usted, tono, longitud de los mensajes, si manda varios mensajes cortos, uso de emojis (cuáles), signos de puntuación, muletillas y frases exactas que repite, cómo pide datos, cómo propone la llamada, cómo cierra y cómo hace seguimiento. Cita frases textuales entre comillas.",
  "conocimiento": "Viñetas con lo que el DUEÑO explica a los clientes: servicios, qué incluye cada uno, su proceso de trabajo, tiempos que menciona, formas de pago, preguntas frecuentes y cómo las responde. Sin montos.",
  "objeciones": "Viñetas 'objeción → cómo responde el DUEÑO' (precio, lo voy a pensar, ya tengo web, no tengo tiempo, etc.). Solo las que aparezcan.",
  "ejemplos": [{{"cliente": "mensaje real del cliente", "respuesta": "respuesta real del DUEÑO"}}],
  "precios_internos": "Resumen de los precios o rangos que el DUEÑO ha mencionado y para qué servicio (referencia interna).",
  "observaciones": "Una o dos líneas sobre qué tan representativa es la muestra."
}}
Para "ejemplos" elige de 8 a 12 intercambios reales y variados que muestren bien su estilo y su forma de cerrar (sin precios).

{convs}"""


def _parse_json(text):
    text = (text or '').strip()
    text = re.sub(r'^\x60{3}(?:json)?\s*|\s*\x60{3}$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < 0:
        raise ValueError('Claude no devolvió JSON')
    return json.loads(text[start:end + 1])


def _as_text(value):
    if isinstance(value, list):
        return '\n'.join(v if str(v).lstrip().startswith('-') else f'- {v}' for v in map(str, value))
    return str(value or '').strip()


def _run(org_id):
    try:
        convs, stats = _collect(org_id)
        if not convs:
            _save(org_id, status='error',
                  error='Todavía no hay conversaciones con mensajes escritos por ti para aprender.')
            return
        model = os.getenv('AI_LEARN_MODEL', LEARN_MODEL_DEFAULT)
        resp = requests.post(
            API_URL,
            headers={'x-api-key': os.getenv('ANTHROPIC_API_KEY', ''),
                     'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': model, 'max_tokens': 4000, 'system': analysis_system(),
                  'messages': [{'role': 'user', 'content': ANALYSIS_USER.format(
                      n=len(convs), convs='\n\n'.join(convs))}]},
            timeout=180,
        )
        if resp.status_code != 200:
            raise RuntimeError(f'Claude API {resp.status_code}: {resp.text[:300]}')
        data = resp.json()
        try:
            from services import ai_agent
            ai_agent._usage_ctx.source = 'aprendizaje'
            ai_agent._record_usage(data.get('model') or model, data.get('usage') or {}, org_id)
        except Exception:
            log.exception('Aprendizaje: no se pudo registrar el consumo')
        finally:
            try:
                ai_agent._usage_ctx.source = None
            except Exception:
                pass
        text = '\n'.join(b.get('text', '') for b in data.get('content') or [] if b.get('type') == 'text')
        result = _parse_json(text)

        sections = []
        for title, key in (('ESTILO DEL DUEÑO', 'guia_estilo'), ('LO QUE EXPLICA A LOS CLIENTES', 'conocimiento'),
                           ('CÓMO RESPONDE OBJECIONES', 'objeciones')):
            body = strip_prices(_as_text(result.get(key)))
            if body:
                sections.append(f'{title}:\n{body}')
        examples = []
        for ex in result.get('ejemplos') or []:
            if not isinstance(ex, dict):
                continue
            c, h = strip_prices(str(ex.get('cliente') or '')).strip(), strip_prices(str(ex.get('respuesta') or ex.get('henrry') or '')).strip()
            if c and h and not _looks_like_bot(h):   # nunca imitar mensajes del bot
                examples.append({'cliente': c[:400], 'respuesta': h[:400]})
        stats['observaciones'] = str(result.get('observaciones') or '')[:400]
        stats['modelo'] = data.get('model') or model
        _save(org_id, status='ready', style_guide='\n\n'.join(sections), examples=examples[:12],
              price_notes=_as_text(result.get('precios_internos'))[:3000], stats=stats, error='',
              learned_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
        log.info('Aprendizaje listo: %s', stats)
    except Exception as exc:
        log.exception('Aprendizaje: falló el análisis')
        _save(org_id, status='error', error=str(exc)[:300])


def start_learning(org_id=1):
    """Lanza el análisis en segundo plano. Devuelve False si ya hay uno en curso."""
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise RuntimeError('Falta ANTHROPIC_API_KEY en el .env')
    with _thread_lock:
        if get_learning(org_id)['status'] == 'running':
            return False
        _save(org_id, status='running', error='')
    threading.Thread(target=_run, args=(org_id,), daemon=True).start()
    return True


def maybe_relearn(org_id=1):
    """Re-aprende solo si nunca se hizo o si pasaron RELEARN_DAYS días. Barato: solo consulta la BD."""
    try:
        data = get_learning(org_id)
        if data['status'] == 'running':
            return
        if data['learned_at']:
            last = datetime.strptime(str(data['learned_at'])[:19], '%Y-%m-%d %H:%M:%S')
            if datetime.utcnow() - last < timedelta(days=RELEARN_DAYS):
                return
        elif data['status'] == 'error' and data['updated_at']:
            # no reintentar en bucle si falló hace poco
            upd = datetime.strptime(str(data['updated_at'])[:19], '%Y-%m-%d %H:%M:%S')
            if datetime.utcnow() - upd < timedelta(hours=12):
                return
        start_learning(org_id)
    except Exception:
        log.exception('Aprendizaje: maybe_relearn falló')


def prompt_block(org_id=1):
    """Texto que se inyecta en el prompt del bot (sin precios)."""
    try:
        data = get_learning(org_id)
    except Exception:
        return ''
    guide = strip_prices(data.get('style_guide') or '').strip()
    examples = data.get('examples') or []
    if not guide and not examples:
        return ''
    owner = cfg.brand().get('owner_first_name') or 'el dueño'
    parts = [f'LO QUE APRENDISTE DE LAS CONVERSACIONES REALES DE {owner.upper()}. Imita al detalle su forma de escribir '
             '(tono, expresiones, emojis, longitud). La estrategia de venta, los precios y las reglas las manda '
             'la sección TU MISIÓN y las REGLAS, aunque aquí diga otra cosa:', guide]
    if examples:
        parts.append(f'EJEMPLOS REALES (cliente → {owner}). Copia el tono, la longitud y las expresiones; '
                     'no copies datos concretos de otros clientes:')
        for ex in examples[:12]:
            parts.append(f"- Cliente: {strip_prices(ex.get('cliente'))}\n  {owner}: {strip_prices(ex.get('respuesta') or ex.get('henrry'))}")
    return '\n'.join(p for p in parts if p)
