"""Correos festivos: configuración por cliente, panel /festivos y baja pública."""
import hmac
import secrets

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from services import festivos

festivos_bp = Blueprint('festivos', __name__)


def _org():
    return g.org_id if hasattr(g, 'org_id') else 1


def _admin_only():
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)


def _csrf_token():
    if 'festivos_csrf' not in session:
        session['festivos_csrf'] = secrets.token_urlsafe(32)
    return session['festivos_csrf']


def _check_csrf():
    token = session.get('festivos_csrf', '')
    sent = request.form.get('csrf') or request.headers.get('X-CSRF-Token', '')
    if not token or not hmac.compare_digest(sent, token):
        abort(400)


# ── Perfil del cliente ───────────────────────────────────────────────────────
@festivos_bp.route('/clients/<int:client_id>/festivos', methods=['POST'])
def save_client(client_id):
    _admin_only()
    _check_csrf()
    try:
        festivos.save_config(client_id, _org(), request.form.get('enabled') == '1',
                             request.form.get('proyecto', ''), request.form.get('logro', ''))
        flash('Correos festivos guardados.', 'success')
    except festivos.FestivosError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('clients.detail', client_id=client_id) + '#festivos')


@festivos_bp.route('/clients/<int:client_id>/festivos/toggle', methods=['POST'])
def toggle_client(client_id):
    """Activar/desactivar con un clic desde la lista de clientes (AJAX)."""
    _admin_only()
    _check_csrf()
    enabled = request.form.get('enabled') == '1'
    try:
        festivos.set_enabled(client_id, _org(), enabled)
    except festivos.FestivosError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 404
    return jsonify({'ok': True, 'enabled': enabled})


# ── Panel ────────────────────────────────────────────────────────────────────
@festivos_bp.route('/festivos')
def index():
    _admin_only()
    org_id = _org()
    proximas = festivos.proximas()
    clave = request.args.get('ocasion') or proximas[0]['clave']
    if clave not in festivos.OCASIONES:
        clave = proximas[0]['clave']
    actual = next(p for p in proximas if p['clave'] == clave)
    return render_template(
        'festivos/index.html',
        proximas=proximas,
        actual=actual,
        envios=festivos.list_envios(org_id, clave=clave, anio=actual['anio']),
        clientes=festivos.list_clients(org_id),
        ajustes=festivos.get_settings(org_id),
        dias_preparacion=festivos.DIAS_PREPARACION,
        csrf=_csrf_token(),
    )


@festivos_bp.route('/festivos/clientes', methods=['POST'])
def save_clients():
    _admin_only()
    _check_csrf()
    festivos.save_bulk(_org(), request.form.getlist('client_ids'))
    flash('Lista de clientes para correos festivos guardada.', 'success')
    return redirect(url_for('festivos.index', ocasion=request.form.get('ocasion')) + '#clientes')


@festivos_bp.route('/festivos/ajustes', methods=['POST'])
def save_settings():
    _admin_only()
    _check_csrf()
    festivos.save_settings(_org(), request.form.get('direccion', ''), request.form.get('aviso_email', ''))
    flash('Ajustes guardados.', 'success')
    return redirect(url_for('festivos.index', ocasion=request.form.get('ocasion')) + '#ajustes')


@festivos_bp.route('/festivos/preparar/<clave>', methods=['POST'])
def prepare_now(clave):
    """Crear ya la lista de pendientes (sin esperar a 3 días antes)."""
    _admin_only()
    _check_csrf()
    if clave not in festivos.OCASIONES:
        abort(404)
    n = festivos.prepare(_org(), clave, festivos.next_send_date(clave))
    flash(f'{n} cliente(s) agregados a la lista de envío.' if n else 'La lista ya estaba al día.', 'success')
    return redirect(url_for('festivos.index', ocasion=clave) + '#envios')


@festivos_bp.route('/festivos/envio/<int:envio_id>/<accion>', methods=['POST'])
def envio_estado(envio_id, accion):
    _admin_only()
    _check_csrf()
    estado = {'omitir': 'omitido', 'restaurar': 'pendiente'}.get(accion)
    if not estado:
        abort(404)
    festivos.set_envio_estado(_org(), envio_id, estado)
    return redirect(url_for('festivos.index', ocasion=request.form.get('ocasion')) + '#envios')


@festivos_bp.route('/festivos/preview/<clave>')
def preview(clave):
    _admin_only()
    if clave not in festivos.OCASIONES:
        abort(404)
    org_id = _org()
    cliente = {'first_name': 'María', 'proyecto': 'tu nueva página web con reservas online',
               'logro': 'tus clientes ya agendan citas solos, a cualquier hora'}
    client_id = request.args.get('client_id', type=int)
    if client_id:
        row = next((c for c in festivos.list_clients(org_id) if c['id'] == client_id), None)
        if row:
            cliente = {**festivos.get_config(client_id, org_id), 'first_name': row['first_name']}
    _, html = festivos.render(clave, cliente, festivos.next_send_date(clave).year, org_id)
    return html


@festivos_bp.route('/festivos/prueba/<clave>', methods=['POST'])
def send_test(clave):
    _admin_only()
    _check_csrf()
    to = festivos.get_settings(_org())['aviso_email']
    try:
        festivos.send_test(_org(), clave, to, client_id=request.form.get('client_id', type=int))
        flash(f'Prueba enviada a {to}.', 'success')
    except Exception as exc:
        flash(f'No se pudo enviar la prueba: {exc}', 'danger')
    return redirect(url_for('festivos.index', ocasion=clave))


# ── Baja pública (enlace en el pie del correo) ───────────────────────────────
@festivos_bp.route('/festivos/baja/<token>', methods=['GET', 'POST'])
def baja(token):
    if request.method == 'POST':
        nombre = festivos.unsubscribe(token)
        return render_template('festivos/baja.html', estado='hecho' if nombre else 'invalido', nombre=nombre)
    return render_template('festivos/baja.html', estado='confirmar', token=token)


@festivos_bp.app_context_processor
def inject_helpers():
    def client_festivos_config(client_id):
        if session.get('user_role') not in ('admin', 'superadmin'):
            return None
        try:
            return festivos.get_config(client_id, _org())
        except Exception:  # noqa: BLE001 — tabla aún no creada
            return None

    def festivos_enabled_ids():
        if session.get('user_role') not in ('admin', 'superadmin'):
            return set()
        try:
            return festivos.enabled_ids(_org())
        except Exception:  # noqa: BLE001
            return set()

    return {'client_festivos_config': client_festivos_config,
            'festivos_enabled_ids': festivos_enabled_ids,
            'festivos_csrf': _csrf_token}
