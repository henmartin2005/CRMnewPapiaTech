import hmac
import math
import os
import re
import secrets
import time
from urllib.parse import urlsplit

import requests
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from database import get_db
from routes.emails import _client_config, _load_creds
from services import video_drive as drive

videos_bp = Blueprint('videos', __name__, url_prefix='/videos')


@videos_bp.before_request
def protect():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login', next=request.full_path))
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)
    session.setdefault('videos_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not hmac.compare_digest(request.headers.get('X-Videos-CSRF', ''), session['videos_csrf']):
            abort(403)
        if request.content_length and request.content_length > 32768:
            abort(413)
        if not request.is_json:
            abort(415)
        if not isinstance(request.get_json(silent=True), dict):
            raise ValueError('Envía un objeto JSON válido.')


@videos_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@videos_bp.errorhandler(drive.DriveError)
def drive_error(exc):
    if '/api/' in request.path:
        return jsonify(error=str(exc), reconnect=exc.reconnect,
                       connect_url=url_for('videos.connect')), exc.status
    flash(str(exc), 'danger')
    return redirect(url_for('videos.index'))


@videos_bp.errorhandler(ValueError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


def query(sql, params=(), one=False):
    db = get_db()
    try:
        result = db.execute(sql, params)
        return result.fetchone() if one else result.fetchall()
    finally:
        db.close()


def client(value):
    if value in (None, ''):
        return None
    try:
        ident = int(value)
    except (ValueError, TypeError):
        raise ValueError('Cliente inválido.')
    row = query('SELECT * FROM clients WHERE id=? AND org_id=?', (ident, g.org_id), True)
    if not row:
        abort(404)
    return row


def video(ident):
    row = query('SELECT * FROM videos WHERE id=? AND org_id=? AND deleted_at IS NULL',
                (ident, g.org_id), True)
    if not row:
        abort(404)
    return row


def clients():
    return query('SELECT id,first_name,last_name FROM clients WHERE org_id=? ORDER BY first_name,last_name', (g.org_id,))


def library(client_id=None, search='', limit=200):
    sql = 'SELECT * FROM videos WHERE org_id=? AND deleted_at IS NULL'
    args = [g.org_id]
    if client_id is not None:
        sql += ' AND cliente_id IS ?'
        args.append(None if client_id == 'sin' else int(client_id))
    if search:
        sql += ' AND instr(lower(titulo),lower(?))>0'
        args.append(search[:200])
    return query(sql + ' ORDER BY created_at DESC,id DESC LIMIT ?', args + [limit])


@videos_bp.app_context_processor
def client_integration():
    def recent(client_id):
        if session.get('user_role') not in ('admin', 'superadmin'):
            return []
        return library(client_id, limit=4)
    return {'recent_client_videos': recent}


@videos_bp.route('')
@videos_bp.route('/cliente/<int:cliente_id>')
def index(cliente_id=None):
    selected = cliente_id or request.args.get('cliente_id')
    if selected and selected != 'sin':
        client(selected)
    connected = False
    try:
        drive.credentials()
        connected = True
    except drive.DriveError as exc:
        if not exc.reconnect:
            flash(str(exc), 'warning')
    return render_template('videos/library.html', videos=library(selected, request.args.get('q', '')),
                           clients=clients(), selected=selected, connected=connected)


@videos_bp.route('/editor')
@videos_bp.route('/editor/<int:video_id>')
def editor(video_id=None):
    source = dict(video(video_id)) if video_id else None
    selected = source['cliente_id'] if source else request.args.get('cliente_id')
    if selected:
        client(selected)
    return render_template('videos/editor.html', source=source, clients=clients(),
                           library=library(limit=200), selected=selected)


@videos_bp.route('/auth')
def connect():
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(_client_config(g.org_id), scopes=[drive.DRIVE_SCOPE])
    flow.redirect_uri = url_for('videos.callback', _external=True)
    auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    session['videos_oauth'] = {'state': state, 'verifier': flow.code_verifier,
                              'org': g.org_id, 'started': time.time()}
    return redirect(auth_url)


@videos_bp.route('/oauth2callback')
def callback():
    from google_auth_oauthlib.flow import Flow
    saved = session.pop('videos_oauth', {})
    if (saved.get('org') != g.org_id or time.time() - saved.get('started', 0) > 600
            or not saved.get('state') or not hmac.compare_digest(saved['state'], request.args.get('state', ''))):
        abort(400)
    if request.args.get('error'):
        flash('No se concedió acceso a Drive. Puedes conectarlo de nuevo.', 'warning')
        return redirect(url_for('videos.index'))
    flow = Flow.from_client_config(_client_config(g.org_id), scopes=[drive.DRIVE_SCOPE], state=saved['state'])
    flow.redirect_uri = url_for('videos.callback', _external=True)
    flow.code_verifier = saved['verifier']
    try:
        # Incremental grants may include existing Gmail/Calendar scopes.
        # OAuthlib honors this flag; restore it immediately after the exchange.
        previous = os.environ.get('OAUTHLIB_RELAX_TOKEN_SCOPE')
        os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
        try:
            flow.fetch_token(authorization_response=request.url)
        finally:
            if previous is None:
                os.environ.pop('OAUTHLIB_RELAX_TOKEN_SCOPE', None)
            else:
                os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = previous
        creds = flow.credentials
        new_info = drive.token_info(creds.token)
        if drive.DRIVE_SCOPE not in new_info.get('scope', '').split():
            raise ValueError('Falta el permiso de Drive.')
        old_row = query('SELECT * FROM gmail_tokens WHERE org_id=?', (g.org_id,), True)
        old = _load_creds(g.org_id) if old_row else None
        if old_row and not old:
            raise ValueError('Reconecta primero la cuenta de Google existente desde Emails y vuelve a conectar Drive.')
        if old:
            old_info = drive.token_info(old.token)
            old_user = old_info.get('sub') or old_info.get('user_id')
            new_user = new_info.get('sub') or new_info.get('user_id')
            if not old_user or old_user != new_user:
                raise ValueError('Conecta la misma cuenta de Google que usa Emails para conservar sus permisos.')
            if not set(old_info.get('scope', '').split()).issubset(new_info.get('scope', '').split()):
                raise ValueError('Google no conservó los permisos existentes. Vuelve a conectar la misma cuenta.')
        refresh = creds.refresh_token or (old_row['refresh_token'] if old_row else None)
        if not refresh:
            raise ValueError('Google no entregó un permiso renovable. Reconecta Google Drive.')
        db = get_db()
        try:
            if old_row:
                db.execute('UPDATE gmail_tokens SET access_token=?,refresh_token=?,updated_at=CURRENT_TIMESTAMP WHERE org_id=?',
                           (creds.token, refresh, g.org_id))
            else:
                db.execute('INSERT INTO gmail_tokens(access_token,refresh_token,org_id) VALUES(?,?,?)',
                           (creds.token, refresh, g.org_id))
            db.commit()
        finally:
            db.close()
        flash('Google Drive conectado.', 'success')
    except (ValueError, drive.DriveError) as exc:
        flash(str(exc), 'danger')
    except Exception:
        flash('No se pudo conectar Google Drive. Revisa las credenciales y vuelve a intentarlo.', 'danger')
    return redirect(url_for('videos.index'))


@videos_bp.route('/api/token')
def token():
    creds, info = drive.credentials()
    return jsonify(access_token=creds.token, expires_in=int(info.get('expires_in', 0)))


@videos_bp.route('/api/upload-session', methods=['POST'])
def upload_session():
    data = request.get_json()
    name = str(data.get('nombre', '')).strip()[:240]
    mime = str(data.get('mime_type', ''))
    try:
        size = int(data.get('size', 0))
    except (ValueError, TypeError):
        raise ValueError('Tamaño inválido.')
    media_type = re.fullmatch(r'video/[a-zA-Z0-9.+_-]+', mime) or mime in {'image/jpeg','image/png','image/webp','image/heic','image/heif'}
    if not name or not media_type or not 0 < size <= 10 * 1024**3:
        raise ValueError('Selecciona un video o una foto válida de hasta 10 GB.')
    customer = client(data.get('cliente_id'))
    creds, _ = drive.credentials()
    folder = drive.ensure_folder(creds, customer)
    nonce = secrets.token_urlsafe(24)
    origin = urlsplit(request.url_root)
    origin = origin.scheme + '://' + origin.netloc
    try:
        response = requests.post('https://www.googleapis.com/upload/drive/v3/files',
            params={'uploadType': 'resumable', 'fields': 'id,name,mimeType,size'},
            headers={'Authorization': 'Bearer ' + creds.token, 'Origin': origin,
                     'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(size)},
            json={'name': name, 'mimeType': mime, 'parents': [folder],
                  'appProperties': {'papiaVideosUpload': nonce, 'papiaVideosOrg': str(g.org_id)}}, timeout=30)
    except requests.RequestException:
        raise drive.DriveError('No se pudo iniciar la subida. Inténtalo de nuevo.')
    if response.status_code in (401, 403):
        raise drive.DriveError('Drive rechazó la subida. Reconecta Google Drive y revisa tu espacio disponible.', 403, True)
    location = response.headers.get('Location', '')
    parsed = urlsplit(location)
    if not response.ok or parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com':
        raise drive.DriveError('Drive no devolvió una sesión de subida válida.')
    db = get_db()
    try:
        db.execute("DELETE FROM videos_uploads WHERE created_at < datetime('now','-7 days')")
        db.execute('INSERT INTO videos_uploads(nonce,org_id,cliente_id,folder_id,nombre,mime_type,size_bytes) VALUES(?,?,?,?,?,?,?)',
                   (nonce, g.org_id, customer['id'] if customer else None, folder, name, mime, size))
        db.commit()
    finally:
        db.close()
    return jsonify(upload_url=location, upload_id=nonce)


@videos_bp.route('/api/registrar', methods=['POST'])
def register():
    data = request.get_json()
    pending = query('SELECT * FROM videos_uploads WHERE nonce=? AND org_id=?',
                    (data.get('upload_id'), g.org_id), True)
    if not pending:
        raise ValueError('La sesión de subida no existe o caducó.')
    file_id = str(data.get('drive_file_id', ''))
    if not re.fullmatch(r'[\w-]{5,200}', file_id):
        raise ValueError('Archivo de Drive inválido.')
    existing = query('SELECT id FROM videos WHERE drive_file_id=? AND org_id=?', (file_id, g.org_id), True)
    if existing:
        return jsonify(id=existing['id'])
    kind = data.get('tipo', 'original')
    if kind not in ('original', 'editado'):
        raise ValueError('Tipo de video inválido.')
    parent = data.get('video_padre_id')
    if parent is not None:
        video(parent)
    if data.get('proyecto_id') is not None:
        raise ValueError('Esta versión de Pizarra no tiene proyectos independientes.')
    try:
        duration = float(data.get('duracion_seg', 0))
    except (ValueError, TypeError):
        raise ValueError('Duración inválida.')
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('Duración inválida.')
    creds, _ = drive.credentials()
    metadata = drive.call('GET', '/files/' + file_id, creds,
        params={'fields': 'id,name,mimeType,size,parents,appProperties,trashed'})
    props = metadata.get('appProperties', {})
    if (metadata.get('trashed') or pending['folder_id'] not in metadata.get('parents', [])
            or props.get('papiaVideosUpload') != pending['nonce']
            or props.get('papiaVideosOrg') != str(g.org_id)
            or int(metadata.get('size', 0)) != pending['size_bytes']
            or metadata.get('mimeType') != pending['mime_type']):
        raise ValueError('Drive aún no confirmó la subida completa en la carpeta correcta.')
    title = str(data.get('titulo') or metadata['name']).strip()[:240]
    db = get_db()
    try:
        db.execute('INSERT OR IGNORE INTO videos(org_id,cliente_id,titulo,descripcion,drive_file_id,drive_folder_id,mime_type,size_bytes,duracion_seg,tipo,video_padre_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (g.org_id, pending['cliente_id'], title, str(data.get('descripcion', ''))[:5000], file_id,
             pending['folder_id'], pending['mime_type'], pending['size_bytes'], duration, kind, parent))
        ident = db.execute('SELECT id FROM videos WHERE org_id=? AND drive_file_id=?', (g.org_id, file_id)).fetchone()['id']
        db.commit()
    finally:
        db.close()
    return jsonify(id=ident), 201


@videos_bp.route('/api/<int:ident>/eliminar', methods=['POST'])
def delete(ident):
    item = video(ident)
    creds, _ = drive.credentials()
    try:
        drive.call('PATCH', '/files/' + item['drive_file_id'], creds, json={'trashed': True})
    except drive.DriveError as exc:
        if exc.status != 404:
            raise
    db = get_db()
    try:
        db.execute('UPDATE videos SET deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND org_id=?', (ident, g.org_id))
        db.commit()
    finally:
        db.close()
    return jsonify(ok=True)
