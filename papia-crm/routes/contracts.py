"""
Papia Sign — módulo Contratos.

  contracts_bp         /contratos/...   (requiere sesión del CRM)
  contracts_public_bp  /firmar/<token>  (firmante, sin cuenta)
                       /verificar       (verificación pública de integridad)
"""
import base64
import io
import json
import time

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file,
                   session, url_for)

from database import get_db
from models import contract as C
from models.client import get_all_clients, get_client
from services import contract_flow as FLOW
from services import contract_notify as N

contracts_bp = Blueprint('contracts', __name__, url_prefix='/contratos')
contracts_public_bp = Blueprint('contracts_public', __name__)

SIGNER_COLORS = ['#FF6B4A', '#00A3B0', '#8B5CF6', '#F59E0B', '#10B981', '#EC4899']
VERIFY_TTL = 4 * 3600            # una verificación OTP dura 4 h en esa sesión de navegador
MAX_SIG_BYTES = 400 * 1024


# ── helpers ──────────────────────────────────────────────────────────────────

def _org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


def _client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    return (fwd.split(',')[0].strip() if fwd else request.remote_addr) or ''


def _ua():
    return request.headers.get('User-Agent', '')[:300]


def _me():
    """Nombre y email del usuario del CRM (para 'Agregarme como firmante')."""
    name, email = session.get('username') or 'Admin', ''
    try:
        db = get_db()
        row = db.execute("SELECT display_name FROM users WHERE id=?", (session.get('user_id'),)).fetchone()
        if row and row['display_name'] and row['display_name'] not in ('Admin',):
            name = row['display_name']
        srow = db.execute("SELECT value FROM settings WHERE key='signature_name' AND org_id=?", (_org_id(),)).fetchone()
        if srow and srow['value'] and name in ('Admin', session.get('username')):
            name = srow['value']
        erow = db.execute("SELECT value FROM settings WHERE key='signature_email' AND org_id=?", (_org_id(),)).fetchone()
        email = erow['value'] if erow and erow['value'] else ''
        db.close()
    except Exception:
        pass
    return name, email


def _load(env_id):
    env = C.get_envelope(env_id, _org_id())
    if not env:
        abort(404)
    return env


def _recipients_from_form():
    try:
        data = json.loads(request.form.get('recipients_json') or '[]')
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clients_for_select():
    try:
        return get_all_clients(_org_id())
    except Exception:
        return []


# ── inbox ────────────────────────────────────────────────────────────────────

@contracts_bp.route('/')
def index():
    tab = request.args.get('tab', 'all')
    if tab not in dict(C.TABS):
        tab = 'all'
    q = (request.args.get('q') or '').strip()
    return render_template('contracts/index.html', envelopes=C.list_envelopes(_org_id(), tab, q),
                           stats=C.envelope_stats(_org_id()), tabs=C.TABS, tab=tab, q=q,
                           colors=SIGNER_COLORS)


# ── create / edit draft ──────────────────────────────────────────────────────

@contracts_bp.route('/nuevo', methods=['GET', 'POST'])
def new():
    if request.method == 'POST':
        f = request.files.get('document')
        title = (request.form.get('title') or '').strip()
        client_id = _int(request.form.get('client_id'), 0) or None
        recipients = _recipients_from_form()
        if client_id and not get_client(client_id, _org_id()):
            client_id = None
        try:
            if not f or not f.filename:
                raise C.ContractError('Sube el documento en PDF.')
            if not f.filename.lower().endswith('.pdf'):
                raise C.ContractError('Por ahora solo se aceptan archivos PDF (exporta tu Word/Google Doc como PDF).')
            data = f.read(C.MAX_PDF_BYTES + 1)
            if not title:
                title = f.filename.rsplit('.', 1)[0][:200]
            env_id = C.create_envelope(
                _org_id(), title, data, f.filename, client_id=client_id,
                message=request.form.get('message', ''), created_by=session.get('user_id'),
                created_by_name=_me()[0], require_otp=bool(request.form.get('require_otp')),
                expires_days=_int(request.form.get('expires_days'), 14),
                reminder_days=_int(request.form.get('reminder_days'), 3))
            try:
                C.set_recipients(env_id, _org_id(), recipients)
            except C.ContractError as exc:
                flash(f'Documento guardado como borrador. {exc}', 'danger')
                return redirect(url_for('contracts.edit', env_id=env_id))
            return redirect(url_for('contracts.prepare', env_id=env_id))
        except C.ContractError as exc:
            flash(str(exc), 'danger')
    pre_client = get_client(_int(request.args.get('client_id'), 0), _org_id()) if request.args.get('client_id') else None
    return render_template('contracts/form.html', env=None, clients=_clients_for_select(), me=_me(),
                           pre_client=pre_client, recipients_json='[]', colors=SIGNER_COLORS)


