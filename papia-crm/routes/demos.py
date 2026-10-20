"""Módulo Demos: catálogo de demos por nicho y códigos de acceso por prospecto.

- /demos/...       panel de administración (solo admin / superadmin).
- /api/demos/...   API pública que usan los demos (Lovable) para validar el
                   código y reportar actividad. No expone otros datos del CRM.
"""
import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import quote

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from database import get_db
from services import demo_access as da

demos_bp = Blueprint('demos', __name__, url_prefix='/demos')
demos_api_bp = Blueprint('demos_api', __name__, url_prefix='/api/demos')

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,30}$')
COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
DEFAULT_MESSAGE = ('Hola {nombre}, soy Henrry de PapiaTech. Preparé un demo de cómo se vería '
                   'el sistema para {negocio}: {enlace}\n\nTu código de acceso es {codigo} '
                   '(válido {dias} días). ¿Lo revisas y me dices qué te parece?')


# ═════════════════════════════ Panel admin ══════════════════════════════════
@demos_bp.before_request
def protect():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login', next=request.full_path))
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)
    session.setdefault('demos_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not hmac.compare_digest(request.form.get('csrf', ''), session['demos_csrf']):
            abort(400)


@demos_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def _db_rows(sql, params=(), one=False):
    db = get_db()
    try:
        cur = db.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    finally:
        db.close()


def _demo(ident):
    row = _db_rows('SELECT * FROM demos WHERE id=? AND org_id=?', (ident, g.org_id), one=True)
    if not row:
        abort(404)
    return row


def _link(ident):
    row = _db_rows('''SELECT l.*, d.nombre AS demo_nombre, d.slug AS nicho, d.url AS demo_url,
                             d.color AS demo_color, d.mensaje AS demo_mensaje,
                             c.first_name AS c_first, c.last_name AS c_last
                        FROM demo_links l
                        JOIN demos d ON d.id = l.demo_id
                   LEFT JOIN clients c ON c.id = l.cliente_id
                       WHERE l.id=? AND l.org_id=?''', (ident, g.org_id), one=True)
    if not row:
        abort(404)
    return row


def demo_url(link):
    return f"{link['demo_url'].rstrip('/')}/?c={link['codigo']}"


def link_status(link):
    if not link['activo']:
        return ('inactivo', 'Desactivado')
    if da.from_db(link['vence_el']) < da.now_utc():
        return ('vencido', 'Vencido')
    if not link['ilimitado'] and link['visitas_usadas'] >= link['max_visitas']:
        return ('agotado', 'Agotado')
    if link['visitas_usadas'] > 0:
        return ('abierto', 'Abierto')
    return ('pendiente', 'Sin abrir')


def whatsapp_message(link):
    dias = max(1, (da.from_db(link['vence_el']) - da.now_utc()).days + 1)
    template = (link['demo_mensaje'] or '').strip() or DEFAULT_MESSAGE
    nombre = (link['nombre_prospecto'] or '').split(' ')[0]
    try:
        return template.format(nombre=nombre, negocio=link['negocio'], enlace=demo_url(link),
                               codigo=link['codigo'], dias=dias)
    except (KeyError, IndexError, ValueError):
        return DEFAULT_MESSAGE.format(nombre=nombre, negocio=link['negocio'], enlace=demo_url(link),
                                      codigo=link['codigo'], dias=dias)


def whatsapp_href(link):
    phone = re.sub(r'\D', '', link['telefono'] or '')
    if len(phone) == 10:
        phone = '1' + phone
    if not phone:
        return None
    return f'https://wa.me/{phone}?text={quote(whatsapp_message(link))}'


_LINK_STATS = '''
    SELECT l.*, d.nombre AS demo_nombre, d.slug AS nicho, d.url AS demo_url, d.color AS demo_color,
           d.mensaje AS demo_mensaje,
           (SELECT COUNT(*) FROM demo_logs x WHERE x.link_id=l.id AND x.tipo='entrada')   AS entradas,
           (SELECT COUNT(*) FROM demo_logs x WHERE x.link_id=l.id AND x.tipo='bloqueado') AS bloqueos,
           (SELECT MAX(fecha_hora) FROM demo_logs x WHERE x.link_id=l.id)                 AS ultima_actividad,
           (SELECT COALESCE(SUM(duracion_segundos),0) FROM demo_logs x
             WHERE x.link_id=l.id AND x.tipo IN ('seccion_vista','salida'))              AS segundos,
           (SELECT GROUP_CONCAT(DISTINCT seccion) FROM demo_logs x
             WHERE x.link_id=l.id AND x.tipo='seccion_vista' AND seccion IS NOT NULL)    AS secciones,
           (SELECT dispositivo FROM demo_logs x WHERE x.link_id=l.id AND dispositivo IS NOT NULL
             ORDER BY fecha_hora DESC, id DESC LIMIT 1)                                  AS ultimo_dispositivo
      FROM demo_links l JOIN demos d ON d.id = l.demo_id
'''


def _decorate(rows):
    out = []
    for r in rows:
        item = dict(r)
        item['estado'], item['estado_label'] = link_status(r)
        item['enlace'] = demo_url(r)
        item['mensaje'] = whatsapp_message(r)
        item['wa_href'] = whatsapp_href(r)
        item['secciones_list'] = [s for s in (r['secciones'] or '').split(',') if s]
        out.append(item)
    return out


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f'{seconds} s'
    minutes, sec = divmod(seconds, 60)
    return f'{minutes} min {sec:02d} s' if minutes < 60 else f'{minutes // 60} h {minutes % 60:02d} min'


@demos_bp.app_template_filter('demo_duration')
def demo_duration_filter(value):
    return fmt_duration(value)


@demos_bp.app_context_processor
def demo_globals():
    """Badge del sidebar y tarjeta Demo en el perfil del cliente."""
    def client_links(client_id):
        if session.get('user_role') not in ('admin', 'superadmin'):
            return []
        try:
            return _decorate(_db_rows(_LINK_STATS + ' WHERE l.org_id=? AND l.cliente_id=? ORDER BY l.creado_el DESC',
                                      (g.org_id, client_id)))
        except Exception:  # noqa: BLE001 — tablas aún no migradas
            return []

    unread = 0
    if session.get('logged_in') and session.get('user_role') in ('admin', 'superadmin'):
        try:
            db = get_db()
            try:
                seen = db.execute("SELECT seen_at FROM user_seen WHERE user_id=? AND org_id=? AND section='demos'",
                                  (session.get('user_id') or 0, g.org_id)).fetchone()
                since = seen[0] if seen else '1970-01-01 00:00:00'
                unread = db.execute('''SELECT COUNT(*) FROM demo_logs x JOIN demo_links l ON l.id=x.link_id
                                        WHERE l.org_id=? AND x.tipo='entrada' AND x.fecha_hora > ?''',
                                    (g.org_id, since)).fetchone()[0]
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            unread = 0
    return {'demo_unread': unread, 'client_demo_links': client_links}


def _mark_seen():
    db = get_db()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS user_seen (
            user_id INTEGER NOT NULL, org_id INTEGER NOT NULL, section TEXT NOT NULL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, org_id, section))''')
        db.execute("INSERT OR REPLACE INTO user_seen(user_id, org_id, section, seen_at) "
                   "VALUES (?, ?, 'demos', CURRENT_TIMESTAMP)", (session.get('user_id') or 0, g.org_id))
        db.commit()
    finally:
        db.close()


FILTERS = [('', 'Todos'), ('pendiente', 'Sin abrir'), ('abierto', 'Abiertos'),
           ('agotado', 'Agotados'), ('vencido', 'Vencidos'), ('inactivo', 'Desactivados')]


@demos_bp.route('')
def index():
    demo_filter = request.args.get('demo', type=int)
    estado = request.args.get('estado', '')
    q = (request.args.get('q') or '').strip()
    sql, params = _LINK_STATS + ' WHERE l.org_id=?', [g.org_id]
    if demo_filter:
        sql += ' AND l.demo_id=?'
        params.append(demo_filter)
    if q:
        sql += ' AND (l.negocio LIKE ? OR l.nombre_prospecto LIKE ? OR l.codigo LIKE ?)'
        params += [f'%{q}%'] * 3
    rows = _decorate(_db_rows(sql + ' ORDER BY l.creado_el DESC, l.id DESC LIMIT 300', params))
    counts = {}
    for r in rows:
        counts[r['estado']] = counts.get(r['estado'], 0) + 1
    if estado:
        rows = [r for r in rows if r['estado'] == estado]
    demos = _db_rows('''SELECT d.*, (SELECT COUNT(*) FROM demo_links l WHERE l.demo_id=d.id) AS enlaces
                          FROM demos d WHERE d.org_id=? ORDER BY d.activo DESC, d.nombre''', (g.org_id,))
    clients = _db_rows('''SELECT id, first_name, last_name, company, phone FROM clients
                           WHERE org_id=? ORDER BY created_at DESC LIMIT 500''', (g.org_id,))
    _mark_seen()
    return render_template('demos/index.html', links=rows, demos=demos, clients=clients,
                           counts=counts, total=sum(counts.values()), filters=FILTERS,
                           estado=estado, demo_filter=demo_filter, q=q,
                           default_visits=da.DEFAULT_VISITS, default_days=da.DEFAULT_DAYS)


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@demos_bp.route('/enlaces', methods=['POST'])
def create_link():
    f = request.form
    demo = _demo(_int(f.get('demo_id'), 0, 0, 10**9))
    nombre = (f.get('nombre_prospecto') or '').strip()[:120]
    negocio = (f.get('negocio') or '').strip()[:120]
    telefono = re.sub(r'[^\d+]', '', f.get('telefono') or '')[:20]
    color = (f.get('color') or '').strip()
    if not nombre or not negocio:
        flash('Escribe el nombre del prospecto y el del negocio.', 'danger')
        return redirect(url_for('demos.index'))
    if color and not COLOR_RE.match(color):
        color = ''
    cliente_id = _int(f.get('cliente_id'), None, 1, 10**12) if f.get('cliente_id') else None
    db = get_db()
    try:
        if cliente_id and not db.execute('SELECT 1 FROM clients WHERE id=? AND org_id=?',
                                         (cliente_id, g.org_id)).fetchone():
            abort(404)
        if not cliente_id and f.get('crear_lead'):
            parts = nombre.split(' ', 1)
            cur = db.execute('''INSERT INTO clients(first_name, last_name, email, phone, company, project_type,
                                    project_details, pipeline_stage, source, total_cost, amount_paid, org_id)
                                VALUES (?, ?, '', ?, ?, 'website', ?, 'new_lead', 'demo', 0, 0, ?)''',
                             (parts[0], parts[1] if len(parts) > 1 else '', telefono, negocio,
                              f"Prospecto de demo: {demo['nombre']}", g.org_id))
            cliente_id = cur.lastrowid
        codigo = da.new_code(db, negocio)
        cur = db.execute('''INSERT INTO demo_links(org_id, demo_id, codigo, cliente_id, nombre_prospecto, negocio,
                                telefono, color, max_visitas, vence_el, ilimitado, amarrar_dispositivo)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (g.org_id, demo['id'], codigo, cliente_id, nombre, negocio, telefono, color or None,
                          _int(f.get('max_visitas'), da.DEFAULT_VISITS, 1, 50),
                          da.default_expiry(_int(f.get('dias'), da.DEFAULT_DAYS, 1, 90)),
                          1 if f.get('ilimitado') else 0, 1 if f.get('amarrar_dispositivo') else 0))
        db.commit()
        link_id = cur.lastrowid
    finally:
        db.close()
    flash(f'Código {codigo} creado. Copia el mensaje y envíalo por WhatsApp.', 'success')
    return redirect(url_for('demos.detail', link_id=link_id))


