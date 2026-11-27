"""Correos festivos automáticos (Navidad, Año Nuevo, San Valentín, 4 de Julio, Thanksgiving).

Reglas:
- Nadie recibe nada por defecto: solo los clientes marcados en su perfil
  (client_festivos.enabled = 1) y que no se hayan dado de baja.
- 3 días antes de cada fecha se crean los envíos 'pendiente' y se avisa al admin
  por email para que revise en /festivos (puede omitir clientes o mandarse una prueba).
- El día del envío salen solo los que siguen 'pendiente'.
- UNIQUE(client_id, ocasion, anio) impide duplicados aunque la tarea corra dos veces.
- Todo se envía con services.mailer.send_email(), así queda copia en Emails → Enviados.

La tarea diaria es send_festivos.py (ver DEPLOY_FESTIVOS.md).
"""
import os
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_db

TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))
DIAS_PREPARACION = 3
TEMPLATE_DIR = 'emails/festivos'


def nth_weekday(year, month, weekday, n):
    """n-ésimo día de la semana del mes (weekday: 0=lunes ... 6=domingo)."""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


# fecha_envio(año) → date. Navidad y Thanksgiving salen un día antes del festivo.
OCASIONES = {
    'navidad': {
        'nombre': 'Navidad', 'emoji': '🎄',
        'asunto': '🎄 Feliz Navidad, {nombre}',
        'preheader': 'Gracias por confiar en nosotros este año.',
        'fecha_envio': lambda y: date(y, 12, 24),
    },
    'ano_nuevo': {
        'nombre': 'Año Nuevo', 'emoji': '🥂',
        'asunto': '🎉 {nombre}, {anio} es tu año',
        'preheader': 'Lo que lograste el año pasado es solo el comienzo.',
        'fecha_envio': lambda y: date(y, 1, 1),
    },
    'san_valentin': {
        'nombre': 'San Valentín', 'emoji': '💌',
        'asunto': '💌 Feliz Día del Amor y la Amistad, {nombre}',
        'preheader': 'Hoy celebramos a quienes nos eligen.',
        'fecha_envio': lambda y: date(y, 2, 14),
    },
    'cuatro_julio': {
        'nombre': '4 de Julio', 'emoji': '🇺🇸',
        'asunto': '🇺🇸 Feliz 4 de Julio, {nombre}',
        'preheader': 'Celebramos la libertad de emprender.',
        'fecha_envio': lambda y: date(y, 7, 4),
    },
    'thanksgiving': {
        'nombre': 'Thanksgiving', 'emoji': '🦃',
        'asunto': '🦃 Gracias por elegirnos, {nombre}',
        'preheader': 'Este año tú estás en nuestra lista de agradecimientos.',
        'fecha_envio': lambda y: nth_weekday(y, 11, 3, 4) - timedelta(days=1),
    },
}