@contracts_bp.route('/<int:env_id>/editar', methods=['GET', 'POST'])
def edit(env_id):
    env = _load(env_id)
    if env['status'] != 'draft':
        return redirect(url_for('contracts.detail', env_id=env_id))
    if request.method == 'POST':
        client_id = _int(request.form.get('client_id'), 0) or None
        if client_id and not get_client(client_id, _org_id()):
            client_id = None
        C.update_envelope_settings(
            env_id, _org_id(), title=(request.form.get('title') or env['title']).strip()[:200],
            message=(request.form.get('message') or '').strip()[:2000], client_id=client_id,
            require_otp=1 if request.form.get('require_otp') else 0,
            expires_days=max(1, min(_int(request.form.get('expires_days'), 14), 120)),
            reminder_days=max(0, min(_int(request.form.get('reminder_days'), 3), 30)))
        try:
            C.set_recipients(env_id, _org_id(), _recipients_from_form())
            return redirect(url_for('contracts.prepare', env_id=env_id))
        except C.ContractError as exc:
            flash(str(exc), 'danger')
            env = _load(env_id)
    rjson = json.dumps([{k: r[k] for k in ('id', 'name', 'email', 'phone', 'role', 'routing_order',
                                             'notify_email', 'notify_whatsapp')} for r in env['recipients']])
    return render_template('contracts/form.html', env=env, clients=_clients_for_select(), me=_me(),
                           pre_client=None, recipients_json=rjson, colors=SIGNER_COLORS)


@contracts_bp.route('/<int:env_id>/preparar')
def prepare(env_id):
    env = _load(env_id)
    if env['status'] != 'draft':
        return redirect(url_for('contracts.detail', env_id=env_id))
    signers = [r for r in env['recipients'] if r['role'] == 'signer']
    if not signers:
        flash('Agrega al menos un firmante.', 'danger')
        return redirect(url_for('contracts.edit', env_id=env_id))
    payload = {
        'envelopeId': env['id'],
        'pdfUrl': url_for('contracts.document', env_id=env_id),
        'saveUrl': url_for('contracts.save_fields', env_id=env_id),
        'sendUrl': url_for('contracts.send', env_id=env_id),
        'pageSizes': env['page_sizes'],
        'signers': [{'id': r['id'], 'name': r['name'], 'order': r['routing_order'],
                     'color': SIGNER_COLORS[r['color_idx'] % len(SIGNER_COLORS)]} for r in signers],
        'fields': [{k: f[k] for k in ('id', 'recipient_id', 'type', 'page', 'x', 'y', 'w', 'h', 'required', 'label')}
                   for f in env['fields']],
        'fieldTypes': C.FIELD_TYPES,
    }
    return render_template('contracts/prepare.html', env=env, signers=signers, payload=payload,
                           colors=SIGNER_COLORS)


@contracts_bp.route('/<int:env_id>/campos', methods=['POST'])
def save_fields(env_id):
    data = request.get_json(silent=True) or {}
    try:
        n = C.save_fields(env_id, _org_id(), data.get('fields') or [])
    except C.ContractError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True, 'count': n})