@demos_bp.route('/enlaces/<int:link_id>')
def detail(link_id):
    _link(link_id)  # 404 si no es de esta org
    row = _decorate(_db_rows(_LINK_STATS + ' WHERE l.id=? AND l.org_id=?', (link_id, g.org_id)))[0]
    logs = _db_rows('SELECT * FROM demo_logs WHERE link_id=? ORDER BY fecha_hora DESC, id DESC LIMIT 300',
                    (link_id,))
    client = None
    if row['cliente_id']:
        client = _db_rows('SELECT id, first_name, last_name FROM clients WHERE id=? AND org_id=?',
                          (row['cliente_id'], g.org_id), one=True)
    return render_template('demos/detail.html', link=row, logs=logs, client=client)


@demos_bp.route('/enlaces/<int:link_id>/accion', methods=['POST'])
def link_action(link_id):
    link = _link(link_id)
    accion = request.form.get('accion', '')
    sets = {
        'activar': ('activo=1', ()),
        'desactivar': ('activo=0', ()),
        'ilimitado_on': ('ilimitado=1', ()),
        'ilimitado_off': ('ilimitado=0', ()),
        'reiniciar': ('visitas_usadas=0, dispositivo_amarrado=NULL', ()),
        'extender': ('vence_el=?', (da.to_db(max(da.from_db(link['vence_el']), da.now_utc()) + timedelta(days=7)),)),
        'mas_visitas': ('max_visitas=MIN(max_visitas+2, 50)', ()),
    }
    db = get_db()
    try:
        if accion == 'eliminar':
            db.execute('DELETE FROM demo_logs WHERE link_id=?', (link_id,))
            db.execute('DELETE FROM demo_links WHERE id=? AND org_id=?', (link_id, g.org_id))
            db.commit()
            flash(f"Código {link['codigo']} eliminado.", 'success')
            return redirect(url_for('demos.index'))
        if accion not in sets:
            abort(400)
        clause, params = sets[accion]
        db.execute(f'UPDATE demo_links SET {clause} WHERE id=? AND org_id=?', (*params, link_id, g.org_id))
        db.commit()
    finally:
        db.close()
    flash('Cambio guardado.', 'success')
    return redirect(url_for('demos.detail', link_id=link_id))