ESTADOS = ('pendiente', 'enviado', 'omitido', 'error')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_festivos (
    client_id        INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    org_id           INTEGER NOT NULL DEFAULT 1,
    enabled          INTEGER NOT NULL DEFAULT 0,
    proyecto         TEXT    NOT NULL DEFAULT '',
    logro            TEXT    NOT NULL DEFAULT '',
    unsubscribe_token TEXT   UNIQUE,
    unsubscribed_at  TEXT,
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS festivos_envios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL DEFAULT 1,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ocasion     TEXT    NOT NULL,
    anio        INTEGER NOT NULL,
    fecha_envio TEXT    NOT NULL,
    estado      TEXT    NOT NULL DEFAULT 'pendiente',
    enviado_en  TEXT,
    error       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (client_id, ocasion, anio)
);
CREATE INDEX IF NOT EXISTS idx_festivos_envios_fecha ON festivos_envios(org_id, fecha_envio, estado);
CREATE TABLE IF NOT EXISTS festivos_settings (
    org_id      INTEGER PRIMARY KEY,
    direccion   TEXT NOT NULL DEFAULT '',
    aviso_email TEXT NOT NULL DEFAULT '',
    avisos_enviados TEXT NOT NULL DEFAULT ''
);
"""


class FestivosError(Exception):
    pass


def migrate():
    db = get_db()
    try:
        db.executescript(_SCHEMA)
        db.commit()
    finally:
        db.close()


def today_local():
    return datetime.now(TZ).date()


# ── Fechas ────────────────────────────────────────────────────────────────────
def next_send_date(clave, today=None):
    """Próxima fecha de envío (hoy incluido) de una ocasión."""
    today = today or today_local()
    f = OCASIONES[clave]['fecha_envio'](today.year)
    return f if f >= today else OCASIONES[clave]['fecha_envio'](today.year + 1)


def anio_de(clave, fecha):
    """Año del festivo al que corresponde un envío (Año Nuevo 1/1 → ese año)."""
    return fecha.year


def proximas(today=None):
    """Ocasiones ordenadas por su próxima fecha de envío."""
    today = today or today_local()
    out = []
    for clave, o in OCASIONES.items():
        f = next_send_date(clave, today)
        out.append({'clave': clave, 'nombre': o['nombre'], 'emoji': o['emoji'],
                    'fecha_envio': f, 'dias': (f - today).days, 'anio': anio_de(clave, f)})
    return sorted(out, key=lambda x: x['fecha_envio'])


# ── Configuración por cliente ────────────────────────────────────────────────
def _default_cfg(client_id, org_id):
    return {'client_id': client_id, 'org_id': org_id, 'enabled': 0, 'proyecto': '', 'logro': '',
            'unsubscribe_token': None, 'unsubscribed_at': None}


def get_config(client_id, org_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM client_festivos WHERE client_id=? AND org_id=?",
                         (client_id, org_id)).fetchone()
    finally:
        db.close()
    return dict(row) if row else _default_cfg(client_id, org_id)


def _clean_text(value, limit=200):
    return ' '.join((value or '').split())[:limit]


def save_config(client_id, org_id, enabled, proyecto='', logro=''):
    """Guarda la configuración de un cliente. Activarlo a mano anula una baja anterior."""
    enabled = 1 if enabled else 0
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM clients WHERE id=? AND org_id=?", (client_id, org_id)).fetchone():
            raise FestivosError('Cliente no encontrado.')
        db.execute(
            "INSERT INTO client_festivos (client_id, org_id, enabled, proyecto, logro, unsubscribe_token) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(client_id) DO UPDATE SET enabled=excluded.enabled, proyecto=excluded.proyecto, "
            "logro=excluded.logro, updated_at=datetime('now'), "
            "unsubscribed_at=CASE WHEN excluded.enabled=1 THEN NULL ELSE client_festivos.unsubscribed_at END, "
            "unsubscribe_token=COALESCE(client_festivos.unsubscribe_token, excluded.unsubscribe_token)",
            (client_id, org_id, enabled, _clean_text(proyecto), _clean_text(logro), secrets.token_urlsafe(24)),
        )
        if not enabled:  # si se desactiva, sus envíos pendientes quedan omitidos
            db.execute("UPDATE festivos_envios SET estado='omitido' WHERE client_id=? AND org_id=? "
                       "AND estado='pendiente'", (client_id, org_id))
        db.commit()
    finally:
        db.close()


def set_enabled(client_id, org_id, enabled):
    cfg = get_config(client_id, org_id)
    save_config(client_id, org_id, enabled, cfg['proyecto'], cfg['logro'])


def save_bulk(org_id, enabled_ids):
    """Panel /festivos: marca exactamente los clientes en enabled_ids y desmarca al resto."""
    enabled_ids = {int(i) for i in enabled_ids}
    for c in list_clients(org_id):
        if bool(c['enabled']) != (c['id'] in enabled_ids):
            set_enabled(c['id'], org_id, c['id'] in enabled_ids)


def list_clients(org_id):
    """Todos los clientes de la org con su configuración de festivos."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT c.id, c.first_name, c.last_name, c.email, c.company, "
            "COALESCE(f.enabled,0) AS enabled, COALESCE(f.proyecto,'') AS proyecto, "
            "COALESCE(f.logro,'') AS logro, f.unsubscribed_at "
            "FROM clients c LEFT JOIN client_festivos f ON f.client_id=c.id "
            "WHERE c.org_id=? ORDER BY COALESCE(f.enabled,0) DESC, c.first_name, c.last_name",
            (org_id,),
        ).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