@contracts_bp.route('/<int:env_id>/enviar', methods=['POST'])
def send(env_id):
    env = _load(env_id)
    wants_json = request.is_json
    try:
        pairs = C.send_envelope(env_id, _org_id(), actor=_me()[0])
    except C.ContractError as exc:
        if wants_json:
            return jsonify({'ok': False, 'error': str(exc)}), 400
        flash(str(exc), 'danger')
        return redirect(url_for('contracts.prepare', env_id=env_id))
    env = _load(env_id)
    FLOW.dispatch_invites(env, pairs, env.get('created_by_name') or _me()[0])
    FLOW.client_note(env.get('client_id'), f'Contrato enviado para firma: {env["title"]} (sobre {env["uid"]})')
    failed = [e for e in _load(env_id)['events'] if e['event'] == 'notify_failed']
    msg = 'Sobre enviado.' + (' Revisa el historial: algún aviso no se pudo entregar.' if failed else '')
    flash(msg, 'success' if not failed else 'danger')
    target = url_for('contracts.detail', env_id=env_id)
    return jsonify({'ok': True, 'redirect': target}) if wants_json else redirect(target)


# ── detail & actions ─────────────────────────────────────────────────────────

@contracts_bp.route('/<int:env_id>')
def detail(env_id):
    env = _load(env_id)
    final_ready = env['status'] == 'completed'
    pending_finalize = (env['status'] == 'sent' and env['recipients']
                        and all(r['status'] == 'completed' for r in env['recipients'] if r['role'] == 'signer'))
    return render_template('contracts/detail.html', env=env, colors=SIGNER_COLORS, final_ready=final_ready,
                           pending_finalize=pending_finalize, fmt_local=_fmt_local)


def _fmt_local(ts, fmt='%b %d, %Y · %I:%M %p'):
    from services.contract_pdf import fmt_local
    return fmt_local(ts, fmt)


@contracts_bp.route('/<int:env_id>/documento')
def document(env_id):
    env = _load(env_id)
    return send_file(C.original_path(env), mimetype='application/pdf', download_name=FLOW.safe_filename(env['title']),
                     as_attachment=bool(request.args.get('dl')), max_age=0)


@contracts_bp.route('/<int:env_id>/final')
def final(env_id):
    env = _load(env_id)
    if env['status'] != 'completed':
        abort(404)
    C.log_event(env_id, _org_id(), 'downloaded', actor=_me()[0], ip=_client_ip(), user_agent=_ua())
    return send_file(C.final_path(env), mimetype='application/pdf',
                     download_name=FLOW.safe_filename(env['title'], '_firmado'),
                     as_attachment=bool(request.args.get('dl')), max_age=0)


@contracts_bp.route('/<int:env_id>/reenviar/<int:rid>', methods=['POST'])
def resend(env_id, rid):
    env = _load(env_id)
    rc = next((r for r in env['recipients'] if r['id'] == rid), None)
    if env['status'] != 'sent' or not C.is_turn(rc):
        flash('Ese destinatario no tiene una firma pendiente.', 'danger')
        return redirect(url_for('contracts.detail', env_id=env_id))
    token = C.issue_token(rid)
    sent = N.invite(env, rc, token, env.get('created_by_name') or _me()[0], reminder=True)
    C.touch_reminder(rid)
    flash(f'Enlace reenviado a {rc["name"]}' + (f' por {" + ".join(sent)}.' if sent else ' — no se pudo entregar, revisa el historial.'),
          'success' if sent else 'danger')
    return redirect(url_for('contracts.detail', env_id=env_id))


