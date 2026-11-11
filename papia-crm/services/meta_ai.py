"""
services/meta_ai.py
Asistente IA para Instagram Direct y Messenger.

Reutiliza el cerebro del bot de WhatsApp (services/ai_agent: prompt, herramientas,
seguridad, aprendizaje y registro de consumo) pero con el historial y el envío de Meta.

Reglas (iguales que en WhatsApp):
- Solo responde si el asistente está ENCENDIDO para ese canal (switch en el inbox).
- Se puede apagar por contacto.
- Si Henrry responde a mano (desde el CRM o desde la app de Instagram), la IA se
  pausa en esa conversación durante las horas configuradas en el bot de WhatsApp.
"""
import logging
import os

from database import get_db
from services import ai_agent
from services.meta_send import send_message

log = logging.getLogger(__name__)

HISTORY_LIMIT = 20
MAX_CHUNK = 950          # Instagram corta los mensajes de más de 1000 caracteres
CHANNEL_LABELS = {'instagram': 'Instagram', 'messenger': 'Messenger'}


# ── Tablas ────────────────────────────────────────────────────────────────────

def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS meta_ai_settings (
        org_id     INTEGER NOT NULL,
        channel    TEXT NOT NULL,
        enabled    INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (org_id, channel))""")
    db.execute("""CREATE TABLE IF NOT EXISTS meta_ai_contacts (
        org_id        INTEGER NOT NULL,
        channel       TEXT NOT NULL,
        sender_id     TEXT NOT NULL,
        disabled      INTEGER NOT NULL DEFAULT 0,
        paused_until  TIMESTAMP,
        last_bot_text TEXT,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (org_id, channel, sender_id))""")
    scols = {r[1] for r in db.execute("PRAGMA table_info(meta_ai_settings)").fetchall()}
    if 'instructions' not in scols:
        db.execute("ALTER TABLE meta_ai_settings ADD COLUMN instructions TEXT")
    cols = {r[1] for r in db.execute("PRAGMA table_info(meta_messages)").fetchall()}
    if 'sent_by' not in cols:
        db.execute("ALTER TABLE meta_messages ADD COLUMN sent_by TEXT")


def _db():
    db = get_db()
    ensure_tables(db)
    return db


# ── Switch por canal ──────────────────────────────────────────────────────────

def is_channel_enabled(org_id, channel):
    db = _db()
    try:
        row = db.execute("SELECT enabled FROM meta_ai_settings WHERE org_id=? AND channel=?",
                         (org_id, channel)).fetchone()
        db.commit()
    finally:
        db.close()
    return bool(row and row[0])


def set_channel_enabled(org_id, channel, enabled):
    db = _db()
    try:
        db.execute("""INSERT INTO meta_ai_settings (org_id, channel, enabled, updated_at)
                      VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                      ON CONFLICT(org_id, channel) DO UPDATE SET
                        enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP""",
                   (org_id, channel, 1 if enabled else 0))
        db.commit()
    finally:
        db.close()


MAX_INSTRUCTIONS = 4000


def get_instructions(org_id, channel):
    db = _db()
    try:
        row = db.execute("SELECT instructions FROM meta_ai_settings WHERE org_id=? AND channel=?",
                         (org_id, channel)).fetchone()
        db.commit()
    finally:
        db.close()
    return (row['instructions'] if row else '') or ''


def set_instructions(org_id, channel, text):
    text = (text or '').strip()[:MAX_INSTRUCTIONS]
    db = _db()
    try:
        db.execute("""INSERT INTO meta_ai_settings (org_id, channel, enabled, instructions, updated_at)
                      VALUES (?, ?, 0, ?, CURRENT_TIMESTAMP)
                      ON CONFLICT(org_id, channel) DO UPDATE SET
                        instructions=excluded.instructions, updated_at=CURRENT_TIMESTAMP""",
                   (org_id, channel, text))
        db.commit()
    finally:
        db.close()
    return text


# ── Estado por contacto ───────────────────────────────────────────────────────

def contact_state(org_id, channel, sender_id):
    db = _db()
    try:
        row = db.execute("""SELECT disabled, paused_until,
                                   CASE WHEN paused_until IS NOT NULL
                                         AND paused_until > CURRENT_TIMESTAMP THEN 1 ELSE 0 END AS paused
                            FROM meta_ai_contacts
                            WHERE org_id=? AND channel=? AND sender_id=?""",
                         (org_id, channel, sender_id)).fetchone()
        db.commit()
    finally:
        db.close()
    if not row:
        return {'disabled': False, 'paused': False, 'paused_until': None}
    return {'disabled': bool(row['disabled']), 'paused': bool(row['paused']),
            'paused_until': row['paused_until'] if row['paused'] else None}


def _upsert_contact(db, org_id, channel, sender_id):
    db.execute("INSERT OR IGNORE INTO meta_ai_contacts (org_id, channel, sender_id) VALUES (?, ?, ?)",
               (org_id, channel, sender_id))


def set_contact_enabled(org_id, channel, sender_id, enabled):
    """Activa/desactiva la IA para un contacto. Al activarla también quita la pausa."""
    db = _db()
    try:
        _upsert_contact(db, org_id, channel, sender_id)
        if enabled:
            db.execute("""UPDATE meta_ai_contacts SET disabled=0, paused_until=NULL,
                          updated_at=CURRENT_TIMESTAMP
                          WHERE org_id=? AND channel=? AND sender_id=?""",
                       (org_id, channel, sender_id))
        else:
            db.execute("""UPDATE meta_ai_contacts SET disabled=1, updated_at=CURRENT_TIMESTAMP
                          WHERE org_id=? AND channel=? AND sender_id=?""",
                       (org_id, channel, sender_id))
        db.commit()
    finally:
        db.close()


def pause(org_id, channel, sender_id, hours=None):
    """Un humano intervino: la IA deja de responder en esta conversación por un tiempo."""
    if hours is None:
        try:
            hours = int(ai_agent.get_settings(org_id)['pause_hours'] or 24)
        except Exception:
            hours = 24
    db = _db()
    try:
        _upsert_contact(db, org_id, channel, sender_id)
        db.execute("""UPDATE meta_ai_contacts SET paused_until=datetime('now', ?),
                      updated_at=CURRENT_TIMESTAMP
                      WHERE org_id=? AND channel=? AND sender_id=?""",
                   (f'+{int(hours)} hours', org_id, channel, sender_id))
        db.commit()
    finally:
        db.close()


def is_bot_echo(org_id, channel, sender_id, text):
    """True si el eco que manda Meta es un mensaje que escribió la propia IA."""
    text = (text or '').strip()
    if not text:
        return True
    db = _db()
    try:
        row = db.execute("""SELECT last_bot_text FROM meta_ai_contacts
                            WHERE org_id=? AND channel=? AND sender_id=?""",
                         (org_id, channel, sender_id)).fetchone()
        db.commit()
    finally:
        db.close()
    last = (row['last_bot_text'] if row else '') or ''
    return bool(last) and (text in last or text == ai_agent.CLOSE_MESSAGE)


def _set_last_bot_text(org_id, channel, sender_id, text):
    db = _db()
    try:
        _upsert_contact(db, org_id, channel, sender_id)
        db.execute("""UPDATE meta_ai_contacts SET last_bot_text=?, updated_at=CURRENT_TIMESTAMP
                      WHERE org_id=? AND channel=? AND sender_id=?""",
                   (text, org_id, channel, sender_id))
        db.commit()
    finally:
        db.close()


# ── Historial, cliente y envío ────────────────────────────────────────────────

def _history(org_id, channel, sender_id):
    db = get_db()
    try:
        rows = db.execute("""SELECT direction, message FROM meta_messages
                             WHERE org_id=? AND channel=? AND sender_id=?
                             ORDER BY id DESC LIMIT ?""",
                          (org_id, channel, sender_id, HISTORY_LIMIT)).fetchall()
    finally:
        db.close()
    msgs = []
    for row in reversed(rows):
        role = 'user' if row['direction'] == 'inbound' else 'assistant'
        text = (row['message'] or '').strip() or '[mensaje sin texto]'
        if msgs and msgs[-1]['role'] == role:
            msgs[-1]['content'] += '\n' + text
        else:
            msgs.append({'role': role, 'content': text})
    while msgs and msgs[0]['role'] != 'user':
        msgs.pop(0)
    return msgs


def _recent_inbound(org_id, channel, sender_id, seconds=60):
    db = get_db()
    try:
        row = db.execute("""SELECT COUNT(*) FROM meta_messages
                            WHERE org_id=? AND channel=? AND sender_id=? AND direction='inbound'
                              AND created_at > datetime('now', ?)""",
                         (org_id, channel, sender_id, f'-{int(seconds)} seconds')).fetchone()
    finally:
        db.close()
    return row[0] if row else 0


def _ensure_client(org_id, channel, page_id, sender_id, first_message=''):
    """Devuelve el cliente de esta conversación; si no hay, lo crea como lead nuevo."""
    label = CHANNEL_LABELS.get(channel, channel.capitalize())
    db = get_db()
    try:
        row = db.execute("""SELECT client_id FROM meta_messages
                            WHERE org_id=? AND channel=? AND sender_id=? AND client_id IS NOT NULL
                            ORDER BY id DESC LIMIT 1""",
                         (org_id, channel, sender_id)).fetchone()
        if row:
            return row['client_id']
        prof = None
        try:
            prof = db.execute("""SELECT username, name FROM meta_contacts
                                 WHERE channel=? AND page_id=? AND sender_id=?""",
                              (channel, page_id, sender_id)).fetchone()
        except Exception:
            prof = None
        username = (prof['username'] if prof else '') or ''
        name = ((prof['name'] if prof else '') or '').strip()
        if name:
            parts = name.split(' ', 1)
            first, last = parts[0], (parts[1] if len(parts) > 1 else '')
        elif username:
            first, last = '@' + username, ''
        else:
            first, last = label, sender_id
        cur = db.execute("""
            INSERT INTO clients
                (first_name, last_name, email, phone, company, project_type,
                 project_details, pipeline_stage, source, total_cost, amount_paid, org_id)
            VALUES (?, ?, '', '', '', 'other', ?, 'new_lead', ?, 0, 0, ?)
        """, (first, last, (first_message or '')[:500], channel, org_id))
        client_id = cur.lastrowid
        handle = f' (@{username})' if username else ''
        note = f'Lead creado automáticamente desde {label}{handle}'
        if first_message:
            note += f' | Primer mensaje: {first_message[:300]}'
        db.execute("INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
                   (client_id, note))
        db.execute("""UPDATE meta_messages SET client_id=?
                      WHERE org_id=? AND channel=? AND sender_id=? AND client_id IS NULL""",
                   (client_id, org_id, channel, sender_id))
        db.commit()
        return client_id
    finally:
        db.close()


def _chunks(text):
    text = (text or '').strip()
    if len(text) <= MAX_CHUNK:
        return [text] if text else []
    out, cur = [], ''
    for para in text.split('\n'):
        while len(para) > MAX_CHUNK:
            cut = para.rfind(' ', 0, MAX_CHUNK)
            cut = cut if cut > 200 else MAX_CHUNK
            if cur:
                out.append(cur)
                cur = ''
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if len(cur) + len(para) + 1 > MAX_CHUNK:
            out.append(cur)
            cur = para
        else:
            cur = (cur + '\n' + para) if cur else para
    if cur:
        out.append(cur)
    return [c for c in out if c]


def _deliver(org_id, channel, page_id, sender_id, text, token, client_id):
    _set_last_bot_text(org_id, channel, sender_id, text)
    for part in _chunks(text):
        result = send_message(token, sender_id, part) or {}
        db = _db()
        try:
            db.execute("""INSERT INTO meta_messages
                            (org_id, channel, sender_id, page_id, direction, message,
                             meta_message_id, client_id, sent_by)
                          VALUES (?, ?, ?, ?, 'outbound', ?, ?, ?, 'ai')""",
                       (org_id, channel, sender_id, page_id, part,
                        result.get('message_id'), client_id))
            db.commit()
        finally:
            db.close()


def _shutdown(org_id, channel, page_id, sender_id, token, client_id, reason, text):
    label = CHANNEL_LABELS.get(channel, channel)
    log.warning('IA %s: chat %s cerrado por seguridad (%s)', label, sender_id, reason)
    try:
        set_contact_enabled(org_id, channel, sender_id, False)
    except Exception:
        log.exception('IA %s: no se pudo desactivar para %s', label, sender_id)
    if client_id:
        ai_agent._add_note(client_id, f'⚠️ Seguridad — chat de {label} cerrado por la IA. Motivo: {reason}. '
                                      f'Mensaje recibido: "{(text or "")[:200]}". '
                                      f'Reactívala desde el inbox de {label} si fue un error.')
    try:
        _deliver(org_id, channel, page_id, sender_id, ai_agent.CLOSE_MESSAGE, token, client_id)
    except Exception:
        log.exception('IA %s: no se pudo enviar el cierre a %s', label, sender_id)


def _generate(org_id, channel, sender_id, client_id):
    """Llama al cerebro de WhatsApp adaptando el prompt al canal."""
    label = CHANNEL_LABELS.get(channel, channel)
    original_prompt = ai_agent._system_prompt
    try:
        custom = get_instructions(org_id, channel)
    except Exception:
        custom = ''

    def channel_prompt(settings, cid, org=1):
        base = original_prompt(settings, cid, org).replace('WhatsApp', label)
        return base + (f'\n\nIMPORTANTE: esta conversación es por mensaje directo de {label}, no por WhatsApp. '
                       'No tienes el número de teléfono de la persona: si hace falta para agendar o '
                       'devolver una llamada, pídeselo de forma natural.') + (
            f'\n\nINSTRUCCIONES ESPECÍFICAS PARA {label.upper()} (tienen prioridad sobre todo lo '
            f'anterior si hay conflicto, pero nunca rompen las reglas de seguridad ni la de no dar precios):\n{custom}'
            if custom else '')

    ctx = getattr(ai_agent, '_usage_ctx', None)
    prev_source = getattr(ctx, 'source', None) if ctx is not None else None
    ai_agent._system_prompt = channel_prompt
    try:
        if ctx is not None:
            ctx.source = channel
        return ai_agent.generate_reply(f'{channel}:{sender_id}', client_id, org_id,
                                       messages=_history(org_id, channel, sender_id))
    finally:
        ai_agent._system_prompt = original_prompt
        if ctx is not None:
            ctx.source = prev_source


# ── Punto de entrada desde el webhook ─────────────────────────────────────────

def handle_incoming(org_id, channel, page_id, sender_id, text, token):
    """Responde automáticamente si está habilitado. Nunca lanza excepciones."""
    try:
        if not os.getenv('ANTHROPIC_API_KEY') or not token:
            return
        if not is_channel_enabled(org_id, channel):
            return
        state = contact_state(org_id, channel, sender_id)
        if state['disabled'] or state['paused']:
            return
        suspicious, reason = ai_agent.security_check(text)
        if not suspicious and _recent_inbound(org_id, channel, sender_id) > ai_agent.FLOOD_LIMIT:
            suspicious, reason = True, f'flood (más de {ai_agent.FLOOD_LIMIT} mensajes por minuto)'
        client_id = _ensure_client(org_id, channel, page_id, sender_id, text)
        if suspicious:
            _shutdown(org_id, channel, page_id, sender_id, token, client_id, reason, text)
            return
        try:
            reply = _generate(org_id, channel, sender_id, client_id)
        except ai_agent.AbuseDetected as exc:
            _shutdown(org_id, channel, page_id, sender_id, token, client_id, str(exc), text)
            return
        if not reply:
            return
        _deliver(org_id, channel, page_id, sender_id, reply, token, client_id)
        try:
            from services import ai_learning
            ai_learning.mark_bot_message(reply, org_id)
        except Exception:
            pass
    except Exception:
        log.exception('IA %s: no se pudo responder a %s', channel, sender_id)