def enabled_ids(org_id):
    return {c['id'] for c in list_clients(org_id) if c['enabled']}


# ── Ajustes de la org ────────────────────────────────────────────────────────
def get_settings(org_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM festivos_settings WHERE org_id=?", (org_id,)).fetchone()
    finally:
        db.close()
    s = dict(row) if row else {'org_id': org_id, 'direccion': '', 'aviso_email': '', 'avisos_enviados': ''}
    s['direccion'] = s['direccion'] or os.getenv('COMPANY_ADDRESS', '')
    s['aviso_email'] = s['aviso_email'] or os.getenv('GOOGLE_ADMIN_EMAIL', 'henrry@papiatech.com')
    return s


def save_settings(org_id, direccion, aviso_email):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO festivos_settings (org_id, direccion, aviso_email) VALUES (?, ?, ?) "
            "ON CONFLICT(org_id) DO UPDATE SET direccion=excluded.direccion, aviso_email=excluded.aviso_email",
            (org_id, _clean_text(direccion, 250), _clean_text(aviso_email, 200)),
        )
        db.commit()
    finally:
        db.close()


def _mark_aviso(org_id, key):
    """Registra que ya se avisó al admin para esta ocasión/año (evita repetir el aviso)."""
    s = get_settings(org_id)
    done = set(filter(None, (s.get('avisos_enviados') or '').split(',')))
    if key in done:
        return False
    done.add(key)
    db = get_db()
    try:
        db.execute("INSERT INTO festivos_settings (org_id, avisos_enviados) VALUES (?, ?) "
                   "ON CONFLICT(org_id) DO UPDATE SET avisos_enviados=excluded.avisos_enviados",
                   (org_id, ','.join(sorted(done))))
        db.commit()
    finally:
        db.close()
    return True


# ── Render ───────────────────────────────────────────────────────────────────
def _base_url(org_id):
    try:
        from services.contract_notify import _base_url as base
        return base(org_id)
    except Exception:
        return (os.getenv('APP_BASE_URL') or 'https://datos.papiatech.com').rstrip('/')


def render(clave, cliente, anio, org_id=1):
    """Devuelve (asunto, html). cliente: dict con first_name, proyecto, logro, unsubscribe_token."""
    from flask import current_app

    o = OCASIONES[clave]
    nombre = (cliente.get('first_name') or '').strip() or 'amigo'
    token = cliente.get('unsubscribe_token')
    unsubscribe_url = f"{_base_url(org_id)}/festivos/baja/{token}" if token else f"{_base_url(org_id)}/festivos/baja/prueba"
    asunto = o['asunto'].format(nombre=nombre, anio=anio)
    html = current_app.jinja_env.get_template(f'{TEMPLATE_DIR}/{clave}.html').render(
        nombre=nombre, anio=anio, asunto=asunto, preheader=o['preheader'],
        proyecto=(cliente.get('proyecto') or '').strip(),
        logro=(cliente.get('logro') or '').strip(),
        direccion_fisica=get_settings(org_id)['direccion'],
        unsubscribe_url=unsubscribe_url,
    )
    return asunto, html


# ── Envíos ───────────────────────────────────────────────────────────────────
def prepare(org_id, clave, fecha):
    """Crea los envíos 'pendiente' de una ocasión para los clientes marcados (idempotente)."""
    anio = anio_de(clave, fecha)
    db = get_db()
    try:
        cur = db.execute(
            "INSERT OR IGNORE INTO festivos_envios (org_id, client_id, ocasion, anio, fecha_envio) "
            "SELECT c.org_id, c.id, ?, ?, ? FROM clients c JOIN client_festivos f ON f.client_id=c.id "
            "WHERE c.org_id=? AND f.enabled=1 AND f.unsubscribed_at IS NULL AND trim(c.email)<>''",
            (clave, anio, fecha.isoformat(), org_id),
        )
        db.commit()
        return cur.rowcount
    finally:
        db.close()