@contracts_bp.route('/<int:env_id>/en-persona/<int:rid>', methods=['POST'])
def in_person(env_id, rid):
    """Firmar en este dispositivo (tú mismo, o el cliente frente a ti). Genera un enlace nuevo."""
    env = _load(env_id)
    rc = next((r for r in env['recipients'] if r['id'] == rid), None)
    if env['status'] != 'sent' or not C.is_turn(rc):
        flash('Ese destinatario no tiene una firma pendiente.', 'danger')
        return redirect(url_for('contracts.detail', env_id=env_id))
    token = C.issue_token(rid)
    _mark_verified(rid)
    C.mark_viewed(rid, auth_method='in_person')
    C.log_event(env_id, _org_id(), 'in_person', rid, actor=_me()[0],
                detail=f'Usuario del CRM: {session.get("username")}', ip=_client_ip(), user_agent=_ua())
    return redirect(url_for('contracts_public.sign', token=token))


@contracts_bp.route('/<int:env_id>/anular', methods=['POST'])
def void(env_id):
    reason = (request.form.get('reason') or '').strip()
    if C.void(env_id, _org_id(), reason, actor=_me()[0]):
        flash('Sobre anulado. Los enlaces de firma ya no funcionan.', 'success')
    return redirect(url_for('contracts.detail', env_id=env_id))


@contracts_bp.route('/<int:env_id>/eliminar', methods=['POST'])
def delete(env_id):
    if C.delete_draft(env_id, _org_id()):
        flash('Borrador eliminado.', 'success')
        return redirect(url_for('contracts.index'))
    flash('Solo se pueden eliminar borradores. Para un sobre enviado usa "Anular".', 'danger')
    return redirect(url_for('contracts.detail', env_id=env_id))


@contracts_bp.route('/<int:env_id>/finalizar', methods=['POST'])
def retry_finalize(env_id):
    env = _load(env_id)
    if env['status'] == 'sent' and all(r['status'] == 'completed' for r in env['recipients'] if r['role'] == 'signer'):
        try:
            FLOW.finalize(env_id)
            flash('PDF final generado y enviado.', 'success')
        except Exception as exc:
            flash(f'No se pudo generar el PDF final: {exc}', 'danger')
    return redirect(url_for('contracts.detail', env_id=env_id))


# ══ Firmante (público) ════════════════════════════════════════════════════════

def _verified_map():
    data = session.get('psign') or {}
    now = time.time()
    return {k: v for k, v in data.items() if now - v < VERIFY_TTL}


def _mark_verified(rid):
    data = _verified_map()
    data[str(rid)] = time.time()
    session['psign'] = data
    session.modified = True


def _is_verified(rid):
    return str(rid) in _verified_map()


def _resolve(token):
    rc = C.find_recipient_by_token(token)
    if not rc:
        return None, None
    env = C.get_envelope(rc['envelope_id'], rc['org_id'])
    return env, rc


MESSAGES = {
    'invalid': ('bad', 'bi-link-45deg', 'Este enlace no es válido',
                'Puede que haya sido reemplazado por uno más reciente. Revisa el último mensaje que recibiste '
                'o contacta a quien te envió el documento.'),
    'voided': ('bad', 'bi-x-octagon', 'Este documento fue anulado',
               'El remitente canceló este sobre. Si te envían una versión nueva, recibirás otro enlace.'),
    'expired': ('bad', 'bi-hourglass-bottom', 'Este enlace venció',
                'Pide a quien te envió el documento que te lo reenvíe.'),
    'declined': ('bad', 'bi-x-circle', 'El documento fue rechazado',
                 'Uno de los firmantes rechazó firmar, así que el sobre quedó cancelado. Ya avisamos al remitente.'),
    'waiting': ('', 'bi-hourglass-split', 'Todavía no es tu turno',
                'Te avisaremos por email o WhatsApp cuando los firmantes anteriores terminen.'),
    'done': ('ok', 'bi-check2-circle', '¡Listo, firmaste!', 'Tu firma quedó registrada.'),
}


def _public(template, env=None, rc=None, **ctx):
    if 'kind' in ctx:
        ctx['m'] = MESSAGES.get(ctx['kind'], MESSAGES['invalid'])
    org = N.org_name(env['org_id']) if env else 'Papia Technology Solutions'
    resp = render_template(f'contracts/public/{template}', env=env, rc=rc, org_name=org, **ctx)
    return resp, 200, {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow',
                       'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'}


