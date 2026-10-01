"""
Per-client credential vault ("Accesos y contraseñas").

- Each client has its own 4-digit PIN (hashed, never stored in clear).
- Only admins can set the PIN; changing it requires the current PIN.
- Viewing / editing credentials requires unlocking with the PIN; the unlock
  lasts UNLOCK_TTL seconds in the user's session.
- 5 wrong PINs lock that client's vault for 15 minutes.
- All entries (platform, URL, user, password, notes) are encrypted at rest.
- Every unlock / failed attempt / change is written to vault_access_log.
"""
import re
import time
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_db
from services.vault_crypto import (
    encrypt_entry, decrypt_entry, is_configured, VaultNotConfigured,
)

vault_bp = Blueprint('vault', __name__, url_prefix='/clients/<int:client_id>/vault')

UNLOCK_TTL    = 5 * 60          # seconds an unlock stays valid
MAX_ATTEMPTS  = 5
LOCK_MINUTES  = 15
PIN_RE        = re.compile(r'^\d{4}$')
FIELDS        = ('platform', 'url', 'username', 'password', 'notes')
MAX_LEN       = {'platform': 120, 'url': 500, 'username': 200, 'password': 500, 'notes': 2000}


# ── helpers ──────────────────────────────────────────────────────────────────

def _org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


def _is_admin():
    return session.get('user_role') in ('admin', 'superadmin')