@demos_bp.route('/catalogo', methods=['POST'])
def save_demo():
    f = request.form
    nombre = (f.get('nombre') or '').strip()[:80]
    slug = (f.get('slug') or '').strip().lower()
    url = (f.get('url') or '').strip()[:300]
    color = (f.get('color') or '#2A5BFF').strip()
    mensaje = (f.get('mensaje') or '').strip()[:1500]
    if not nombre or not SLUG_RE.match(slug) or not url.startswith('https://') or not COLOR_RE.match(color):
        flash('Revisa el demo: nombre, identificador (minúsculas, números y guiones), URL https:// y color #RRGGBB.',
              'danger')
        return redirect(url_for('demos.index'))
    demo_id = _int(f.get('demo_id'), None, 1, 10**9) if f.get('demo_id') else None
    db = get_db()
    try:
        if demo_id:
            _demo(demo_id)
            db.execute('UPDATE demos SET nombre=?, slug=?, url=?, color=?, mensaje=?, activo=? WHERE id=? AND org_id=?',
                       (nombre, slug, url, color, mensaje, 1 if f.get('activo') else 0, demo_id, g.org_id))
        else:
            if db.execute('SELECT 1 FROM demos WHERE org_id=? AND slug=?', (g.org_id, slug)).fetchone():
                flash(f'Ya existe un demo con el identificador "{slug}".', 'danger')
                return redirect(url_for('demos.index'))
            db.execute('INSERT INTO demos(org_id, slug, nombre, url, color, mensaje) VALUES (?,?,?,?,?,?)',
                       (g.org_id, slug, nombre, url, color, mensaje))
        db.commit()
    finally:
        db.close()
    flash(f'Demo "{nombre}" guardado.', 'success')
    return redirect(url_for('demos.index'))


