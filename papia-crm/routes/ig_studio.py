"""Pestaña Instagram Studio: analítica, comentarios con IA, agentes y estrategia semanal."""
import hmac
import logging
import secrets
import threading

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from services import ig_studio as igs

log = logging.getLogger(__name__)

ig_studio_bp = Blueprint('ig_studio', __name__, url_prefix='/instagram-studio')

_sync_running = set()


def _org():
    return getattr(g, 'org_id', 1) or 1


@ig_studio_bp.before_request
def protect():
    if request.endpoint == 'ig_studio.oauth_callback':
        return None
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)
    session.setdefault('igs_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not hmac.compare_digest(request.headers.get('X-IGS-CSRF', ''), session['igs_csrf']):
            return jsonify(error='Sesión vencida. Recarga la página.'), 403
    return None


@ig_studio_bp.errorhandler(igs.GraphError)
def graph_error(exc):
    return jsonify(error=f'Instagram respondió: {exc}'), 502


@ig_studio_bp.errorhandler(RuntimeError)
def runtime_error(exc):
    log.exception('Instagram Studio')
    return jsonify(error=str(exc)[:300]), 502


@ig_studio_bp.errorhandler(ValueError)
def bad_value(exc):
    return jsonify(error=str(exc)), 400


def _common(active):
    org = _org()
    accounts = igs.list_accounts(org)
    acct = request.args.get('acct', '').strip()
    if acct and not any(a['ig_user_id'] == acct for a in accounts):
        acct = ''
    return {
        'active_tab': active,
        'accounts': accounts,
        'acct': acct,
        'configured': igs.is_configured(),
        'sync_running': org in _sync_running,
        'csrf': session.get('igs_csrf', ''),
    }


# ── Páginas ─────────────────────────────────────────────────────────────────

@ig_studio_bp.route('')
def analitica():
    ctx = _common('analitica')
    org, acct = _org(), ctx['acct'] or None
    days = request.args.get('days', '30')
    days = int(days) if days in ('7', '30', '90') else 30
    order = request.args.get('order', 'er')
    series = igs.growth_series(org, acct, days)
    report = igs.latest_report(org)
    return render_template(
        'ig_studio/analitica.html', **ctx,
        days=days, order=order,
        ov=igs.overview(org, acct, days),
        chart=igs.chart_svg_data(series),
        formats=igs.format_performance(org, acct),
        slots=igs.best_slots(org, acct),
        posts=igs.top_posts(org, acct, order),
        insights=(report or {}).get('data', {}).get('insights', []),
    )


@ig_studio_bp.route('/comentarios')
def comentarios():
    ctx = _common('comentarios')
    org = _org()
    status = request.args.get('status', '')
    if status not in igs.STATUS_LABELS:
        status = ''
    agents = igs.list_agents(org)
    return render_template(
        'ig_studio/comentarios.html', **ctx,
        status=status,
        status_labels=igs.STATUS_LABELS,
        comments=igs.list_comments(org, status or None, ctx['acct'] or None),
        stats=igs.comment_stats(org, ctx['acct'] or None),
        agents=[a for a in agents if a['kind'] == 'comments'],
        default_agent=next((a for a in agents if a['kind'] == 'comments' and a['is_default']), None),
        media=igs.media_for_assignment(org),
    )


@ig_studio_bp.route('/agente')
def agente():
    ctx = _common('agente')
    org = _org()
    agents = igs.list_agents(org)
    sel_id = request.args.get('id', type=int)
    selected = next((a for a in agents if a['id'] == sel_id), agents[0] if agents else None)
    return render_template(
        'ig_studio/agente.html', **ctx,
        agents=agents, selected=selected,
        runs=igs.recent_runs(org), summary=igs.runs_summary(org),
    )


@ig_studio_bp.route('/estrategia')
def estrategia():
    ctx = _common('estrategia')
    org = _org()
    report = igs.latest_report(org)
    days = {}
    for it in (report or {}).get('items', []):
        days.setdefault(it['day'], []).append(it)
    return render_template(
        'ig_studio/estrategia.html', **ctx,
        report=report, days_order=igs.DAYS_ES, plan_by_day=days,
        context_text=igs.strategy_context(org),
    )


# ── Conexión con Instagram Login ────────────────────────────────────────────

@ig_studio_bp.route('/conectar')
def conectar():
    if not igs.is_configured():
        flash('Falta IG_APP_ID / IG_APP_SECRET en el .env del servidor.', 'danger')
        return redirect(url_for('ig_studio.analitica'))
    state = secrets.token_urlsafe(24)
    session['igs_oauth_state'] = state
    return redirect(igs.authorize_url(state))


@ig_studio_bp.route('/oauth/callback')
def oauth_callback():
    if session.get('user_role') not in ('admin', 'superadmin'):
        return redirect(url_for('auth.login', next=request.full_path))
    expected = session.pop('igs_oauth_state', None)
    if not expected or not hmac.compare_digest(request.args.get('state', ''), expected):
        flash('La conexión con Instagram expiró. Inténtalo de nuevo.', 'danger')
        return redirect(url_for('ig_studio.analitica'))
    if request.args.get('error'):
        flash(f"Instagram canceló la conexión: {request.args.get('error_description') or request.args['error']}",
              'warning')
        return redirect(url_for('ig_studio.analitica'))
    try:
        token, expires, scopes = igs.exchange_code(request.args.get('code', ''))
        ig_id, username = igs.save_account(_org(), token, expires, scopes)
    except Exception as exc:
        log.exception('Instagram Studio: OAuth falló')
        flash(f'No se pudo conectar Instagram: {exc}', 'danger')
        return redirect(url_for('ig_studio.analitica'))
    _start_sync(_org())
    flash(f'@{username} conectada. Sincronizando publicaciones e insights…', 'success')
    return redirect(url_for('ig_studio.analitica', acct=ig_id))


# ── API (JSON) ──────────────────────────────────────────────────────────────

def _start_sync(org):
    if org in _sync_running:
        return False

    def run():
        try:
            igs.sync_all(org)
        finally:
            _sync_running.discard(org)

    _sync_running.add(org)
    threading.Thread(target=run, daemon=True).start()
    return True


@ig_studio_bp.route('/api/sync', methods=['POST'])
def api_sync():
    started = _start_sync(_org())
    return jsonify(ok=True, started=started), 202


@ig_studio_bp.route('/api/sync/status')
def api_sync_status():
    accts = igs.list_accounts(_org())
    return jsonify(running=_org() in _sync_running,
                   accounts=[{'username': a['username'], 'last_sync_at': a['last_sync_at'],
                              'last_error': a['last_error']} for a in accts])


@ig_studio_bp.route('/api/accounts/<ig_user_id>/disconnect', methods=['POST'])
def api_disconnect(ig_user_id):
    igs.disconnect_account(_org(), ig_user_id)
    return jsonify(ok=True)


def _body():
    return request.get_json(silent=True) or {}


@ig_studio_bp.route('/api/comments/<comment_id>/reply', methods=['POST'])
def api_reply(comment_id):
    igs.manual_reply(_org(), comment_id, _body().get('text', ''), mark='manual')
    return jsonify(ok=True)


@ig_studio_bp.route('/api/comments/<comment_id>/approve', methods=['POST'])
def api_approve(comment_id):
    igs.manual_reply(_org(), comment_id, _body().get('text', ''), mark='replied')
    return jsonify(ok=True)


@ig_studio_bp.route('/api/comments/<comment_id>/ignore', methods=['POST'])
def api_ignore(comment_id):
    igs.ignore_comment(_org(), comment_id)
    return jsonify(ok=True)


@ig_studio_bp.route('/api/comments/<comment_id>/retry', methods=['POST'])
def api_retry(comment_id):
    return jsonify(ok=True, status=igs.retry_comment(_org(), comment_id))


@ig_studio_bp.route('/api/agents', methods=['POST'])
@ig_studio_bp.route('/api/agents/<int:agent_id>', methods=['POST'])
def api_agent_save(agent_id=None):
    data = _body()
    new_id = igs.save_agent(_org(), agent_id, data, kind=data.get('kind', 'comments'))
    return jsonify(ok=True, id=new_id)


@ig_studio_bp.route('/api/agents/<int:agent_id>/test', methods=['POST'])
def api_agent_test(agent_id):
    agent = igs.get_agent(_org(), agent_id)
    if not agent:
        abort(404)
    text = (_body().get('text') or '').strip()
    if not text:
        raise ValueError('Escribe un comentario de prueba.')
    fake = {'from_username': 'prueba', 'text': text}
    decision, _usage, model = igs.decide(agent, None, fake, _body().get('caption', ''))
    return jsonify(ok=True, decision=decision, model=model)


@ig_studio_bp.route('/api/media/<media_id>/agent', methods=['POST'])
def api_media_agent(media_id):
    data = _body()
    value = str(data.get('agent', ''))
    off = value == 'off'
    agent_id = int(value) if value.isdigit() else None
    if agent_id and not igs.get_agent(_org(), agent_id):
        raise ValueError('Agente no encontrado.')
    if not igs.set_media_agent(_org(), media_id, agent_id, off):
        abort(404)
    return jsonify(ok=True)


@ig_studio_bp.route('/api/strategy/generate', methods=['POST'])
def api_strategy_generate():
    rid = igs.generate_weekly(_org(), force=True)
    return jsonify(ok=True, id=rid)


@ig_studio_bp.route('/api/strategy/email', methods=['POST'])
def api_strategy_email():
    to = igs.email_report(_org())
    return jsonify(ok=True, to=to)


@ig_studio_bp.route('/api/strategy/context', methods=['POST'])
def api_strategy_context():
    text = (_body().get('text') or '').strip()[:3000]
    igs.set_setting(f'ig_studio_strategy_context_{_org()}', text or igs.DEFAULT_STRATEGY_CONTEXT)
    return jsonify(ok=True)


@ig_studio_bp.route('/api/plan/<int:item_id>', methods=['POST'])
def api_plan_item(item_id):
    if not igs.set_plan_item(_org(), item_id, bool(_body().get('approved'))):
        abort(404)
    return jsonify(ok=True)