def _json(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _client_exists(db, client_id):
    return db.execute("SELECT 1 FROM clients WHERE id=? AND org_id=?",
                      (client_id, _org_id())).fetchone() is not None


def _log(db, client_id, action):
    db.execute(
        """INSERT INTO vault_access_log (client_id, org_id, user_id, username, action, ip)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, _org_id(), session.get('user_id'), session.get('username', ''),
         action, request.headers.get('X-Forwarded-For', request.remote_addr or '')[:64]),
    )


def _unlocked(client_id) -> bool:
    exp = (session.get('vault_unlock') or {}).get(str(client_id))
    return bool(exp and exp > time.time())


def _set_unlocked(client_id):
    data = dict(session.get('vault_unlock') or {})
    now = time.time()
    data = {k: v for k, v in data.items() if v > now}   # drop expired
    data[str(client_id)] = now + UNLOCK_TTL
    session['vault_unlock'] = data


def _clear_unlocked(client_id):
    data = dict(session.get('vault_unlock') or {})
    data.pop(str(client_id), None)
    session['vault_unlock'] = data


def _pin_row(db, client_id):
    return db.execute("SELECT * FROM client_vault_pins WHERE client_id=? AND org_id=?",
                      (client_id, _org_id())).fetchone()


def _locked_until(row):
    if row and row['locked_until']:
        until = datetime.fromisoformat(row['locked_until'])
        if until > datetime.utcnow():
            return until
    return None


def _clean_entry(data: dict) -> dict:
    out = {}
    for f in FIELDS:
        out[f] = str(data.get(f) or '').strip()[:MAX_LEN[f]]
    if out['url'] and not re.match(r'^https?://', out['url'], re.I):
        out['url'] = 'https://' + out['url']
    return out


def _guard(db, client_id):
    """Common checks for endpoints that expose or change credentials."""
    if not _client_exists(db, client_id):
        return _json({'ok': False, 'error': 'Cliente no encontrado'}, 404)
    if not is_configured():
        return _json({'ok': False, 'error': 'La bóveda no está configurada en el servidor (VAULT_KEY).'}, 503)
    if not _unlocked(client_id):
        return _json({'ok': False, 'locked': True, 'error': 'Bóveda bloqueada'}, 403)
    return None


# ── status / PIN ─────────────────────────────────────────────────────────────

@vault_bp.route('/status')
def status(client_id):
    db = get_db()
    try:
        if not _client_exists(db, client_id):
            return _json({'ok': False, 'error': 'Cliente no encontrado'}, 404)
        row = _pin_row(db, client_id)
        count = db.execute("SELECT COUNT(*) FROM client_credentials WHERE client_id=? AND org_id=?",
                           (client_id, _org_id())).fetchone()[0]
        until = _locked_until(row)
        exp = (session.get('vault_unlock') or {}).get(str(client_id))
        return _json({
            'ok': True,
            'configured': is_configured(),
            'has_pin': row is not None,
            'unlocked': _unlocked(client_id),
            'expires_in': max(0, int(exp - time.time())) if exp and _unlocked(client_id) else 0,
            'count': count,
            'locked_until': until.isoformat() + 'Z' if until else None,
            'can_manage_pin': _is_admin(),
        })
    finally:
        db.close()


@vault_bp.route('/pin', methods=['POST'])
def set_pin(client_id):
    """Create the PIN (admin) or change it (admin + current PIN)."""
    if not _is_admin():
        return _json({'ok': False, 'error': 'Solo un administrador puede establecer el PIN.'}, 403)
    data = request.get_json(silent=True) or {}
    new_pin, current = str(data.get('new_pin', '')), str(data.get('current_pin', ''))
    if not PIN_RE.match(new_pin):
        return _json({'ok': False, 'error': 'El PIN debe tener exactamente 4 dígitos.'}, 400)

    db = get_db()
    try:
        if not _client_exists(db, client_id):
            return _json({'ok': False, 'error': 'Cliente no encontrado'}, 404)
        row = _pin_row(db, client_id)
        if row:
            if _locked_until(row):
                return _json({'ok': False, 'error': 'Bóveda bloqueada temporalmente por intentos fallidos.'}, 429)
            if not check_password_hash(row['pin_hash'], current):
                _register_failure(db, client_id, row)
                db.commit()
                return _json({'ok': False, 'error': 'El PIN actual no es correcto.'}, 401)
            db.execute("""UPDATE client_vault_pins SET pin_hash=?, failed_attempts=0, locked_until=NULL,
                          updated_at=CURRENT_TIMESTAMP WHERE client_id=? AND org_id=?""",
                       (generate_password_hash(new_pin), client_id, _org_id()))
            _log(db, client_id, 'pin_changed')
        else:
            db.execute("""INSERT INTO client_vault_pins (client_id, org_id, pin_hash)
                          VALUES (?, ?, ?)""",
                       (client_id, _org_id(), generate_password_hash(new_pin)))
            _log(db, client_id, 'pin_created')
        db.commit()
        _set_unlocked(client_id)
        return _json({'ok': True})
    finally:
        db.close()


@vault_bp.route('/pin/reset', methods=['POST'])
def reset_pin(client_id):
    """Forgotten PIN: superadmin only. Credentials are kept."""
    if session.get('user_role') != 'superadmin':
        return _json({'ok': False, 'error': 'Solo el superadmin puede restablecer el PIN.'}, 403)
    db = get_db()
    try:
        if not _client_exists(db, client_id):
            return _json({'ok': False, 'error': 'Cliente no encontrado'}, 404)
        db.execute("DELETE FROM client_vault_pins WHERE client_id=? AND org_id=?", (client_id, _org_id()))
        _log(db, client_id, 'pin_reset')
        db.commit()
        _clear_unlocked(client_id)
        return _json({'ok': True})
    finally:
        db.close()


def _register_failure(db, client_id, row):
    attempts = (row['failed_attempts'] or 0) + 1
    locked = None
    if attempts >= MAX_ATTEMPTS:
        locked = (datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)).isoformat()
        attempts = 0
    db.execute("UPDATE client_vault_pins SET failed_attempts=?, locked_until=? WHERE client_id=? AND org_id=?",
               (attempts, locked, client_id, _org_id()))
    _log(db, client_id, 'unlock_failed' + ('_locked' if locked else ''))
    return locked


@vault_bp.route('/unlock', methods=['POST'])
def unlock(client_id):
    pin = str((request.get_json(silent=True) or {}).get('pin', ''))
    db = get_db()
    try:
        if not _client_exists(db, client_id):
            return _json({'ok': False, 'error': 'Cliente no encontrado'}, 404)
        row = _pin_row(db, client_id)
        if not row:
            return _json({'ok': False, 'error': 'Este cliente aún no tiene PIN.'}, 400)
        until = _locked_until(row)
        if until:
            mins = max(1, int((until - datetime.utcnow()).total_seconds() // 60) + 1)
            return _json({'ok': False, 'error': f'Demasiados intentos. Intenta en {mins} min.'}, 429)
        if not PIN_RE.match(pin) or not check_password_hash(row['pin_hash'], pin):
            locked = _register_failure(db, client_id, row)
            db.commit()
            left = MAX_ATTEMPTS - (0 if locked else (_pin_row(db, client_id)['failed_attempts']))
            msg = (f'PIN incorrecto. Bóveda bloqueada {LOCK_MINUTES} min.' if locked
                   else f'PIN incorrecto. Te quedan {left} intento(s).')
            return _json({'ok': False, 'error': msg}, 429 if locked else 401)
        db.execute("UPDATE client_vault_pins SET failed_attempts=0, locked_until=NULL WHERE client_id=? AND org_id=?",
                   (client_id, _org_id()))
        _log(db, client_id, 'unlocked')
        db.commit()
        _set_unlocked(client_id)
        return _json({'ok': True, 'expires_in': UNLOCK_TTL})
    finally:
        db.close()


@vault_bp.route('/lock', methods=['POST'])
def lock(client_id):
    _clear_unlocked(client_id)
    return _json({'ok': True})


# ── credentials CRUD ─────────────────────────────────────────────────────────

@vault_bp.route('/entries')
def list_entries(client_id):
    db = get_db()
    try:
        err = _guard(db, client_id)
        if err:
            return err
        rows = db.execute("""SELECT id, data_enc, updated_at FROM client_credentials
                             WHERE client_id=? AND org_id=? ORDER BY id""",
                          (client_id, _org_id())).fetchall()
        entries = []
        for r in rows:
            e = decrypt_entry(r['data_enc'])
            e['id'] = r['id']
            e['updated_at'] = r['updated_at']
            entries.append(e)
        entries.sort(key=lambda e: (e.get('platform') or '').lower())
        _log(db, client_id, 'viewed')
        db.commit()
        exp = (session.get('vault_unlock') or {}).get(str(client_id), 0)
        return _json({'ok': True, 'entries': entries, 'expires_in': max(0, int(exp - time.time()))})
    except VaultNotConfigured as exc:
        return _json({'ok': False, 'error': str(exc)}, 503)
    finally:
        db.close()


@vault_bp.route('/entries', methods=['POST'])
def create_entry(client_id):
    db = get_db()
    try:
        err = _guard(db, client_id)
        if err:
            return err
        entry = _clean_entry(request.get_json(silent=True) or {})
        if not entry['platform']:
            return _json({'ok': False, 'error': 'Indica la plataforma o página.'}, 400)
        cur = db.execute("INSERT INTO client_credentials (client_id, org_id, data_enc) VALUES (?, ?, ?)",
                         (client_id, _org_id(), encrypt_entry(entry)))
        _log(db, client_id, f'created:{cur.lastrowid}')
        db.commit()
        return _json({'ok': True, 'id': cur.lastrowid})
    except VaultNotConfigured as exc:
        return _json({'ok': False, 'error': str(exc)}, 503)
    finally:
        db.close()


@vault_bp.route('/entries/<int:entry_id>', methods=['POST'])
def update_entry(client_id, entry_id):
    db = get_db()
    try:
        err = _guard(db, client_id)
        if err:
            return err
        entry = _clean_entry(request.get_json(silent=True) or {})
        if not entry['platform']:
            return _json({'ok': False, 'error': 'Indica la plataforma o página.'}, 400)
        cur = db.execute("""UPDATE client_credentials SET data_enc=?, updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND client_id=? AND org_id=?""",
                         (encrypt_entry(entry), entry_id, client_id, _org_id()))
        if not cur.rowcount:
            return _json({'ok': False, 'error': 'Registro no encontrado'}, 404)
        _log(db, client_id, f'updated:{entry_id}')
        db.commit()
        return _json({'ok': True})
    except VaultNotConfigured as exc:
        return _json({'ok': False, 'error': str(exc)}, 503)
    finally:
        db.close()


@vault_bp.route('/entries/<int:entry_id>/delete', methods=['POST'])
def delete_entry(client_id, entry_id):
    db = get_db()
    try:
        err = _guard(db, client_id)
        if err:
            return err
        cur = db.execute("DELETE FROM client_credentials WHERE id=? AND client_id=? AND org_id=?",
                         (entry_id, client_id, _org_id()))
        if not cur.rowcount:
            return _json({'ok': False, 'error': 'Registro no encontrado'}, 404)
        _log(db, client_id, f'deleted:{entry_id}')
        db.commit()
        return _json({'ok': True})
    finally:
        db.close()