# ═════════════════════════════ API pública ══════════════════════════════════
@demos_api_bp.after_request
def api_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def _json():
    if request.content_length and request.content_length > 4096:
        abort(413)
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@demos_api_bp.route('/validar', methods=['POST', 'OPTIONS'])
def api_validate():
    if request.method == 'OPTIONS':
        return '', 204
    data = _json()
    codigo = da.normalize_code(data.get('codigo'))
    device_id = str(data.get('deviceId') or '')[:64]
    if not re.match(r'^[A-Za-z0-9\-]{0,64}$', device_id):
        device_id = ''
    dispositivo = da.describe_device(request.headers.get('User-Agent'))

    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        if da.rate_limited(db, da.client_ip(request)):
            db.commit()
            return jsonify(ok=False, error='demasiados_intentos'), 429
        link, existing, avisar_bloqueo = None, None, False
        if codigo:
            link, existing = da.consume_visit(db, codigo, device_id)
        if link:
            da.add_log(db, link['id'], 'entrada', dispositivo=dispositivo)
        elif existing:
            # Un solo aviso de bloqueo por hora y por código, para no llenarte de mensajes.
            avisar_bloqueo = not db.execute(
                "SELECT 1 FROM demo_logs WHERE link_id=? AND tipo='bloqueado' "
                "AND fecha_hora >= datetime('now','-1 hour')", (existing['id'],)).fetchone()
            da.add_log(db, existing['id'], 'bloqueado', dispositivo=dispositivo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if link:
        if link['visitas_usadas'] == 1:
            da.notify_owner(link, 'primera_visita', dispositivo)
        return jsonify(
            ok=True,
            token=da.sign_session(link),
            codigo=link['codigo'],
            negocio=link['negocio'],
            prospecto=link['nombre_prospecto'],
            nicho=link['nicho'],
            color=link['color'] or link['demo_color'],
            visita=link['visitas_usadas'],
            max_visitas=link['max_visitas'],
            ilimitado=bool(link['ilimitado']),
            sesion_minutos=da.SESSION_MINUTES,
            vence_el=da.from_db(link['vence_el']).isoformat(),
        )
    if existing and avisar_bloqueo:
        da.notify_owner(existing, 'bloqueado', dispositivo)
    # Mismo mensaje para código inexistente, vencido o agotado: no revelamos el motivo.
    return jsonify(ok=False, error='acceso_no_valido')


def _session_link(token):
    data = da.verify_session(token)
    if not data:
        return None
    return _db_rows('''SELECT l.*, d.slug AS nicho, d.color AS demo_color FROM demo_links l
                         JOIN demos d ON d.id=l.demo_id WHERE l.id=? AND l.codigo=? AND l.activo=1''',
                    (data['lid'], data['c']), one=True)


@demos_api_bp.route('/sesion', methods=['POST', 'OPTIONS'])
def api_session():
    if request.method == 'OPTIONS':
        return '', 204
    link = _session_link(_json().get('token'))
    if not link:
        return jsonify(ok=False)
    return jsonify(ok=True, codigo=link['codigo'], negocio=link['negocio'], nicho=link['nicho'],
                   color=link['color'] or link['demo_color'])


@demos_api_bp.route('/evento', methods=['POST', 'OPTIONS'])
def api_event():
    if request.method == 'OPTIONS':
        return '', 204
    data = _json()
    link = _session_link(data.get('token'))
    if not link:
        return jsonify(ok=False), 401
    tipo = data.get('tipo')
    if tipo not in ('seccion_vista', 'salida'):
        return jsonify(ok=False, error='tipo_invalido'), 400
    seccion = str(data.get('seccion') or '').strip()[:60] or None
    if seccion and not da.SECTION_RE.match(seccion):
        seccion = re.sub(r'[^\w\- ]', '', seccion)[:60] or None
    duracion = _int(data.get('duracion'), None, 0, 7200) if data.get('duracion') is not None else None
    db = get_db()
    try:
        if da.events_today(db, link['id']) >= da.MAX_EVENTS_PER_DAY:
            return jsonify(ok=False, error='limite'), 429
        da.add_log(db, link['id'], tipo, seccion=seccion,
                   dispositivo=da.describe_device(request.headers.get('User-Agent')), duracion=duracion)
        db.commit()
    finally:
        db.close()
    return jsonify(ok=True)