def _gate(env, rc):
    """Devuelve una respuesta si el firmante no puede firmar ahora; None si puede."""
    if not env or not rc:
        return _public('message.html', kind='invalid')
    if env['status'] == 'voided':
        return _public('message.html', env, rc, kind='voided')
    if env['status'] == 'expired' or (env['status'] == 'sent' and (env.get('expires_at') or '9') < C.now_utc()):
        if env['status'] == 'sent':
            C.expire_overdue()
        return _public('message.html', env, rc, kind='expired')
    if env['status'] == 'declined':
        return _public('message.html', env, rc, kind='declined')
    if rc['status'] == 'completed' or env['status'] == 'completed':
        return _public('message.html', env, rc, kind='done', token=request.view_args.get('token'),
                       can_download=_can_download(env, rc))
    if rc['role'] != 'signer' or rc['status'] == 'created':
        return _public('message.html', env, rc, kind='waiting')
    return None


def _can_download(env, rc):
    return env['status'] == 'completed' and (not env['require_otp'] or _is_verified(rc['id']))


@contracts_public_bp.route('/firmar/<token>')
def sign(token):
    env, rc = _resolve(token)
    blocked = _gate(env, rc)
    if blocked:
        return blocked
    if env['require_otp'] and not _is_verified(rc['id']):
        return _public('verify.html', env, rc, token=token,
                       channel_hint=_mask(rc['email']) if rc['email'] else _mask_phone(rc['phone']))
    if rc['status'] == 'sent':
        C.mark_viewed(rc['id'], auth_method=None if env['require_otp'] else 'link')
        C.log_event(env['id'], env['org_id'], 'viewed', rc['id'], actor=rc['name'], ip=_client_ip(), user_agent=_ua())
    elif not rc.get('auth_method') and not env['require_otp']:
        C.mark_viewed(rc['id'], auth_method='link')
    rcpts = {r['id']: r for r in env['recipients']}
    fields = []
    for f in env['fields']:
        owner = rcpts.get(f['recipient_id'])
        if f['recipient_id'] == rc['id']:
            fields.append({**_field_public(f), 'mine': True})
        elif owner and owner['status'] == 'completed' and f.get('value') not in (None, ''):
            item = {**_field_public(f), 'mine': False, 'value': f['value']}
            if f['type'] in ('signature', 'initials'):
                item['img'] = owner['signature_png'] if f['type'] == 'signature' else (owner['initials_png'] or owner['signature_png'])
            elif f['type'] == 'date_signed':
                from services.contract_pdf import fmt_local
                item['value'] = fmt_local(f['value'], '%m/%d/%Y')
            fields.append(item)
    from services.contract_pdf import fmt_local
    payload = {
        'pdfUrl': url_for('contracts_public.sign_document', token=token),
        'completeUrl': url_for('contracts_public.complete', token=token),
        'consentUrl': url_for('contracts_public.consent', token=token),
        'declineUrl': url_for('contracts_public.decline', token=token),
        'pageSizes': env['page_sizes'],
        'fields': fields,
        'signer': {'name': rc['name'], 'email': rc['email'], 'consented': bool(rc.get('consent_at'))},
        'today': fmt_local(C.now_utc(), '%m/%d/%Y'),
    }
    return _public('sign.html', env, rc, token=token, payload=payload,
                   sender=env.get('created_by_name') or N.org_name(env['org_id']))


def _field_public(f):
    return {k: f[k] for k in ('id', 'type', 'page', 'x', 'y', 'w', 'h', 'required', 'label')}


def _mask(email):
    if not email or '@' not in email:
        return ''
    user, dom = email.split('@', 1)
    return (user[:2] + '•' * max(1, len(user) - 2)) + '@' + dom


def _mask_phone(phone):
    digits = ''.join(ch for ch in phone or '' if ch.isdigit())
    return f'WhatsApp terminado en {digits[-4:]}' if digits else ''


