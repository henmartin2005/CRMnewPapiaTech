"""Pestaña "Gasto de API de Google Maps" en el perfil del cliente (solo admin)."""
import hmac
import secrets

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, session, url_for

from models.client import get_client
from services import gcp_costs

gcp_costs_bp = Blueprint('gcp_costs', __name__, url_prefix='/clients')


def _org():
    return g.org_id if hasattr(g, 'org_id') else 1


def _admin_only():
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)


def _client_or_404(client_id):
    if not get_client(client_id, org_id=_org()):
        abort(404)


def _check_csrf():
    token = session.get('gcp_costs_csrf', '')
    if not token or not hmac.compare_digest(request.form.get('csrf', ''), token):
        abort(400)


@gcp_costs_bp.route('/<int:client_id>/gasto-maps.json')
def costs_json(client_id):
    _admin_only()
    _client_or_404(client_id)
    try:
        data = gcp_costs.get_costs(client_id, _org(), force=request.args.get('refresh') == '1')
    except gcp_costs.CostsError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 200
    resp = jsonify({'ok': True, 'data': data})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@gcp_costs_bp.route('/<int:client_id>/gasto-maps/vincular', methods=['POST'])
def link_project(client_id):
    _admin_only()
    _client_or_404(client_id)
    _check_csrf()
    try:
        gcp_costs.set_project(client_id, _org(), request.form.get('gcp_project_id'))
        flash('Proyecto de Google Cloud vinculado. El gasto aparece en el perfil del cliente.', 'success')
    except gcp_costs.CostsError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('clients.detail', client_id=client_id) + '#gasto-maps')


@gcp_costs_bp.route('/<int:client_id>/gasto-maps/desvincular', methods=['POST'])
def unlink_project(client_id):
    _admin_only()
    _client_or_404(client_id)
    _check_csrf()
    gcp_costs.unlink(client_id, _org())
    flash('Se quitó el seguimiento de gasto de Google Maps de este cliente.', 'success')
    return redirect(url_for('clients.detail', client_id=client_id))


@gcp_costs_bp.app_context_processor
def inject_helpers():
    def client_gmaps_config(client_id):
        if session.get('user_role') not in ('admin', 'superadmin'):
            return None
        try:
            return gcp_costs.get_config(client_id, _org())
        except Exception:  # noqa: BLE001 — tabla aún no creada
            return None

    def gcp_costs_csrf():
        if 'gcp_costs_csrf' not in session:
            session['gcp_costs_csrf'] = secrets.token_urlsafe(32)
        return session['gcp_costs_csrf']

    return {'client_gmaps_config': client_gmaps_config, 'gcp_costs_csrf': gcp_costs_csrf}
