"""Metadata only. Video bytes travel between the browser and Google Drive."""
import requests
from flask import g
from database import get_db
from routes.emails import _load_creds

DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
API = 'https://www.googleapis.com/drive/v3'


class DriveError(Exception):
    def __init__(self, message, status=502, reconnect=False):
        super().__init__(message)
        self.status = status
        self.reconnect = reconnect


def token_info(token):
    try:
        response = requests.get('https://oauth2.googleapis.com/tokeninfo',
                                params={'access_token': token}, timeout=20)
    except requests.RequestException:
        raise DriveError('Google no responde. Inténtalo de nuevo.')
    if response.status_code in (400, 401):
        raise DriveError('Reconecta Google Drive', 401, True)
    if response.status_code != 200:
        raise DriveError('No se pudo validar la conexión con Google. Inténtalo de nuevo.')
    return response.json()


def verified_credentials(org_id):
    # Gmail's stored tokens have no expiry. google-auth therefore considers an
    # expired access token "valid" until Google actually rejects it. Reuse its
    # loader, then refresh once on rejection without changing Gmail's OAuth.
    creds = _load_creds(org_id)
    if not creds:
        raise DriveError('Reconecta Google Drive', 401, True)
    try:
        return creds, token_info(creds.token)
    except DriveError as exc:
        if not exc.reconnect:
            raise
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError, TransportError
    try:
        creds.refresh(Request())
    except RefreshError:
        raise DriveError('La autorización de Google caducó o fue revocada. Reconecta Google Drive.', 401, True)
    except (TransportError, requests.RequestException):
        raise DriveError('Google no responde al renovar la conexión. Inténtalo de nuevo.')
    info = token_info(creds.token)
    db = get_db()
    try:
        db.execute('UPDATE gmail_tokens SET access_token=?,updated_at=CURRENT_TIMESTAMP WHERE org_id=?',
                   (creds.token, org_id))
        db.commit()
    finally:
        db.close()
    return creds, info


def credentials():
    creds, info = verified_credentials(g.org_id)
    if DRIVE_SCOPE not in info.get('scope', '').split():
        raise DriveError('Reconecta Google Drive', 401, True)
    return creds, info


def call(method, path, creds, **kwargs):
    try:
        response = requests.request(method, API + path,
            headers={'Authorization': 'Bearer ' + creds.token}, timeout=30, **kwargs)
    except requests.RequestException:
        raise DriveError('Google Drive no responde. Inténtalo de nuevo.')
    if response.status_code in (401, 403):
        reason = response.text
        if response.status_code == 401 or 'insufficientPermissions' in reason or 'ACCESS_TOKEN_SCOPE_INSUFFICIENT' in reason:
            raise DriveError('Reconecta Google Drive', 401, True)
        raise DriveError('Drive rechazó la operación. Revisa el espacio, los permisos y que Drive API esté habilitada.', 403)
    if response.status_code == 404:
        raise DriveError('El archivo o la carpeta ya no existe en Drive.', 404)
    if not response.ok:
        raise DriveError('No se pudo completar la operación en Google Drive.')
    return response.json() if response.content else {}


def ensure_folder(creds, cliente=None):
    """Serialize folder discovery/creation across WSGI processes, recover by appProperties."""
    root = _folder(creds, 'raiz', None, 'PapiaTech Videos', None)
    if cliente is None:
        return _folder(creds, 'cliente', None, 'Sin cliente', root)
    name = (cliente['first_name'] + ' ' + cliente['last_name']).strip()
    return _folder(creds, 'cliente', cliente['id'], name, root)


def _folder(creds, kind, client_id, name, parent):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM drive_carpetas WHERE org_id=? AND tipo=? AND cliente_id IS ?',
                         (g.org_id, kind, client_id)).fetchone()
        if row:
            try:
                existing = call('GET', '/files/' + row['folder_id'], creds,
                    params={'fields': 'id,trashed,mimeType,parents'})
                if (not existing.get('trashed') and existing.get('mimeType') == 'application/vnd.google-apps.folder'
                        and (parent is None or parent in existing.get('parents', []))):
                    return row['folder_id']
            except DriveError as exc:
                if exc.status != 404:
                    raise
            db.execute('DELETE FROM drive_carpetas WHERE id=?', (row['id'],))
        key = f'{g.org_id}:{kind}:{client_id or 0}'
        query = "trashed=false and mimeType='application/vnd.google-apps.folder' and appProperties has { key='papiaVideosFolder' and value='" + key + "' }"
        if parent:
            query += " and '" + parent.replace("'", "\\'") + "' in parents"
        found = call('GET', '/files', creds, params={'q': query, 'fields': 'files(id)', 'pageSize': 1})
        items = found.get('files', [])
        if items:
            folder_id = items[0]['id']
        else:
            metadata = {'name': name, 'mimeType': 'application/vnd.google-apps.folder',
                        'appProperties': {'papiaVideosFolder': key}}
            if parent:
                metadata['parents'] = [parent]
            folder_id = call('POST', '/files', creds, json=metadata, params={'fields': 'id'})['id']
        db.execute('INSERT INTO drive_carpetas(org_id,tipo,cliente_id,folder_id,nombre) VALUES(?,?,?,?,?)',
                   (g.org_id, kind, client_id, folder_id, name))
        db.commit()
        return folder_id
    finally:
        db.close()