@contracts_public_bp.route('/firmar/<token>/codigo', methods=['POST'])
def send_code(token):
    env, rc = _resolve(token)
    blocked = _gate(env, rc)
    if blocked:
        return jsonify({'ok': False, 'error': 'Este enlace ya no está disponible.'}), 400
    recent = [e for e in env['events'] if e['event'] == 'otp_sent' and e['recipient_id'] == rc['id']]
    if recent and recent[-1]['created_at'] > _ago(45):
        return jsonify({'ok': False, 'error': 'Espera unos segundos antes de pedir otro código.'}), 429
    if len([e for e in recent if e['created_at'] > _ago(3600)]) >= 6:
        return jsonify({'ok': False, 'error': 'Demasiados códigos solicitados. Intenta en una hora.'}), 429
    code = C.issue_otp(rc['id'])
    channel = N.send_otp(env, rc, code)
    if not channel:
        return jsonify({'ok': False, 'error': 'No pudimos enviarte el código. Contacta a quien te envió el documento.'}), 502
    session['psign_channel'] = channel
    C.log_event(env['id'], env['org_id'], 'otp_sent', rc['id'], actor='Sistema', detail=f'Por {channel}',
                ip=_client_ip(), user_agent=_ua())
    return jsonify({'ok': True, 'channel': channel})


def _ago(seconds):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime('%Y-%m-%d %H:%M:%S')


@contracts_public_bp.route('/firmar/<token>/verificar', methods=['POST'])
def verify_code(token):
    env, rc = _resolve(token)
    blocked = _gate(env, rc)
    if blocked:
        return jsonify({'ok': False, 'error': 'Este enlace ya no está disponible.'}), 400
    result = C.check_otp(rc['id'], (request.get_json(silent=True) or {}).get('code', ''))
    if result == 'ok':
        _mark_verified(rc['id'])
        channel = session.get('psign_channel') or ('email' if rc['email'] else 'whatsapp')
        C.mark_viewed(rc['id'], auth_method=f'otp_{channel}')
        C.log_event(env['id'], env['org_id'], 'otp_verified', rc['id'], actor=rc['name'], detail=f'Código por {channel}',
                    ip=_client_ip(), user_agent=_ua())
        return jsonify({'ok': True})
    C.log_event(env['id'], env['org_id'], 'otp_failed', rc['id'], actor=rc['name'], ip=_client_ip(), user_agent=_ua())
    msg = {'bad': 'Código incorrecto. Revisa e intenta de nuevo.',
           'expired': 'El código venció. Pide uno nuevo.',
           'locked': 'Demasiados intentos. Pide un código nuevo.'}[result]
    return jsonify({'ok': False, 'error': msg}), 400


def _signer_ok(token):
    env, rc = _resolve(token)
    if _gate(env, rc) is not None:
        return None, None
    if env['require_otp'] and not _is_verified(rc['id']):
        return None, None
    return env, rc


@contracts_public_bp.route('/firmar/<token>/documento')
def sign_document(token):
    env, rc = _resolve(token)
    if not env or not rc or env['status'] in ('voided',):
        abort(404)
    if env['require_otp'] and not _is_verified(rc['id']):
        abort(403)
    resp = send_file(C.original_path(env), mimetype='application/pdf', max_age=0)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@contracts_public_bp.route('/firmar/<token>/final')
def sign_final(token):
    env, rc = _resolve(token)
    if not env or not _can_download(env, rc):
        abort(404)
    return send_file(C.final_path(env), mimetype='application/pdf', as_attachment=True,
                     download_name=FLOW.safe_filename(env['title'], '_firmado'), max_age=0)


@contracts_public_bp.route('/firmar/<token>/consentimiento', methods=['POST'])
def consent(token):
    env, rc = _signer_ok(token)
    if not env:
        return jsonify({'ok': False, 'error': 'Sesión vencida. Recarga la página.'}), 403
    if not rc.get('consent_at'):
        C.set_consent(rc['id'])
        C.log_event(env['id'], env['org_id'], 'consent', rc['id'], actor=rc['name'],
                    detail='Acepta usar firmas y registros electrónicos (ESIGN/UETA)', ip=_client_ip(), user_agent=_ua())
    return jsonify({'ok': True})