def list_envios(org_id, clave=None, anio=None, estado=None):
    sql = ("SELECT e.*, c.first_name, c.last_name, c.email, c.company, "
           "COALESCE(f.proyecto,'') AS proyecto, COALESCE(f.logro,'') AS logro "
           "FROM festivos_envios e JOIN clients c ON c.id=e.client_id "
           "LEFT JOIN client_festivos f ON f.client_id=e.client_id WHERE e.org_id=?")
    args = [org_id]
    for col, val in (('e.ocasion', clave), ('e.anio', anio), ('e.estado', estado)):
        if val is not None:
            sql += f" AND {col}=?"
            args.append(val)
    sql += " ORDER BY e.fecha_envio DESC, c.first_name"
    db = get_db()
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


def set_envio_estado(org_id, envio_id, estado):
    """Omitir / volver a poner en pendiente un envío que aún no salió."""
    if estado not in ('pendiente', 'omitido'):
        raise FestivosError('Estado no válido.')
    db = get_db()
    try:
        cur = db.execute("UPDATE festivos_envios SET estado=?, error=NULL WHERE id=? AND org_id=? "
                         "AND estado IN ('pendiente','omitido','error')", (estado, envio_id, org_id))
        db.commit()
        return cur.rowcount
    finally:
        db.close()


def _send(org_id, to_email, asunto, html, client_id=None):
    from services.mailer import send_email
    return send_email(org_id, to_email, asunto, html, client_id=client_id)


def send_pending(org_id, clave, fecha, pause=1.5):
    """Envía los pendientes de una ocasión. Devuelve dict sent/skipped/errors."""
    import time

    sent, skipped, errors = [], [], []
    for e in list_envios(org_id, clave=clave, anio=anio_de(clave, fecha), estado='pendiente'):
        label = f"{e['first_name']} {e['last_name']}".strip()
        cfg = get_config(e['client_id'], org_id)
        if not cfg['enabled'] or cfg['unsubscribed_at'] or not (e['email'] or '').strip():
            set_envio_estado(org_id, e['id'], 'omitido')
            skipped.append(f"{label}: desactivado o sin email")
            continue
        try:
            asunto, html = render(clave, {**e, **cfg}, e['anio'], org_id)
            _send(org_id, e['email'].strip(), asunto, html, client_id=e['client_id'])
            _update_envio(e['id'], 'enviado')
            sent.append(label)
        except Exception as exc:  # sigue con los demás
            _update_envio(e['id'], 'error', str(exc)[:300])
            errors.append(f"{label}: {exc}")
        if pause:
            time.sleep(pause)
    return {'sent': sent, 'skipped': skipped, 'errors': errors}


def _update_envio(envio_id, estado, error=None):
    db = get_db()
    try:
        db.execute("UPDATE festivos_envios SET estado=?, error=?, "
                   "enviado_en=CASE WHEN ?='enviado' THEN datetime('now') ELSE enviado_en END WHERE id=?",
                   (estado, error, estado, envio_id))
        db.commit()
    finally:
        db.close()


def send_test(org_id, clave, to_email, client_id=None):
    """Manda una prueba al admin. Con client_id usa los datos reales de ese cliente."""
    if clave not in OCASIONES:
        raise FestivosError('Ocasión no válida.')
    fecha = next_send_date(clave)
    cliente = {'first_name': 'Henrry', 'proyecto': 'tu nueva página web con reservas online',
               'logro': 'tus clientes ya agendan citas solos, a cualquier hora'}
    if client_id:
        db = get_db()
        try:
            row = db.execute("SELECT first_name FROM clients WHERE id=? AND org_id=?", (client_id, org_id)).fetchone()
        finally:
            db.close()
        if row:
            cliente = {**get_config(client_id, org_id), 'first_name': row['first_name']}
    asunto, html = render(clave, cliente, anio_de(clave, fecha), org_id)
    _send(org_id, to_email, f"[PRUEBA] {asunto}", html)


