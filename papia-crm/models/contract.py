"""
Contratos con firma electrónica dentro del CRM.

Conceptos (mismo modelo que DocuSign):
  - Sobre (envelope): un PDF + destinatarios + campos. Estados:
        draft → sent → completed
                     ↘ declined | voided | expired
  - Destinatario (recipient): firmante ('signer') o copia ('cc').
        Los firmantes firman por `routing_order`; los del mismo orden firman en paralelo.
        Estados: created → sent → viewed → completed | declined
  - Campo (field): posición normalizada (0–1, origen arriba-izquierda) en una página.
  - Evento (event): audit trail inmutable (quién, qué, cuándo, IP, dispositivo).

Seguridad:
  - El enlace de firma lleva un token aleatorio de 256 bits; en la BD solo se guarda su SHA-256.
  - Código OTP de 6 dígitos (hash), 10 min de vigencia, 5 intentos.
  - SHA-256 del PDF original al subirlo y del PDF final sellado.
"""
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from database import get_db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES_ROOT = os.path.join(BASE_DIR, 'contract_files')

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5

ENVELOPE_STATUSES = [
    ('draft', 'Borrador'),
    ('sent', 'Esperando firmas'),
    ('completed', 'Completado'),
    ('declined', 'Rechazado'),
    ('voided', 'Anulado'),
    ('expired', 'Vencido'),
]
RECIPIENT_STATUSES = {
    'created': 'En cola', 'sent': 'Enviado', 'viewed': 'Abrió',
    'completed': 'Firmó', 'declined': 'Rechazó',
}
FIELD_TYPES = [
    ('signature', 'Firma'),
    ('initials', 'Iniciales'),
    ('date_signed', 'Fecha de firma'),
    ('full_name', 'Nombre completo'),
    ('email', 'Email'),
    ('text', 'Texto libre'),
    ('checkbox', 'Casilla'),
]
FIELD_TYPE_KEYS = {k for k, _ in FIELD_TYPES}
AUTO_FIELDS = {'date_signed', 'full_name', 'email'}   # se completan solos al firmar

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_envelopes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          INTEGER NOT NULL DEFAULT 1,
    uid             TEXT    NOT NULL UNIQUE,
    client_id       INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    title           TEXT    NOT NULL,
    message         TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'draft',
    original_name   TEXT    NOT NULL DEFAULT 'documento.pdf',
    page_count      INTEGER NOT NULL DEFAULT 1,
    page_sizes      TEXT    NOT NULL DEFAULT '[]',
    original_sha256 TEXT    NOT NULL,
    final_sha256    TEXT,
    require_otp     INTEGER NOT NULL DEFAULT 1,
    expires_days    INTEGER NOT NULL DEFAULT 14,
    reminder_days   INTEGER NOT NULL DEFAULT 3,
    expires_at      TEXT,
    void_reason     TEXT,
    created_by      INTEGER,
    created_by_name TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ce_org    ON contract_envelopes(org_id, status);
CREATE INDEX IF NOT EXISTS idx_ce_client ON contract_envelopes(client_id);

