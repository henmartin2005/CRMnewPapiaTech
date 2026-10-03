import json
import logging
import os
import mimetypes
import re
import shutil
import subprocess
import tempfile
import uuid
from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash, g, send_file, abort
from database import get_db
from services import zernio
from services import ai_agent

log = logging.getLogger(__name__)

whatsapp_bp = Blueprint('whatsapp', __name__)

# ── Phone normalization ──────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """Strip whatsapp: prefix and whitespace. Keep leading +."""
    phone = raw.strip().replace('whatsapp:', '').strip()
    return phone


def phones_match(p1: str, p2: str) -> bool:
    """Compare only digits; handle stored numbers with spaces/dashes."""
    d1 = re.sub(r'\D', '', p1)
    d2 = re.sub(r'\D', '', p2)
    # Compare last 10 digits to avoid country-code mismatches
    return d1[-10:] == d2[-10:] if len(d1) >= 10 and len(d2) >= 10 else d1 == d2


# ── DB helpers ───────────────────────────────────────────────────────────────

def find_client_by_phone(phone: str, org_id=1):
    db = get_db()
    clients = db.execute(
        "SELECT * FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
        (org_id,)
    ).fetchall()
    db.close()
    for c in clients:
        if phones_match(phone, c['phone']):
            return c
    return None


def _split_profile_name(profile_name: str, phone: str):
    """Turn Twilio's ProfileName into (first_name, last_name)."""
    name = (profile_name or '').strip()
    if not name:
        return 'WhatsApp', phone
    parts = name.split(' ', 1)
    return parts[0], (parts[1] if len(parts) > 1 else '')


def get_or_create_client_from_whatsapp(phone: str, profile_name: str = '',
                                       first_message: str = '', org_id: int = 1):
    """
    Find the client for this phone or create it as a new lead in the pipeline.

    Runs inside a BEGIN IMMEDIATE transaction so two messages arriving at the
    same time from an unknown number can't create duplicate leads.
    Returns (client_id, created).
    """
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT id, phone FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
            (org_id,)
        ).fetchall()
        for r in rows:
            if phones_match(phone, r['phone']):
                db.execute("COMMIT")
                return r['id'], False

        first_name, last_name = _split_profile_name(profile_name, phone)
        cur = db.execute("""
            INSERT INTO clients
                (first_name, last_name, email, phone, company, project_type,
                 project_details, pipeline_stage, source, total_cost, amount_paid, org_id)
            VALUES (?, ?, '', ?, '', 'other', ?, 'new_lead', 'whatsapp', 0, 0, ?)
        """, (first_name, last_name, phone, first_message[:500], org_id))
        client_id = cur.lastrowid

        note = 'Lead creado automáticamente desde WhatsApp'
        if first_message:
            note += f' | Primer mensaje: {first_message[:300]}'
        db.execute(
            "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'whatsapp_lead', ?)",
            (client_id, note),
        )

        # Link any earlier orphan messages from this number to the new client
        db.execute(
            "UPDATE whatsapp_messages SET client_id=? WHERE phone=? AND org_id=? AND client_id IS NULL",
            (client_id, phone, org_id),
        )
        db.execute("COMMIT")
        return client_id, True
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        db.close()


def _resolve_client_id(phone: str, name: str = '', first_message: str = '', org_id: int = 1):
    """Existing client id, or a new lead auto-created in the pipeline. Never raises."""
    try:
        client_id, _created = get_or_create_client_from_whatsapp(
            phone, profile_name=name, first_message=first_message, org_id=org_id)
        return client_id
    except Exception:
        log.exception('WhatsApp: no se pudo auto-crear el lead para %s', phone)
        client = find_client_by_phone(phone, org_id)
        return client['id'] if client else None