def _valid_png(data_url):
    if not data_url or not isinstance(data_url, str) or not data_url.startswith('data:image/png;base64,'):
        return None
    if len(data_url) > MAX_SIG_BYTES * 4 // 3 + 64:
        return None
    try:
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except Exception:
        return None
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            if im.width > 2000 or im.height > 1000:
                return None
    except Exception:
        return None
    return data_url


@contracts_public_bp.route('/firmar/<token>/completar', methods=['POST'])
def complete(token):
    env, rc = _signer_ok(token)
    if not env:
        return jsonify({'ok': False, 'error': 'Tu sesión venció o el sobre cambió. Recarga la página.'}), 403
    data = request.get_json(silent=True) or {}
    if not data.get('consent') and not rc.get('consent_at'):
        return jsonify({'ok': False, 'error': 'Debes aceptar el uso de firmas electrónicas.'}), 400
    if not rc.get('consent_at'):
        C.set_consent(rc['id'])
        C.log_event(env['id'], env['org_id'], 'consent', rc['id'], actor=rc['name'],
                    detail='Acepta usar firmas y registros electrónicos (ESIGN/UETA)', ip=_client_ip(), user_agent=_ua())
    signature = _valid_png(data.get('signature'))
    initials = _valid_png(data.get('initials')) if data.get('initials') else None
    if not signature:
        return jsonify({'ok': False, 'error': 'Adopta tu firma antes de finalizar.'}), 400
    kind = 'draw' if data.get('kind') == 'draw' else 'type'
    try:
        outcome, pairs = C.complete_signature(env, rc, data.get('values') or {}, signature, initials, kind,
                                              _client_ip(), _ua())
    except C.ContractError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    FLOW.after_signature(C.get_envelope(env['id'], env['org_id']), rc, outcome, pairs)
    return jsonify({'ok': True, 'redirect': url_for('contracts_public.sign', token=token)})


@contracts_public_bp.route('/firmar/<token>/rechazar', methods=['POST'])
def decline(token):
    env, rc = _signer_ok(token)
    if not env:
        return jsonify({'ok': False, 'error': 'Tu sesión venció. Recarga la página.'}), 403
    reason = ((request.get_json(silent=True) or {}).get('reason') or '').strip()
    if len(reason) < 3:
        return jsonify({'ok': False, 'error': 'Cuéntanos brevemente el motivo.'}), 400
    C.decline(env, rc, reason, _client_ip(), _ua())
    FLOW.client_note(env.get('client_id'), f'{rc["name"]} rechazó "{env["title"]}": {reason}')
    N.notify_sender(env, f'Rechazado: {env["title"]}', [f'{rc["name"]} rechazó firmar "{env["title"]}".',
                                                        f'Motivo: {reason}'])
    return jsonify({'ok': True, 'redirect': url_for('contracts_public.sign', token=token)})


# ── verificación pública ─────────────────────────────────────────────────────

@contracts_public_bp.route('/verificar', methods=['GET', 'POST'])
def verify_public():
    result, env = None, None
    if request.method == 'POST':
        f = request.files.get('pdf')
        uid = (request.form.get('uid') or '').strip()
        if f and f.filename:
            data = f.read(C.MAX_PDF_BYTES + 1)
            digest = C.sha256_hex(data)
            env = C.find_envelope_by_final_hash(digest)
            if env and env.get('final_sha256') == digest:
                result = 'match'
            elif env:
                result = 'original'
            else:
                result = 'nomatch'
        elif uid:
            env = C.find_envelope_by_uid(uid)
            result = 'found' if env and env['status'] == 'completed' else 'nomatch'
    return _public('verify_public.html', env if result in ('match', 'found', 'original') else None,
                   result=result)
