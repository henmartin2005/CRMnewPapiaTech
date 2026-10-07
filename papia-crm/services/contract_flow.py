"""Firma electrónica (Contratos) — orquestación: invitar, cerrar el sobre, archivar y avisar."""
import logging
import os
import re
import uuid

from database import get_db
from models import contract as C
from services import contract_notify as N

log = logging.getLogger(__name__)


def safe_filename(title, suffix=''):
    base = re.sub(r'[^\w\- ]+', '', title, flags=re.UNICODE).strip().replace(' ', '_')[:80] or 'contrato'
    return f'{base}{suffix}.pdf'


def client_note(client_id, text):
    if not client_id:
        return
    try:
        db = get_db()
        db.execute("INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)", (int(client_id), text))
        db.commit()
        db.close()
    except Exception:
        log.exception('Contratos: no se pudo guardar la nota del cliente')


def dispatch_invites(env, pairs, sender_name):
    """pairs: [(recipient, raw_token)] devueltos por send_envelope / complete_signature."""
    for rc, token in pairs:
        N.invite(env, rc, token, sender_name)


def finalize(env_id):
    """Genera el PDF final sellado, lo archiva en el cliente y envía copias."""
    from services.contract_pdf import build_final_pdf
    env = C.get_envelope_by_id_unscoped(env_id)
    org = N.org_name(env['org_id'])
    with open(C.original_path(env), 'rb') as fh:
        original = fh.read()
    if C.sha256_hex(original) != env['original_sha256']:
        raise RuntimeError('El PDF original cambió en disco; no se puede sellar.')

    env['completed_at'] = C.now_utc()
    env['events'] = env['events'] + [{
        'event': 'completed', 'created_at': env['completed_at'], 'actor': 'Sistema',
        'detail': 'Todas las firmas recibidas', 'ip': '', 'user_agent': ''}]
    data, sealed = build_final_pdf(env, original, org)
    with open(C.final_path(env), 'wb') as fh:
        fh.write(data)
    digest = C.sha256_hex(data)
    C.mark_completed(env['id'], digest)
    C.log_event(env['id'], env['org_id'], 'completed', actor='Sistema',
                detail=f'SHA-256 final {digest}' + ('' if sealed else ' · sin sello PAdES'))

    filename = safe_filename(env['title'], '_firmado')
    if env.get('client_id'):
        try:
            from models.client_document import client_dir, add_file_document
            folder = client_dir(env['org_id'], env['client_id'])
            os.makedirs(folder, exist_ok=True)
            stored = f'{uuid.uuid4().hex}.pdf'
            with open(os.path.join(folder, stored), 'wb') as fh:
                fh.write(data)
            add_file_document(env['client_id'], env['org_id'], f'{env["title"]} (firmado)', filename, stored,
                              'application/pdf', len(data))
            C.log_event(env['id'], env['org_id'], 'archived', actor='Sistema')
        except Exception:
            log.exception('Contratos: no se pudo archivar en documentos del cliente')
        client_note(env['client_id'], f'Contrato firmado por todas las partes: {env["title"]} (sobre {env["uid"]})')

    env = C.get_envelope(env['id'], env['org_id'])
    N.send_completed_copies(env, data, filename)
    N.notify_sender(env, f'✅ Completado: {env["title"]}',
                    [f'Todas las partes firmaron "{env["title"]}".', f'ID del sobre: {env["uid"]}',
                     'El PDF sellado ya está en el perfil del cliente y en Contratos.'],
                    attachments=[(filename, data)])
    return env


def after_signature(env, rc, outcome, pairs):
    sender = env.get('created_by_name') or N.org_name(env['org_id'])
    client_note(env.get('client_id'), f'{rc["name"]} firmó "{env["title"]}"')
    if outcome == 'completed':
        try:
            finalize(env['id'])
        except Exception:
            log.exception('Contratos: error al finalizar el sobre %s', env['id'])
            C.log_event(env['id'], env['org_id'], 'notify_failed', actor='Sistema',
                        detail='Error al generar el PDF final; reintenta desde el detalle del sobre.')
    else:
        dispatch_invites(env, pairs, sender)
        if not pairs:
            N.notify_sender(env, f'{rc["name"]} firmó: {env["title"]}',
                            [f'{rc["name"]} firmó "{env["title"]}". Faltan otras firmas del mismo grupo.'])
        else:
            N.notify_sender(env, f'{rc["name"]} firmó: {env["title"]}',
                            [f'{rc["name"]} firmó "{env["title"]}".',
                             'Enviamos el enlace al siguiente firmante: ' + ', '.join(p[0]['name'] for p in pairs) + '.'])