def save_message(phone, direction, message, wa_message_id=None, client_id=None,
                 status=None, org_id=1, media_path=None, media_type=None, media_mime=None):
    if status is None:
        status = 'received' if direction == 'inbound' else 'sent'
    _ensure_media_columns()
    db = get_db()
    db.execute(
        """INSERT INTO whatsapp_messages
               (client_id, phone, direction, message, status, wa_message_id, org_id,
                media_path, media_type, media_mime)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (client_id, phone, direction, message, status, wa_message_id, org_id,
         media_path, media_type, media_mime),
    )
    db.commit()
    db.close()
    if client_id:
        try:
            save_client_note(int(client_id), phone=phone)
        except Exception:
            pass


def get_conversations(org_id=1):
    """Return one row per unique phone: last message + unread count."""
    db = get_db()
    rows = db.execute("""
        SELECT
            w.phone,
            w.message          AS last_message,
            w.direction        AS last_direction,
            w.status           AS last_status,
            w.created_at       AS last_at,
            w.client_id,
            c.first_name, c.last_name,
            COUNT(CASE WHEN w2.direction='inbound' AND w2.status='received' THEN 1 END) AS unread
        FROM whatsapp_messages w
        LEFT JOIN whatsapp_messages w2 ON w2.phone = w.phone
        LEFT JOIN clients c ON c.id = w.client_id
        WHERE w.org_id = ?
          AND w.id = (
            SELECT id FROM whatsapp_messages
            WHERE phone = w.phone AND org_id = ?
            ORDER BY created_at DESC LIMIT 1
          )
        GROUP BY w.phone
        ORDER BY w.created_at DESC
    """, (org_id, org_id)).fetchall()
    db.close()
    return rows


def get_conversation(phone: str, org_id=1):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM whatsapp_messages WHERE phone = ? AND org_id = ? ORDER BY created_at ASC",
        (phone, org_id),
    ).fetchall()
    db.close()
    return rows


def mark_read(phone: str, org_id=1):
    db = get_db()
    db.execute(
        "UPDATE whatsapp_messages SET status='read' WHERE phone=? AND org_id=? AND direction='inbound' AND status='received'",
        (phone, org_id),
    )
    db.commit()
    db.close()


def get_unread_count(org_id=None) -> int:
    db = get_db()
    if org_id is not None:
        count = db.execute(
            "SELECT COUNT(*) FROM whatsapp_messages WHERE direction='inbound' AND status='received' AND org_id=?",
            (org_id,)
        ).fetchone()[0]
    else:
        count = db.execute(
            "SELECT COUNT(*) FROM whatsapp_messages WHERE direction='inbound' AND status='received'"
        ).fetchone()[0]
    db.close()
    return count


def save_client_note(client_id: int, content: str = '', phone: str = ''):
    """Registra la conversación de WhatsApp en el historial del cliente.

    Mantiene UNA sola entrada por cliente (note_type='whatsapp_chat') cuyo
    contenido es el teléfono del chat; el historial la muestra como enlace
    "Conversación aquí". Cada mensaje nuevo solo actualiza la fecha de la
    última actividad, así no se duplican los chats en el historial.
    """
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id, content FROM notes WHERE client_id = ? AND note_type = 'whatsapp_chat' "
            "ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        if not phone and not existing:
            row = db.execute("SELECT phone FROM clients WHERE id = ?", (client_id,)).fetchone()
            phone = (row['phone'] if row else '') or ''
        if existing:
            if phone:
                db.execute(
                    "UPDATE notes SET content = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (phone, existing['id']),
                )
            else:
                db.execute(
                    "UPDATE notes SET created_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (existing['id'],),
                )
        else:
            db.execute(
                "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'whatsapp_chat', ?)",
                (client_id, phone),
            )
        db.commit()
    finally:
        db.close()


def messages_to_dicts(rows):
    def _media(r):
        keys = r.keys() if hasattr(r, 'keys') else []
        if 'media_path' in keys and r['media_path']:
            return url_for('whatsapp.media', msg_id=r['id']), r['media_type']
        if 'media_type' in keys and r['media_type']:
            return None, r['media_type']
        return None, None
    return [
        {
            'media_url':    _media(r)[0],
            'media_type':   _media(r)[1],
            'id':           r['id'],
            'phone':        r['phone'],
            'direction':    r['direction'],
            'message':      r['message'],
            'status':       r['status'],
            'wa_message_id': r['wa_message_id'],
            'created_at':   r['created_at'],
        }
        for r in rows
    ]


def _get_org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


# ── Proveedor de envío (Zernio por defecto, Twilio como respaldo) ────────────

def _provider() -> str:
    forced = os.getenv('WHATSAPP_PROVIDER', '').strip().lower()
    if forced in ('zernio', 'twilio'):
        return forced
    return 'zernio' if zernio.is_configured() else 'twilio'


def _get_conv_id(phone: str, org_id=1):
    db = get_db()
    row = db.execute(
        "SELECT zernio_conversation_id FROM whatsapp_conversations WHERE org_id=? AND phone=?",
        (org_id, zernio.to_e164(phone)),
    ).fetchone()
    db.close()
    return row['zernio_conversation_id'] if row else None


def _save_conv_id(phone: str, conversation_id: str, org_id=1, inbound=False):
    if not conversation_id:
        return
    db = get_db()
    db.execute(
        """INSERT INTO whatsapp_conversations (org_id, phone, zernio_conversation_id, last_inbound_at)
               VALUES (?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP END)
           ON CONFLICT(org_id, phone) DO UPDATE SET
               zernio_conversation_id = excluded.zernio_conversation_id,
               last_inbound_at = COALESCE(excluded.last_inbound_at, whatsapp_conversations.last_inbound_at),
               updated_at = CURRENT_TIMESTAMP""",
        (org_id, zernio.to_e164(phone), conversation_id, 1 if inbound else 0),
    )
    db.commit()
    db.close()


def _message_exists(wa_message_id: str) -> bool:
    if not wa_message_id:
        return False
    db = get_db()
    row = db.execute("SELECT 1 FROM whatsapp_messages WHERE wa_message_id=? LIMIT 1",
                     (wa_message_id,)).fetchone()
    db.close()
    return row is not None


def _update_status(wa_message_id: str, status: str):
    if not wa_message_id:
        return
    rank = {'sent': 1, 'delivered': 2, 'read': 3, 'failed': 9}
    db = get_db()
    row = db.execute("SELECT id, status FROM whatsapp_messages WHERE wa_message_id=? AND direction='outbound'",
                     (wa_message_id,)).fetchone()
    # No retroceder (un 'delivered' tardío no pisa un 'read')
    if row and rank.get(status, 0) > rank.get(row['status'], 0):
        db.execute("UPDATE whatsapp_messages SET status=? WHERE id=?", (status, row['id']))
        db.commit()
    db.close()


def deliver(phone: str, message: str, org_id=1, template=None) -> str:
    """
    Envía un WhatsApp y devuelve el id del mensaje.
    template = {'name': ..., 'language': 'es', 'params': [...]} para primer contacto.
    Lanza zernio.TemplateRequired si no hay ventana abierta y no se pasó plantilla.
    """
    phone = zernio.to_e164(phone)

    if _provider() == 'zernio':
        if template and template.get('name'):
            data = zernio.start_with_template(phone, template['name'],
                                              template.get('language') or 'es',
                                              template.get('params'))
            _save_conv_id(phone, data.get('conversationId'), org_id)
            return data.get('messageId', '')

        conv_id = _get_conv_id(phone, org_id) or zernio.find_conversation_id(phone)
        if not conv_id:
            raise zernio.TemplateRequired(
                'Este contacto no ha escrito todavía. WhatsApp exige una plantilla aprobada para el primer mensaje.',
                code='TEMPLATE_REQUIRED')
        _save_conv_id(phone, conv_id, org_id)
        data = zernio.send_text(conv_id, message)
        return data.get('messageId', '')

    # ── Twilio (legacy) ──
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token  = os.getenv('TWILIO_AUTH_TOKEN')
    from_number = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    if not account_sid or not auth_token:
        raise RuntimeError('No hay proveedor de WhatsApp configurado (Zernio ni Twilio).')
    from twilio.rest import Client as TwilioClient
    msg = TwilioClient(account_sid, auth_token).messages.create(
        from_=from_number, to=f'whatsapp:{phone}', body=message)
    return msg.sid


def _record_outbound(phone, message, wa_id, org_id, client_id=None, media=None):
    m_path, m_type, m_mime = media or (None, None, None)
    resolved = client_id
    if not resolved:
        matched = find_client_by_phone(phone, org_id)
        resolved = matched['id'] if matched else None
    save_message(phone=zernio.to_e164(phone), direction='outbound', message=message,
                 wa_message_id=wa_id, client_id=resolved, status='sent', org_id=org_id,
                 media_path=m_path, media_type=m_type, media_mime=m_mime)
    if resolved:
        try:
            save_client_note(int(resolved), phone=zernio.to_e164(phone))
        except Exception:
            pass
    return resolved


# ── Routes ───────────────────────────────────────────────────────────────────

# ── Adjuntos: audios, stickers, imágenes ─────────────────────────────────────

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'wa_media')
MAX_MEDIA_BYTES = 16 * 1024 * 1024
_MEDIA_COLS_OK = False

_EXT_BY_MIME = {
    'audio/ogg': '.ogg', 'audio/opus': '.ogg', 'audio/mpeg': '.mp3', 'audio/mp3': '.mp3',
    'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a', 'audio/aac': '.aac', 'audio/amr': '.amr',
    'audio/webm': '.webm', 'audio/wav': '.wav', 'audio/x-wav': '.wav',
    'image/webp': '.webp', 'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'video/mp4': '.mp4', 'video/3gpp': '.3gp', 'application/pdf': '.pdf',
}
_MEDIA_LABELS = {'audio': '[audio]', 'sticker': '[sticker]', 'image': '[image]',
                 'video': '[video]', 'file': '[archivo]'}


def _ensure_media_columns():
    """Agrega (una sola vez) las columnas de adjuntos a whatsapp_messages."""
    global _MEDIA_COLS_OK
    if _MEDIA_COLS_OK:
        return
    db = get_db()
    try:
        cols = {r[1] for r in db.execute('PRAGMA table_info(whatsapp_messages)').fetchall()}
        for col in ('media_path', 'media_type', 'media_mime'):
            if col not in cols:
                db.execute(f'ALTER TABLE whatsapp_messages ADD COLUMN {col} TEXT')
        db.commit()
        _MEDIA_COLS_OK = True
    finally:
        db.close()


def _clean_mime(mime) -> str:
    return (mime or '').split(';')[0].strip().lower()


def _ext_for(mime, fallback='.bin') -> str:
    m = _clean_mime(mime)
    return _EXT_BY_MIME.get(m) or mimetypes.guess_extension(m) or fallback


def _media_kind(att_type, mime) -> str:
    t, m = (att_type or '').lower(), _clean_mime(mime)
    if t == 'sticker' or m == 'image/webp':
        return 'sticker'
    if t in ('audio', 'voice', 'ptt') or m.startswith('audio/'):
        return 'audio'
    if t == 'image' or m.startswith('image/'):
        return 'image'
    if t == 'video' or m.startswith('video/'):
        return 'video'
    return 'file'


def _media_label(kind):
    return _MEDIA_LABELS.get(kind or '', '') if kind else ''


def _store_media(data: bytes, mime: str) -> str:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    name = uuid.uuid4().hex + _ext_for(mime)
    with open(os.path.join(MEDIA_DIR, name), 'wb') as fh:
        fh.write(data)
    return name


def _save_inbound_media(attachments):
    """Descarga el primer adjunto de un webhook y lo guarda en wa_media/.
    Devuelve (media_path, media_type, media_mime); (None, None, None) si no hay."""
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        payload = att.get('payload') if isinstance(att.get('payload'), dict) else {}
        url = att.get('url') or payload.get('url')
        hint_mime = att.get('mimeType') or att.get('mime_type') or payload.get('mimeType')
        kind = _media_kind(att.get('type'), hint_mime)
        if not url:
            return None, kind, None
        try:
            data, ctype = zernio.download_media(url)
        except Exception:
            log.exception('WhatsApp: no se pudo descargar el adjunto')
            return None, kind, None
        mime = _clean_mime(hint_mime or ctype)
        kind = _media_kind(att.get('type'), mime)
        return _store_media(data, mime), kind, mime
    return None, None, None


def _ffmpeg():
    return shutil.which('ffmpeg')


def _ffmpeg_convert(data: bytes, in_ext: str, out_ext: str, args) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'in' + in_ext)
        dst = os.path.join(tmp, 'out' + out_ext)
        with open(src, 'wb') as fh:
            fh.write(data)
        proc = subprocess.run([_ffmpeg(), '-y', '-loglevel', 'error', '-i', src] + list(args) + [dst],
                              capture_output=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(dst):
            raise ValueError('No se pudo convertir el audio: ' + proc.stderr.decode('utf-8', 'ignore')[:200])
        with open(dst, 'rb') as fh:
            return fh.read()


_WA_AUDIO_OK = ('audio/mpeg', 'audio/mp3', 'audio/mp4', 'audio/x-m4a', 'audio/aac', 'audio/amr')


def _prepare_audio(data: bytes, mime: str):
    """WhatsApp acepta OGG/Opus (nota de voz), MP3, AAC/M4A y AMR.
    Todo se convierte con ffmpeg a OGG/Opus mono para que llegue como nota de voz.
    Devuelve (bytes, mime, es_nota_de_voz)."""
    mime = _clean_mime(mime)
    if _ffmpeg():
        out = _ffmpeg_convert(data, _ext_for(mime, '.webm'), '.ogg',
                              ['-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k'])
        return out, 'audio/ogg', True
    if mime in ('audio/ogg', 'audio/opus'):
        return data, 'audio/ogg', True
    if mime in _WA_AUDIO_OK:
        return data, mime, False
    raise ValueError('Formato de audio no compatible con WhatsApp y el servidor no tiene ffmpeg para convertirlo.')


def _prepare_sticker(data: bytes, mime: str):
    """Sticker de WhatsApp: WEBP 512x512, máx. 100 KB (animado hasta 500 KB)."""
    import io
    from PIL import Image
    mime = _clean_mime(mime)
    if mime == 'image/webp' and len(data) <= 500 * 1024:
        try:
            with Image.open(io.BytesIO(data)) as im:
                if getattr(im, 'is_animated', False) or (im.size == (512, 512) and len(data) <= 100 * 1024):
                    return data, 'image/webp'
        except Exception:
            return data, 'image/webp'
    with Image.open(io.BytesIO(data)) as src:
        im = src.convert('RGBA')
    im.thumbnail((512, 512))
    canvas = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
    canvas.paste(im, ((512 - im.width) // 2, (512 - im.height) // 2), im)
    buf = io.BytesIO()
    for q in (85, 70, 55, 40):
        buf = io.BytesIO()
        canvas.save(buf, 'WEBP', quality=q)
        if buf.tell() <= 100 * 1024:
            break
    return buf.getvalue(), 'image/webp'


def _webp_to_png(data: bytes) -> bytes:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.convert('RGBA').save(buf, 'PNG')
        return buf.getvalue()


def deliver_media(phone: str, data: bytes, mime: str, kind: str, filename: str = 'archivo',
                  voice: bool = False, org_id=1) -> str:
    """Envía audio / sticker / imagen / archivo por Zernio. Devuelve el id del mensaje."""
    if _provider() != 'zernio':
        raise RuntimeError('El envío de audios y stickers requiere Zernio como proveedor de WhatsApp.')
    phone = zernio.to_e164(phone)
    conv_id = _get_conv_id(phone, org_id) or zernio.find_conversation_id(phone)
    if not conv_id:
        raise zernio.TemplateRequired(
            'Este contacto no ha escrito todavía. WhatsApp exige una plantilla aprobada para el primer mensaje.',
            code='TEMPLATE_REQUIRED')
    _save_conv_id(phone, conv_id, org_id)
    base = 'audio' if kind == 'audio' else 'sticker' if kind == 'sticker' else 'archivo'
    url = zernio.upload_media(data, base + _ext_for(mime), mime)
    if kind == 'sticker':
        try:
            res = zernio.send_attachment(conv_id, url, 'sticker')
        except zernio.TemplateRequired:
            raise
        except zernio.ZernioError as exc:
            # Si la cuenta no acepta stickers, se envía como imagen PNG
            log.warning('Sticker no aceptado (%s); se envía como imagen', exc)
            png_url = zernio.upload_media(_webp_to_png(data), 'sticker.png', 'image/png')
            res = zernio.send_attachment(conv_id, png_url, 'image')
    else:
        att = {'audio': 'audio', 'image': 'image', 'video': 'video'}.get(kind, 'file')
        res = zernio.send_attachment(conv_id, url, att, voice_note=voice,
                                     name=filename if att == 'file' else None)
    return res.get('messageId', '') or (res.get('messageIds') or [''])[0]


@whatsapp_bp.route('/whatsapp/media/<int:msg_id>')
def media(msg_id):
    """Sirve el adjunto de un mensaje (solo usuarios con sesión, de su organización)."""
    _ensure_media_columns()
    db = get_db()
    row = db.execute('SELECT media_path, media_mime FROM whatsapp_messages WHERE id = ? AND org_id = ?',
                     (msg_id, _get_org_id())).fetchone()
    db.close()
    if not row or not row['media_path']:
        abort(404)
    path = os.path.join(MEDIA_DIR, os.path.basename(row['media_path']))
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype=row['media_mime'] or None, conditional=True, max_age=86400)


@whatsapp_bp.route('/whatsapp/stickers.json')
def stickers_json():
    """Últimos stickers enviados o recibidos, para reutilizarlos."""
    _ensure_media_columns()
    db = get_db()
    rows = db.execute(
        "SELECT MAX(id) AS id FROM whatsapp_messages WHERE org_id = ? AND media_type = 'sticker' "
        "AND media_path IS NOT NULL GROUP BY media_path ORDER BY MAX(id) DESC LIMIT 40",
        (_get_org_id(),)).fetchall()
    db.close()
    return jsonify({'stickers': [{'id': r['id'], 'url': url_for('whatsapp.media', msg_id=r['id'])}
                                 for r in rows]})


def _parse_ts(value):
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        s = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s) if 'T' in s else datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@whatsapp_bp.route('/whatsapp/media/backfill', methods=['POST'])
def media_backfill():
    """Recupera desde Zernio los adjuntos (audios, stickers…) de mensajes que llegaron
    sin archivo guardado. WhatsApp los conserva ~7 días."""
    _ensure_media_columns()
    org_id = _get_org_id()
    phone = zernio.to_e164((request.get_json(silent=True) or {}).get('phone') or request.form.get('phone') or '')
    if not phone or _provider() != 'zernio':
        return jsonify({'success': False, 'updated': 0})
    db = get_db()
    pending = db.execute(
        "SELECT id, wa_message_id, created_at, direction FROM whatsapp_messages "
        "WHERE org_id = ? AND phone = ? AND media_path IS NULL "
        "AND message GLOB '[[]*]' AND created_at >= datetime('now', '-7 days') "
        "ORDER BY id DESC LIMIT 30",
        (org_id, phone)).fetchall()
    db.close()
    if not pending:
        return jsonify({'success': True, 'updated': 0})
    conv_id = _get_conv_id(phone, org_id) or zernio.find_conversation_id(phone)
    if not conv_id:
        return jsonify({'success': False, 'updated': 0, 'error': 'conversación no encontrada en Zernio'})
    try:
        remote = [m for m in zernio.list_messages(conv_id, 100, 'desc') if m.get('attachments')]
    except Exception as exc:
        log.exception('Backfill: no se pudieron listar mensajes')
        return jsonify({'success': False, 'updated': 0, 'error': str(exc)})

    used, updated = set(), 0
    for row in pending:
        match = None
        for m in remote:
            ids = {m.get('id'), m.get('platformMessageId'), m.get('messageId')}
            if row['wa_message_id'] and row['wa_message_id'] in ids:
                match = m
                break
        if match is None:   # respaldo: mismo sentido y hora parecida (±3 min)
            t_row = _parse_ts(row['created_at'])
            want = 'incoming' if row['direction'] == 'inbound' else 'outgoing'
            for m in remote:
                key = m.get('id')
                t_m = _parse_ts(m.get('createdAt') or m.get('timestamp') or m.get('sentAt'))
                if key in used or (m.get('direction') and m.get('direction') != want):
                    continue
                if t_row and t_m and abs((t_m - t_row).total_seconds()) <= 180:
                    match = m
                    break
        if match is None:
            continue
        used.add(match.get('id'))
        m_path, m_type, m_mime = _save_inbound_media(match.get('attachments'))
        if not m_path:
            continue
        db = get_db()
        db.execute('UPDATE whatsapp_messages SET media_path = ?, media_type = ?, media_mime = ? WHERE id = ?',
                   (m_path, m_type, m_mime, row['id']))
        db.commit()
        db.close()
        updated += 1
    return jsonify({'success': True, 'updated': updated, 'pending': len(pending)})


@whatsapp_bp.route('/whatsapp/media-check')
def media_check():
    try:
        import PIL  # noqa: F401
        pillow = True
    except Exception:
        pillow = False
    return jsonify({'ffmpeg': bool(_ffmpeg()), 'pillow': pillow, 'provider': _provider()})


@whatsapp_bp.route('/whatsapp/send-media', methods=['POST'])
def send_media():
    """Envía un audio (grabado o archivo), un sticker o una imagen/archivo."""
    _ensure_media_columns()
    org_id = _get_org_id()
    phone = (request.form.get('phone') or '').strip()
    kind = (request.form.get('kind') or '').strip().lower()
    client_id = request.form.get('client_id') or None
    reuse_id = request.form.get('reuse_id')
    if not phone:
        return jsonify({'success': False, 'error': 'Falta el teléfono'}), 400

    if reuse_id:   # reenviar un sticker ya guardado
        db = get_db()
        row = db.execute('SELECT media_path, media_mime FROM whatsapp_messages WHERE id = ? AND org_id = ?',
                         (int(reuse_id), org_id)).fetchone()
        db.close()
        path = row and row['media_path'] and os.path.join(MEDIA_DIR, os.path.basename(row['media_path']))
        if not path or not os.path.exists(path):
            return jsonify({'success': False, 'error': 'Sticker no encontrado'}), 404
        with open(path, 'rb') as fh:
            data = fh.read()
        mime, filename, kind = row['media_mime'] or 'image/webp', 'sticker.webp', 'sticker'
    else:
        f = request.files.get('file')
        if not f:
            return jsonify({'success': False, 'error': 'Falta el archivo'}), 400
        data = f.read()
        mime = _clean_mime(f.mimetype)
        filename = os.path.basename(f.filename or 'archivo')
        if not kind:
            kind = _media_kind('', mime)

    if not data:
        return jsonify({'success': False, 'error': 'Archivo vacío'}), 400
    if len(data) > MAX_MEDIA_BYTES:
        return jsonify({'success': False, 'error': 'El archivo supera 16 MB'}), 413

    voice = False
    try:
        if kind == 'audio':
            data, mime, voice = _prepare_audio(data, mime)
        elif kind == 'sticker':
            data, mime = _prepare_sticker(data, mime)
        wa_id = deliver_media(phone, data, mime, kind, filename, voice, org_id)
    except zernio.TemplateRequired as exc:
        return jsonify({'success': False, 'template_required': True, 'error': str(exc)}), 409
    except Exception as exc:
        log.exception('WhatsApp: error enviando adjunto')
        return jsonify({'success': False, 'error': str(exc)}), 500

    media_path = _store_media(data, mime)
    _record_outbound(phone, _media_label(kind), wa_id, org_id, client_id,
                     media=(media_path, kind, mime))
    try:
        ai_agent.pause(zernio.to_e164(phone), org_id)   # intervención humana
    except Exception:
        log.exception('No se pudo pausar el bot')
    return jsonify({'success': True, 'sid': wa_id})


@whatsapp_bp.route('/whatsapp')
def index():
    org_id        = _get_org_id()
    conversations = get_conversations(org_id)
    active_phone  = request.args.get('phone', '').strip()
    active_msgs   = []
    active_client = None

    if active_phone:
        mark_read(active_phone, org_id)
        active_msgs   = get_conversation(active_phone, org_id)
        active_client = find_client_by_phone(active_phone, org_id)

    return render_template(
        'whatsapp/index.html',
        conversations=conversations,
        conversations_data=conversations_to_dicts(conversations, org_id),
        active_phone=active_phone,
        active_msgs=active_msgs,
        active_msgs_data=messages_to_dicts(active_msgs),
        active_client=active_client,
        bot_state=(ai_agent.contact_bot_state(zernio.to_e164(active_phone) or active_phone, org_id)
                   if active_phone else None),
    )


def _clients_by_digits(org_id=1):
    """Mapa últimos-10-dígitos → cliente, para nombrar chats cuyo mensaje no quedó enlazado."""
    db = get_db()
    rows = db.execute(
        "SELECT id, first_name, last_name, phone FROM clients WHERE phone IS NOT NULL AND phone != '' AND org_id=?",
        (org_id,)).fetchall()
    db.close()
    out = {}
    for c in rows:
        d = re.sub(r'\D', '', c['phone'] or '')
        if len(d) >= 7:
            out[d[-10:]] = c
    return out


def conversations_to_dicts(rows, org_id=1):
    clients = _clients_by_digits(org_id)
    out = []
    for r in rows:
        client_id, first, last = r['client_id'], r['first_name'], r['last_name']
        if not first:
            c = clients.get(re.sub(r'\D', '', r['phone'] or '')[-10:])
            if c:
                client_id, first, last = c['id'], c['first_name'], c['last_name']
        name = ' '.join(x for x in (first, last) if x) if first else ''
        out.append({
            'phone':      r['phone'],
            'name':       name,
            'client_id':  client_id,
            'last_message':   r['last_message'],
            'last_direction': r['last_direction'],
            'last_status':    r['last_status'],
            'last_at':    r['last_at'],
            'unread':     r['unread'] or 0,
        })
    return out


@whatsapp_bp.route('/whatsapp/conversations.json')
def conversations_json():
    """Lista de chats para refrescar el panel izquierdo sin recargar."""
    org_id = _get_org_id()
    return jsonify({'conversations': conversations_to_dicts(get_conversations(org_id), org_id)})


@whatsapp_bp.route('/webhook/whatsapp', methods=['POST'])
def webhook():
    """Legacy: mensajes entrantes desde Twilio."""
    raw_from = request.form.get('From', '')
    body      = request.form.get('Body', '').strip()
    wa_id     = request.form.get('MessageSid', '')

    profile   = request.form.get('ProfileName', '').strip()

    phone  = normalize_phone(raw_from)
    org_id = 1
    if not phone or _message_exists(wa_id):   # Twilio reintenta webhooks
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

    client_id = _resolve_client_id(phone, profile, body, org_id)
    save_message(phone=phone, direction='inbound', message=body or '[mensaje sin texto]',
                 wa_message_id=wa_id, client_id=client_id, org_id=org_id)
    ai_agent.handle_incoming(phone, client_id, org_id)
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


@whatsapp_bp.route('/webhook/zernio', methods=['POST'])
def zernio_webhook():
    """Eventos de Zernio: message.received / sent / delivered / read / failed."""
    raw = request.get_data()
    if not zernio.verify_signature(raw, request.headers.get('X-Zernio-Signature', '')):
        log.warning('Zernio webhook: firma inválida')
        return jsonify({'error': 'invalid signature'}), 401

    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        return jsonify({'error': 'invalid json'}), 400

    event   = payload.get('event') or request.headers.get('X-Zernio-Event', '')
    msg     = payload.get('message') or {}
    conv    = payload.get('conversation') or {}
    account = payload.get('account') or {}
    platform = msg.get('platform') or account.get('platform')

    if event == 'webhook.test':
        return jsonify({'ok': True})
    if platform and platform != 'whatsapp':
        return jsonify({'ignored': platform})   # IG/Messenger siguen por Meta directo

    org_id = 1  # PapiaTech (single-tenant para este número)
    wa_id  = msg.get('platformMessageId') or msg.get('id')

    try:
        if event == 'message.received':
            sender = msg.get('sender') or {}
            phone = zernio.to_e164(sender.get('phoneNumber') or conv.get('participantId') or sender.get('id'))
            if not phone or _message_exists(wa_id):
                return jsonify({'ok': True})
            text = msg.get('text') or ''
            if not text and msg.get('attachments'):
                kinds = ', '.join(a.get('type', 'archivo') for a in msg['attachments'])
                text = f'[{kinds}]'
            m_path, m_type, m_mime = _save_inbound_media(msg.get('attachments'))
            if m_type and (not msg.get('text') or text.startswith('[')):
                text = msg.get('text') or _media_label(m_type)
            _save_conv_id(phone, conv.get('id') or msg.get('conversationId'), org_id, inbound=True)
            name = (sender.get('name') or sender.get('displayName')
                    or conv.get('participantName') or conv.get('name') or '')
            # Número desconocido → se crea como cliente en "Nuevo Lead" (pipeline)
            client_id = _resolve_client_id(phone, name, text, org_id)
            save_message(phone=phone, direction='inbound', message=text or '[mensaje]',
                         media_path=m_path, media_type=m_type, media_mime=m_mime,
                         wa_message_id=wa_id, client_id=client_id,
                         org_id=org_id)
            # Asistente IA: responde si está activo y la conversación no está pausada
            ai_agent.handle_incoming(phone, client_id, org_id)

        elif event == 'message.sent':
            # Enviado desde el celular (coexistence) o desde el panel de Zernio
            phone = zernio.to_e164(conv.get('participantId') or '')
            if phone and not _message_exists(wa_id):
                _save_conv_id(phone, conv.get('id'), org_id)
                sent_media = _save_inbound_media(msg.get('attachments'))
                _record_outbound(phone, msg.get('text') or _media_label(sent_media[1]) or '[mensaje]',
                                 wa_id, org_id, media=sent_media)
                # Escribiste desde el celular/panel: el bot se pausa en este chat
                if not ai_agent.is_bot_echo(phone, msg.get('text') or '', org_id):
                    ai_agent.pause(phone, org_id)

        elif event in ('message.delivered', 'message.read', 'message.failed'):
            _update_status(wa_id, event.split('.', 1)[1])
            if event == 'message.failed':
                log.warning('Zernio message.failed: %s', payload.get('error'))
    except Exception:
        log.exception('Error procesando webhook de Zernio')
        # 200 igual: el error es nuestro, reintentar no lo arregla
    return jsonify({'ok': True})


@whatsapp_bp.route('/whatsapp/send', methods=['POST'])
def send_message():
    """Enviar WhatsApp desde el CRM."""
    org_id    = _get_org_id()
    data      = request.get_json(silent=True) or {}
    phone     = data.get('phone', '').strip()
    message   = data.get('message', '').strip()
    client_id = data.get('client_id')
    template  = data.get('template')  # opcional {'name','language','params'}

    if not phone or (not message and not template):
        return jsonify({'success': False, 'error': 'phone and message required'}), 400

    try:
        wa_id = deliver(phone, message, org_id, template=template)
        shown = message or f"[plantilla: {template.get('name')}]"
        _record_outbound(phone, shown, wa_id, org_id, client_id)
        try:
            ai_agent.pause(zernio.to_e164(phone), org_id)   # intervención humana
        except Exception:
            log.exception('No se pudo pausar el bot')
        return jsonify({'success': True, 'sid': wa_id})
    except zernio.TemplateRequired as exc:
        return jsonify({'success': False, 'template_required': True, 'error': str(exc)}), 409
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@whatsapp_bp.route('/whatsapp/conversation/<path:phone>')
def conversation_json(phone):
    """Return conversation as JSON for polling."""
    org_id = _get_org_id()
    phone = normalize_phone(phone)
    mark_read(phone, org_id)
    msgs   = get_conversation(phone, org_id)
    client = find_client_by_phone(phone, org_id)
    return jsonify({
        'messages': messages_to_dicts(msgs),
        'client': {
            'id':         client['id'],
            'first_name': client['first_name'],
            'last_name':  client['last_name'],
        } if client else None,
    })


@whatsapp_bp.route('/whatsapp/templates.json')
def templates_json():
    """Plantillas aprobadas en Zernio/Meta, para el formulario de nueva conversación."""
    if _provider() != 'zernio':
        return jsonify({'templates': []})
    try:
        data = zernio.list_templates()
        items = data.get('templates') or data.get('data') or []
        out = [{'name': t.get('name'), 'language': t.get('language'),
                'status': t.get('status'), 'category': t.get('category')}
               for t in items if str(t.get('status', '')).upper() == 'APPROVED']
        return jsonify({'templates': out})
    except Exception as exc:
        return jsonify({'templates': [], 'error': str(exc)})


@whatsapp_bp.route('/whatsapp/status')
def provider_status():
    """Diagnóstico rápido: proveedor activo y estado del número en Meta."""
    info = {'provider': _provider(), 'zernio_configured': zernio.is_configured()}
    if _provider() == 'zernio':
        try:
            info['number'] = zernio.number_info()
        except Exception as exc:
            info['number_error'] = str(exc)
    return jsonify(info)


@whatsapp_bp.route('/whatsapp/new-conversation', methods=['GET', 'POST'])
def new_conversation():
    """Iniciar conversación con cualquier número."""
    org_id = _get_org_id()
    if request.method == 'POST':
        phone    = zernio.to_e164(normalize_phone(request.form.get('phone', '')))
        message  = request.form.get('message', '').strip()
        tpl_name = request.form.get('template_name', '').strip()
        tpl_lang = request.form.get('template_language', '').strip() or 'es'
        tpl_params = [p.strip() for p in request.form.get('template_params', '').split('|') if p.strip()]

        if not phone:
            flash('Número de teléfono requerido.', 'danger')
            return redirect(url_for('whatsapp.new_conversation'))

        if message or tpl_name:
            template = {'name': tpl_name, 'language': tpl_lang, 'params': tpl_params} if tpl_name else None
            try:
                wa_id = deliver(phone, message, org_id, template=template)
                _record_outbound(phone, message if not template else f'[plantilla: {tpl_name}]',
                                 wa_id, org_id)
            except zernio.TemplateRequired as exc:
                flash(f'{exc} Elige una plantilla aprobada abajo.', 'danger')
                return redirect(url_for('whatsapp.new_conversation', phone=phone, need_template=1))
            except Exception as exc:
                flash(f'Error al enviar: {exc}', 'danger')
                return redirect(url_for('whatsapp.new_conversation', phone=phone))

        return redirect(url_for('whatsapp.index', phone=phone))

    return render_template('whatsapp/new_conversation.html')


# ── Asistente IA (Claude) ────────────────────────────────────────────────────

@whatsapp_bp.route('/whatsapp/bot', methods=['GET', 'POST'])
def bot_settings():
    org_id = _get_org_id()
    if request.method == 'POST':
        try:
            hours = max(1, min(168, int(request.form.get('pause_hours') or 24)))
        except ValueError:
            hours = 24
        ai_agent.save_settings(org_id, bool(request.form.get('enabled')),
                               (request.form.get('business_info') or '').strip(), hours)
        flash('Asistente de WhatsApp actualizado.', 'success')
        return redirect(url_for('whatsapp.bot_settings'))
    return render_template('whatsapp/bot.html',
                           settings=ai_agent.get_settings(org_id),
                           paused=ai_agent.paused_list(org_id),
                           has_key=bool(os.getenv('ANTHROPIC_API_KEY')),
                           usage=ai_agent.usage_summary(org_id),
                           calendar=ai_agent.calendar_status(org_id),
                           model=os.getenv('AI_BOT_MODEL', ai_agent.DEFAULT_MODEL))


@whatsapp_bp.route('/whatsapp/bot/resume', methods=['POST'])
def bot_resume():
    ai_agent.resume((request.form.get('phone') or '').strip(), _get_org_id())
    flash('El asistente volverá a responder en esa conversación.', 'success')
    return redirect(url_for('whatsapp.bot_settings'))


@whatsapp_bp.route('/whatsapp/bot/contact', methods=['POST'])
def bot_contact_toggle():
    """Activa o desactiva el asistente IA para un contacto concreto."""
    data = request.get_json(silent=True) or {}
    raw = (data.get('phone') or '').strip()
    if not raw:
        return jsonify({'success': False, 'error': 'Falta el teléfono'}), 400
    org_id = _get_org_id()
    phone = zernio.to_e164(raw) or raw
    ai_agent.set_contact_enabled(phone, bool(data.get('enabled')), org_id)
    return jsonify({'success': True, 'state': ai_agent.contact_bot_state(phone, org_id)})


@whatsapp_bp.route('/whatsapp/bot/test', methods=['POST'])
def bot_test():
    text = ((request.get_json(silent=True) or {}).get('message') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'Escribe un mensaje'}), 400
    try:
        return jsonify({'success': True, 'reply': ai_agent.preview(text[:1000], _get_org_id())})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)[:300]}), 500