def unsubscribe(token):
    """Baja desde el enlace del correo. Devuelve el nombre del cliente o None."""
    if not token or len(token) < 16:
        return None
    db = get_db()
    try:
        row = db.execute("SELECT f.client_id, f.org_id, c.first_name FROM client_festivos f "
                         "JOIN clients c ON c.id=f.client_id WHERE f.unsubscribe_token=?", (token,)).fetchone()
        if not row:
            return None
        db.execute("UPDATE client_festivos SET enabled=0, unsubscribed_at=datetime('now') WHERE client_id=?",
                   (row['client_id'],))
        db.execute("UPDATE festivos_envios SET estado='omitido' WHERE client_id=? AND estado='pendiente'",
                   (row['client_id'],))
        db.commit()
        return row['first_name']
    finally:
        db.close()


# ── Tarea diaria ─────────────────────────────────────────────────────────────
def _org_ids():
    db = get_db()
    try:
        return [r['org_id'] for r in db.execute(
            "SELECT DISTINCT org_id FROM client_festivos WHERE enabled=1").fetchall()]
    finally:
        db.close()


def _aviso_admin(org_id, clave, fecha):
    """Email al admin 3 días antes: cuántos saldrán y enlace al panel para revisar."""
    s = get_settings(org_id)
    if not s['aviso_email'] or not _mark_aviso(org_id, f"{clave}-{anio_de(clave, fecha)}"):
        return
    o = OCASIONES[clave]
    envios = list_envios(org_id, clave=clave, anio=anio_de(clave, fecha), estado='pendiente')
    filas = ''.join(f"<li>{e['first_name']} {e['last_name']} — {e['email']}</li>" for e in envios)
    url = f"{_base_url(org_id)}/festivos?ocasion={clave}"
    html = (f"<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#1f2937'>"
            f"<p>Hola Henrry:</p><p>El <strong>{fecha:%d/%m/%Y}</strong> sale el correo de "
            f"<strong>{o['emoji']} {o['nombre']}</strong> a <strong>{len(envios)} cliente(s)</strong>:</p>"
            f"<ul>{filas or '<li>(ninguno)</li>'}</ul>"
            f"<p>Revisa, omite a quien no quieras o mándate una prueba aquí:<br>"
            f"<a href='{url}'>{url}</a></p></div>")
    _send(org_id, s['aviso_email'], f"{o['emoji']} {o['nombre']}: {len(envios)} correo(s) saldrán el {fecha:%d/%m}", html)


def run_daily(today=None, dry_run=False):
    """Prepara (3 días antes) y envía (el día) los correos festivos de todas las orgs."""
    today = today or today_local()
    report = []
    for org_id in _org_ids():
        for clave in OCASIONES:
            fecha = next_send_date(clave, today)
            dias = (fecha - today).days
            if dias > DIAS_PREPARACION:
                continue
            if dry_run:
                n = len(enabled_ids(org_id))
                report.append(f"[org {org_id}] {clave} {fecha} (en {dias} días): {n} cliente(s) marcados")
                continue
            nuevos = prepare(org_id, clave, fecha)
            report.append(f"[org {org_id}] {clave} {fecha}: {nuevos} envío(s) nuevos en pendiente")
            if dias >= 1:
                try:
                    _aviso_admin(org_id, clave, fecha)
                except Exception as exc:
                    report.append(f"  ✗ aviso al admin: {exc}")
            if dias == 0:
                res = send_pending(org_id, clave, fecha)
                report += [f"  ✓ enviado: {s}" for s in res['sent']]
                report += [f"  - omitido: {s}" for s in res['skipped']]
                report += [f"  ✗ error: {s}" for s in res['errors']]
    return report