CREATE TABLE IF NOT EXISTS contract_recipients (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id       INTEGER NOT NULL REFERENCES contract_envelopes(id) ON DELETE CASCADE,
    org_id            INTEGER NOT NULL DEFAULT 1,
    role              TEXT    NOT NULL DEFAULT 'signer',
    routing_order     INTEGER NOT NULL DEFAULT 1,
    name              TEXT    NOT NULL,
    email             TEXT    NOT NULL DEFAULT '',
    phone             TEXT    NOT NULL DEFAULT '',
    notify_email      INTEGER NOT NULL DEFAULT 1,
    notify_whatsapp   INTEGER NOT NULL DEFAULT 0,
    color_idx         INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'created',
    token_hash        TEXT,
    otp_hash          TEXT,
    otp_expires_at    TEXT,
    otp_attempts      INTEGER NOT NULL DEFAULT 0,
    auth_method       TEXT,
    consent_at        TEXT,
    signature_png     TEXT,
    initials_png      TEXT,
    signature_kind    TEXT,
    signed_at         TEXT,
    signed_ip         TEXT,
    signed_ua         TEXT,
    decline_reason    TEXT,
    sent_at           TEXT,
    viewed_at         TEXT,
    last_reminder_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cr_env   ON contract_recipients(envelope_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_token ON contract_recipients(token_hash);

CREATE TABLE IF NOT EXISTS contract_fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id   INTEGER NOT NULL REFERENCES contract_envelopes(id) ON DELETE CASCADE,
    recipient_id  INTEGER NOT NULL REFERENCES contract_recipients(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL,
    page          INTEGER NOT NULL,
    x             REAL    NOT NULL,
    y             REAL    NOT NULL,
    w             REAL    NOT NULL,
    h             REAL    NOT NULL,
    required      INTEGER NOT NULL DEFAULT 1,
    label         TEXT    NOT NULL DEFAULT '',
    value         TEXT,
    filled_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_cf_env ON contract_fields(envelope_id);

CREATE TABLE IF NOT EXISTS contract_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id   INTEGER NOT NULL REFERENCES contract_envelopes(id) ON DELETE CASCADE,
    org_id        INTEGER NOT NULL DEFAULT 1,
    recipient_id  INTEGER,
    event         TEXT    NOT NULL,
    actor         TEXT    NOT NULL DEFAULT '',
    detail        TEXT    NOT NULL DEFAULT '',
    ip            TEXT    NOT NULL DEFAULT '',
    user_agent    TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cev_env ON contract_events(envelope_id, id);
"""

EVENT_LABELS = {
    'created': 'Sobre creado',
    'sent': 'Sobre enviado',
    'invited': 'Invitación enviada',
    'notify_failed': 'No se pudo notificar',
    'viewed': 'Enlace abierto',
    'otp_sent': 'Código de verificación enviado',
    'otp_verified': 'Identidad verificada por código',
    'otp_failed': 'Código incorrecto',
    'in_person': 'Firma en persona iniciada desde el CRM',
    'consent': 'Consentimiento de firma electrónica aceptado',
    'signed': 'Firmado',
    'declined': 'Rechazado',
    'reminder': 'Recordatorio enviado',
    'voided': 'Sobre anulado',
    'expired': 'Sobre vencido',
    'completed': 'Sobre completado · PDF sellado',
    'copy_sent': 'Copia final enviada',
    'archived': 'PDF archivado en el perfil del cliente',
    'downloaded': 'PDF descargado',
}


# ── helpers ──────────────────────────────────────────────────────────────────

_SCHEMA_READY = False


def _db():
    global _SCHEMA_READY
    db = get_db()
    if not _SCHEMA_READY:
        db.executescript(_SCHEMA)
        db.commit()
        _SCHEMA_READY = True
    return db


def ensure_schema():
    _db().close()


def now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def envelope_dir(org_id, uid):
    return os.path.join(FILES_ROOT, str(int(org_id)), uid)


def original_path(env):
    return os.path.join(envelope_dir(env['org_id'], env['uid']), 'original.pdf')


def final_path(env):
    return os.path.join(envelope_dir(env['org_id'], env['uid']), 'final.pdf')


class ContractError(ValueError):
    """Error de validación que se puede mostrar al usuario."""


# ── PDF intake ───────────────────────────────────────────────────────────────

def inspect_pdf(data: bytes):
    """Valida el PDF y devuelve [(ancho_pt, alto_pt), ...] por página."""
    from pypdf import PdfReader
    if not data or len(data) > MAX_PDF_BYTES:
        raise ContractError(f'El PDF debe pesar menos de {MAX_PDF_BYTES // (1024 * 1024)} MB.')
    if not data.lstrip().startswith(b'%PDF'):
        raise ContractError('El archivo no es un PDF válido.')
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ContractError('El PDF está protegido con contraseña. Quítale la protección y vuelve a subirlo.')
        sizes = []
        for page in reader.pages:
            box = page.cropbox          # lo mismo que muestra pdf.js
            w, h = float(box.width), float(box.height)
            if int(page.get('/Rotate') or 0) % 180:
                w, h = h, w
            sizes.append((round(w, 2), round(h, 2)))
    except ContractError:
        raise
    except Exception:
        raise ContractError('No se pudo leer el PDF. Exporta el documento de nuevo como PDF.')
    if not sizes:
        raise ContractError('El PDF no tiene páginas.')
    if len(sizes) > MAX_PAGES:
        raise ContractError(f'El PDF tiene más de {MAX_PAGES} páginas.')
    return sizes


# ── envelopes ────────────────────────────────────────────────────────────────

def create_envelope(org_id, title, pdf_bytes, original_name, client_id=None, message='',
                    created_by=None, created_by_name='', require_otp=True,
                    expires_days=14, reminder_days=3):
    sizes = inspect_pdf(pdf_bytes)
    uid = str(uuid.uuid4()).upper()
    folder = envelope_dir(org_id, uid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'original.pdf'), 'wb') as fh:
        fh.write(pdf_bytes)

    db = _db()
    try:
        cur = db.execute(
            """INSERT INTO contract_envelopes
               (org_id, uid, client_id, title, message, original_name, page_count, page_sizes,
                original_sha256, require_otp, expires_days, reminder_days, created_by, created_by_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_id, uid, client_id or None, title.strip()[:200], (message or '').strip()[:2000],
             (original_name or 'documento.pdf')[:200], len(sizes), json.dumps(sizes),
             sha256_hex(pdf_bytes), 1 if require_otp else 0,
             max(1, min(int(expires_days or 14), 120)), max(0, min(int(reminder_days or 0), 30)),
             created_by, created_by_name or ''),
        )
        env_id = cur.lastrowid
        db.commit()
    finally:
        db.close()
    log_event(env_id, org_id, 'created', actor=created_by_name or 'CRM',
              detail=f'Documento: {original_name} · SHA-256 {sha256_hex(pdf_bytes)}')
    return env_id


def update_envelope_settings(env_id, org_id, **kw):
    allowed = {'title', 'message', 'client_id', 'require_otp', 'expires_days', 'reminder_days'}
    sets, vals = [], []
    for k, v in kw.items():
        if k in allowed:
            sets.append(f'{k}=?')
            vals.append(v)
    if not sets:
        return
    db = _db()
    try:
        db.execute(f"UPDATE contract_envelopes SET {', '.join(sets)}, updated_at=datetime('now') "
                   "WHERE id=? AND org_id=? AND status='draft'", (*vals, env_id, org_id))
        db.commit()
    finally:
        db.close()


def _decorate_env(row):
    e = dict(row)
    e['status_label'] = dict(ENVELOPE_STATUSES).get(e['status'], e['status'])
    try:
        e['page_sizes'] = json.loads(e.get('page_sizes') or '[]')
    except ValueError:
        e['page_sizes'] = []
    return e


def get_envelope(env_id, org_id, with_children=True):
    db = _db()
    try:
        row = db.execute("SELECT e.*, c.first_name || ' ' || COALESCE(c.last_name, '') AS client_name "
                         "FROM contract_envelopes e LEFT JOIN clients c ON c.id = e.client_id "
                         "WHERE e.id=? AND e.org_id=?", (env_id, org_id)).fetchone()
        if not row:
            return None
        env = _decorate_env(row)
        if with_children:
            env['recipients'] = [_decorate_rcpt(r) for r in db.execute(
                "SELECT * FROM contract_recipients WHERE envelope_id=? ORDER BY role='cc', routing_order, id",
                (env_id,)).fetchall()]
            env['fields'] = [dict(r) for r in db.execute(
                "SELECT * FROM contract_fields WHERE envelope_id=? ORDER BY page, y, x", (env_id,)).fetchall()]
            env['events'] = [_decorate_event(r) for r in db.execute(
                "SELECT * FROM contract_events WHERE envelope_id=? ORDER BY id", (env_id,)).fetchall()]
        return env
    finally:
        db.close()


def get_envelope_by_id_unscoped(env_id):
    db = _db()
    try:
        row = db.execute("SELECT org_id FROM contract_envelopes WHERE id=?", (env_id,)).fetchone()
    finally:
        db.close()
    return get_envelope(env_id, row['org_id']) if row else None


def find_envelope_by_uid(uid):
    db = _db()
    try:
        row = db.execute("SELECT id, org_id FROM contract_envelopes WHERE uid=?",
                         ((uid or '').strip().upper(),)).fetchone()
    finally:
        db.close()
    return get_envelope(row['id'], row['org_id']) if row else None


def find_envelope_by_final_hash(digest):
    db = _db()
    try:
        row = db.execute("SELECT id, org_id FROM contract_envelopes WHERE final_sha256=? OR original_sha256=?",
                         (digest, digest)).fetchone()
    finally:
        db.close()
    return get_envelope(row['id'], row['org_id']) if row else None


TABS = [
    ('all', 'Todos'),
    ('waiting', 'Esperando por otros'),
    ('completed', 'Completados'),
    ('draft', 'Borradores'),
    ('closed', 'Rechazados / anulados'),
]


def list_envelopes(org_id, tab='all', q='', client_id=None, limit=300):
    where, args = ['e.org_id=?'], [org_id]
    if tab == 'waiting':
        where.append("e.status='sent'")
    elif tab == 'completed':
        where.append("e.status='completed'")
    elif tab == 'draft':
        where.append("e.status='draft'")
    elif tab == 'closed':
        where.append("e.status IN ('declined','voided','expired')")
    if client_id:
        where.append('e.client_id=?')
        args.append(client_id)
    if q:
        where.append("(e.title LIKE ? OR c.first_name LIKE ? OR c.last_name LIKE ? OR c.company LIKE ? OR e.uid LIKE ?)")
        like = f'%{q.strip()}%'
        args += [like] * 5
    db = _db()
    try:
        rows = db.execute(f"""
            SELECT e.*, c.first_name || ' ' || COALESCE(c.last_name,'') AS client_name, c.company AS client_company,
                   (SELECT COUNT(*) FROM contract_recipients r WHERE r.envelope_id=e.id AND r.role='signer') AS signer_total,
                   (SELECT COUNT(*) FROM contract_recipients r WHERE r.envelope_id=e.id AND r.role='signer'
                                                               AND r.status='completed') AS signer_done,
                   (SELECT MAX(created_at) FROM contract_events v WHERE v.envelope_id=e.id) AS last_activity
            FROM contract_envelopes e LEFT JOIN clients c ON c.id=e.client_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE((SELECT MAX(created_at) FROM contract_events v WHERE v.envelope_id=e.id), e.created_at) DESC
            LIMIT ?""", (*args, limit)).fetchall()
        out = []
        for r in rows:
            e = _decorate_env(r)
            e['recipients'] = [_decorate_rcpt(x) for x in db.execute(
                "SELECT * FROM contract_recipients WHERE envelope_id=? AND role='signer' ORDER BY routing_order, id",
                (e['id'],)).fetchall()]
            out.append(e)
        return out
    finally:
        db.close()


def envelope_stats(org_id):
    db = _db()
    try:
        r = db.execute("""
            SELECT
              SUM(status='sent') AS waiting,
              SUM(status='draft') AS drafts,
              SUM(status='completed' AND completed_at >= datetime('now','-30 days')) AS completed_30,
              SUM(status='sent' AND expires_at IS NOT NULL AND expires_at <= datetime('now','+3 days')) AS expiring
            FROM contract_envelopes WHERE org_id=?""", (org_id,)).fetchone()
        return {k: (r[k] or 0) for k in ('waiting', 'drafts', 'completed_30', 'expiring')}
    finally:
        db.close()


def delete_draft(env_id, org_id):
    env = get_envelope(env_id, org_id, with_children=False)
    if not env or env['status'] != 'draft':
        return False
    db = _db()
    try:
        db.execute("DELETE FROM contract_envelopes WHERE id=? AND org_id=?", (env_id, org_id))
        db.commit()
    finally:
        db.close()
    shutil.rmtree(envelope_dir(org_id, env['uid']), ignore_errors=True)
    return True


# ── recipients ───────────────────────────────────────────────────────────────

def _decorate_rcpt(row):
    r = dict(row)
    r['status_label'] = RECIPIENT_STATUSES.get(r['status'], r['status'])
    r['initials_txt'] = ''.join(p[0] for p in (r.get('name') or '?').split()[:2]).upper()
    return r


def set_recipients(env_id, org_id, recipients):
    """Reemplaza los destinatarios de un borrador. Mantiene los ids existentes
    (y por tanto sus campos) cuando la fila trae `id`."""
    env = get_envelope(env_id, org_id, with_children=False)
    if not env or env['status'] != 'draft':
        raise ContractError('Solo se pueden editar destinatarios de un borrador.')
    clean = []
    for i, rc in enumerate(recipients):
        name = (rc.get('name') or '').strip()[:120]
        email = (rc.get('email') or '').strip()[:200]
        phone = (rc.get('phone') or '').strip()[:40]
        role = 'cc' if rc.get('role') == 'cc' else 'signer'
        if not name:
            continue
        if not email and not phone:
            raise ContractError(f'"{name}" necesita un email o un teléfono para recibir el enlace.')
        if email and ('@' not in email or '.' not in email.split('@')[-1]):
            raise ContractError(f'El email de "{name}" no es válido.')
        notify_email = 1 if (rc.get('notify_email', True) and email) else 0
        notify_wa = 1 if (rc.get('notify_whatsapp') and phone) else 0
        if not notify_email and not notify_wa:
            notify_email = 1 if email else 0
            notify_wa = 0 if email else 1
        clean.append({
            'id': int(rc['id']) if str(rc.get('id') or '').isdigit() else None,
            'name': name, 'email': email, 'phone': phone, 'role': role,
            'routing_order': max(1, min(int(rc.get('routing_order') or (i + 1)), 20)),
            'notify_email': notify_email, 'notify_whatsapp': notify_wa, 'color_idx': i % 6,
        })
    if not any(c['role'] == 'signer' for c in clean):
        raise ContractError('Agrega al menos un firmante.')
    db = _db()
    try:
        existing = {r['id'] for r in db.execute(
            "SELECT id FROM contract_recipients WHERE envelope_id=?", (env_id,)).fetchall()}
        keep = set()
        for c in clean:
            if c['id'] in existing:
                db.execute("""UPDATE contract_recipients SET name=?, email=?, phone=?, role=?, routing_order=?,
                              notify_email=?, notify_whatsapp=?, color_idx=? WHERE id=?""",
                           (c['name'], c['email'], c['phone'], c['role'], c['routing_order'],
                            c['notify_email'], c['notify_whatsapp'], c['color_idx'], c['id']))
                keep.add(c['id'])
            else:
                cur = db.execute("""INSERT INTO contract_recipients
                    (envelope_id, org_id, name, email, phone, role, routing_order, notify_email,
                     notify_whatsapp, color_idx) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (env_id, org_id, c['name'], c['email'], c['phone'], c['role'], c['routing_order'],
                     c['notify_email'], c['notify_whatsapp'], c['color_idx']))
                keep.add(cur.lastrowid)
        for rid in existing - keep:
            db.execute("DELETE FROM contract_recipients WHERE id=?", (rid,))
        # Un CC no lleva campos
        db.execute("""DELETE FROM contract_fields WHERE envelope_id=? AND recipient_id IN
                      (SELECT id FROM contract_recipients WHERE envelope_id=? AND role='cc')""", (env_id, env_id))
        db.execute("UPDATE contract_envelopes SET updated_at=datetime('now') WHERE id=?", (env_id,))
        db.commit()
    finally:
        db.close()


def get_recipient(rid):
    db = _db()
    try:
        row = db.execute("SELECT * FROM contract_recipients WHERE id=?", (rid,)).fetchone()
        return _decorate_rcpt(row) if row else None
    finally:
        db.close()


def issue_token(rid):
    """Genera un enlace nuevo (invalida el anterior). Devuelve el token en claro."""
    raw = secrets.token_urlsafe(32)
    db = _db()
    try:
        db.execute("UPDATE contract_recipients SET token_hash=? WHERE id=?", (_hash_secret(raw), rid))
        db.commit()
    finally:
        db.close()
    return raw


def find_recipient_by_token(raw):
    if not raw or len(raw) > 100:
        return None
    db = _db()
    try:
        row = db.execute("SELECT * FROM contract_recipients WHERE token_hash=?", (_hash_secret(raw),)).fetchone()
        return _decorate_rcpt(row) if row else None
    finally:
        db.close()


def issue_otp(rid):
    code = f'{secrets.randbelow(1_000_000):06d}'
    expires = (datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MIN)).strftime('%Y-%m-%d %H:%M:%S')
    db = _db()
    try:
        db.execute("UPDATE contract_recipients SET otp_hash=?, otp_expires_at=?, otp_attempts=0 WHERE id=?",
                   (_hash_secret(f'{rid}:{code}'), expires, rid))
        db.commit()
    finally:
        db.close()
    return code


def check_otp(rid, code):
    """Devuelve 'ok' | 'bad' | 'expired' | 'locked'."""
    rc = get_recipient(rid)
    if not rc or not rc.get('otp_hash'):
        return 'expired'
    if rc['otp_attempts'] >= OTP_MAX_ATTEMPTS:
        return 'locked'
    if (rc.get('otp_expires_at') or '') < now_utc():
        return 'expired'
    code = ''.join(ch for ch in (code or '') if ch.isdigit())
    ok = hmac.compare_digest(rc['otp_hash'], _hash_secret(f'{rid}:{code}'))
    db = _db()
    try:
        if ok:
            db.execute("UPDATE contract_recipients SET otp_hash=NULL, otp_attempts=0 WHERE id=?", (rid,))
        else:
            db.execute("UPDATE contract_recipients SET otp_attempts=otp_attempts+1 WHERE id=?", (rid,))
        db.commit()
    finally:
        db.close()
    if ok:
        return 'ok'
    return 'locked' if rc['otp_attempts'] + 1 >= OTP_MAX_ATTEMPTS else 'bad'


def mark_viewed(rid, auth_method=None):
    db = _db()
    try:
        db.execute("""UPDATE contract_recipients SET
                        status=CASE WHEN status='sent' THEN 'viewed' ELSE status END,
                        viewed_at=COALESCE(viewed_at, datetime('now')),
                        auth_method=COALESCE(?, auth_method)
                      WHERE id=?""", (auth_method, rid))
        db.commit()
    finally:
        db.close()


def set_consent(rid):
    db = _db()
    try:
        db.execute("UPDATE contract_recipients SET consent_at=COALESCE(consent_at, datetime('now')) WHERE id=?",
                   (rid,))
        db.commit()
    finally:
        db.close()


# ── fields ───────────────────────────────────────────────────────────────────

def _clamp(v, lo=0.0, hi=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def save_fields(env_id, org_id, fields):
    env = get_envelope(env_id, org_id)
    if not env or env['status'] != 'draft':
        raise ContractError('Solo se pueden editar los campos de un borrador.')
    signer_ids = {r['id'] for r in env['recipients'] if r['role'] == 'signer'}
    rows = []
    for f in fields[:500]:
        ftype = f.get('type')
        rid = int(f.get('recipient_id') or 0)
        page = int(f.get('page') or 0)
        if ftype not in FIELD_TYPE_KEYS or rid not in signer_ids or not (1 <= page <= env['page_count']):
            continue
        w, h = _clamp(f.get('w'), 0.01, 1), _clamp(f.get('h'), 0.008, 1)
        x, y = _clamp(f.get('x'), 0, 1 - w), _clamp(f.get('y'), 0, 1 - h)
        rows.append((env_id, rid, ftype, page, x, y, w, h,
                     0 if ftype == 'checkbox' and not f.get('required') else (1 if f.get('required', True) else 0),
                     (f.get('label') or '')[:80]))
    db = _db()
    try:
        db.execute("DELETE FROM contract_fields WHERE envelope_id=?", (env_id,))
        db.executemany("""INSERT INTO contract_fields
            (envelope_id, recipient_id, type, page, x, y, w, h, required, label)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        db.execute("UPDATE contract_envelopes SET updated_at=datetime('now') WHERE id=?", (env_id,))
        db.commit()
    finally:
        db.close()
    return len(rows)


def validate_ready_to_send(env):
    signers = [r for r in env['recipients'] if r['role'] == 'signer']
    if not signers:
        raise ContractError('Agrega al menos un firmante.')
    for s in signers:
        if not any(f['recipient_id'] == s['id'] and f['type'] == 'signature' for f in env['fields']):
            raise ContractError(f'Coloca al menos un campo de Firma para {s["name"]}.')


# ── workflow ─────────────────────────────────────────────────────────────────

def log_event(env_id, org_id, event, recipient_id=None, actor='', detail='', ip='', user_agent=''):
    db = _db()
    try:
        db.execute("""INSERT INTO contract_events (envelope_id, org_id, recipient_id, event, actor, detail, ip, user_agent)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (env_id, org_id, recipient_id, event, actor or '', (detail or '')[:1000],
                    (ip or '')[:64], (user_agent or '')[:300]))
        db.commit()
    finally:
        db.close()


def _decorate_event(row):
    e = dict(row)
    e['label'] = EVENT_LABELS.get(e['event'], e['event'])
    e['device'] = describe_user_agent(e.get('user_agent') or '')
    return e


def describe_user_agent(ua):
    if not ua:
        return ''
    ua_l = ua.lower()
    browser = ('Edge' if 'edg/' in ua_l else 'Chrome' if 'chrome' in ua_l and 'chromium' not in ua_l
               else 'Firefox' if 'firefox' in ua_l else 'Safari' if 'safari' in ua_l else 'Navegador')
    os_name = ('iOS' if ('iphone' in ua_l or 'ipad' in ua_l) else 'Android' if 'android' in ua_l
               else 'Windows' if 'windows' in ua_l else 'macOS' if 'mac os' in ua_l
               else 'Linux' if 'linux' in ua_l else '')
    return f'{browser} / {os_name}' if os_name else browser


def _activate_next(db, env_id):
    """Activa el siguiente grupo de firmantes. Devuelve ids activados ([] si ya no quedan)."""
    pending = db.execute("""SELECT id, routing_order, status FROM contract_recipients
                            WHERE envelope_id=? AND role='signer' AND status NOT IN ('completed')
                            ORDER BY routing_order, id""", (env_id,)).fetchall()
    if not pending:
        return []
    order = pending[0]['routing_order']
    group = [r for r in pending if r['routing_order'] == order]
    to_activate = [r['id'] for r in group if r['status'] == 'created']
    for rid in to_activate:
        db.execute("UPDATE contract_recipients SET status='sent', sent_at=datetime('now') WHERE id=?", (rid,))
    return to_activate


def send_envelope(env_id, org_id, actor=''):
    """Pasa el borrador a 'sent' y devuelve [(recipient, raw_token)] a notificar."""
    env = get_envelope(env_id, org_id)
    if not env or env['status'] != 'draft':
        raise ContractError('Este sobre ya fue enviado.')
    validate_ready_to_send(env)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=env['expires_days'])).strftime('%Y-%m-%d %H:%M:%S')
    db = _db()
    try:
        db.execute("""UPDATE contract_envelopes SET status='sent', sent_at=datetime('now'),
                      expires_at=?, updated_at=datetime('now') WHERE id=?""", (expires_at, env_id))
        activated = _activate_next(db, env_id)
        db.commit()
    finally:
        db.close()
    log_event(env_id, org_id, 'sent', actor=actor,
              detail=f"{len([r for r in env['recipients'] if r['role'] == 'signer'])} firmante(s) · vence {expires_at[:10]}")
    return [(get_recipient(rid), issue_token(rid)) for rid in activated]


def is_turn(rc):
    return rc and rc['role'] == 'signer' and rc['status'] in ('sent', 'viewed')


def complete_signature(env, rc, values, signature_png, initials_png, signature_kind, ip, ua):
    """Guarda la firma de un destinatario. Devuelve ('next', [(rcpt, token)]) o ('completed', [])."""
    my_fields = [f for f in env['fields'] if f['recipient_id'] == rc['id']]
    stamp = now_utc()
    db = _db()
    try:
        for f in my_fields:
            val = None
            if f['type'] in ('signature', 'initials'):
                val = 'adopted'
            elif f['type'] == 'date_signed':
                val = stamp
            elif f['type'] == 'full_name':
                val = rc['name']
            elif f['type'] == 'email':
                val = rc['email']
            elif f['type'] == 'text':
                val = (str(values.get(str(f['id'])) or '')).strip()[:500]
            elif f['type'] == 'checkbox':
                val = '1' if values.get(str(f['id'])) in (True, '1', 1, 'true', 'on') else '0'
            if f['required'] and f['type'] in ('text',) and not val:
                raise ContractError('Completa todos los campos obligatorios.')
            if f['required'] and f['type'] == 'checkbox' and val != '1':
                raise ContractError('Marca todas las casillas obligatorias.')
            db.execute("UPDATE contract_fields SET value=?, filled_at=? WHERE id=?", (val, stamp, f['id']))
        db.execute("""UPDATE contract_recipients SET status='completed', signature_png=?, initials_png=?,
                      signature_kind=?, signed_at=?, signed_ip=?, signed_ua=? WHERE id=?""",
                   (signature_png, initials_png, signature_kind, stamp, (ip or '')[:64], (ua or '')[:300], rc['id']))
        db.execute("UPDATE contract_envelopes SET updated_at=datetime('now') WHERE id=?", (env['id'],))
        activated = _activate_next(db, env['id'])
        still_pending = db.execute("""SELECT COUNT(*) FROM contract_recipients WHERE envelope_id=?
                                      AND role='signer' AND status!='completed'""", (env['id'],)).fetchone()[0]
        db.commit()
    finally:
        db.close()
    log_event(env['id'], env['org_id'], 'signed', rc['id'], actor=rc['name'],
              detail=f"Firma {signature_kind or ''} · {rc['email'] or rc['phone']}", ip=ip, user_agent=ua)
    if still_pending:
        return 'next', [(get_recipient(rid), issue_token(rid)) for rid in activated]
    return 'completed', []


def mark_completed(env_id, final_sha):
    db = _db()
    try:
        db.execute("""UPDATE contract_envelopes SET status='completed', final_sha256=?, completed_at=datetime('now'),
                      updated_at=datetime('now') WHERE id=?""", (final_sha, env_id))
        db.commit()
    finally:
        db.close()


def decline(env, rc, reason, ip, ua):
    db = _db()
    try:
        db.execute("UPDATE contract_recipients SET status='declined', decline_reason=? WHERE id=?",
                   ((reason or '').strip()[:1000], rc['id']))
        db.execute("UPDATE contract_envelopes SET status='declined', updated_at=datetime('now') WHERE id=?",
                   (env['id'],))
        db.commit()
    finally:
        db.close()
    log_event(env['id'], env['org_id'], 'declined', rc['id'], actor=rc['name'], detail=reason or '', ip=ip, user_agent=ua)


def void(env_id, org_id, reason, actor=''):
    db = _db()
    try:
        n = db.execute("""UPDATE contract_envelopes SET status='voided', void_reason=?, updated_at=datetime('now')
                          WHERE id=? AND org_id=? AND status='sent'""",
                       ((reason or '').strip()[:500], env_id, org_id)).rowcount
        db.execute("UPDATE contract_recipients SET token_hash=NULL WHERE envelope_id=?", (env_id,))
        db.commit()
    finally:
        db.close()
    if n:
        log_event(env_id, org_id, 'voided', actor=actor, detail=reason or '')
    return bool(n)


def expire_overdue():
    """Marca como vencidos los sobres pasados de fecha. Devuelve [(id, org_id)]."""
    db = _db()
    try:
        rows = db.execute("""SELECT id, org_id FROM contract_envelopes
                             WHERE status='sent' AND expires_at IS NOT NULL AND expires_at < datetime('now')""").fetchall()
        for r in rows:
            db.execute("UPDATE contract_envelopes SET status='expired', updated_at=datetime('now') WHERE id=?", (r['id'],))
        db.commit()
    finally:
        db.close()
    for r in rows:
        log_event(r['id'], r['org_id'], 'expired', actor='Sistema')
    return [(r['id'], r['org_id']) for r in rows]


def due_reminders():
    """Firmantes con turno activo a los que toca recordar."""
    db = _db()
    try:
        rows = db.execute("""
            SELECT r.id AS rid, e.id AS env_id, e.org_id FROM contract_recipients r
            JOIN contract_envelopes e ON e.id=r.envelope_id
            WHERE e.status='sent' AND e.reminder_days > 0 AND r.role='signer' AND r.status IN ('sent','viewed')
              AND COALESCE(r.last_reminder_at, r.sent_at) <= datetime('now', '-' || e.reminder_days || ' days')
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def touch_reminder(rid):
    db = _db()
    try:
        db.execute("UPDATE contract_recipients SET last_reminder_at=datetime('now') WHERE id=?", (rid,))
        db.commit()
    finally:
        db.close()
