"""Pruebas de adjuntos en Emails (redactar y enviar desde el perfil del cliente).
Ejecutar desde papia-crm/:  python3 tests/test_email_adjuntos.py
Usa una base de datos temporal y un Gmail simulado; no envía correos reales."""
import base64
import email
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
database.DATABASE = _tmp.name

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


class _FakeGmail:
    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        self.sent.append(body['raw'])
        return self

    def execute(self):
        return {'id': f'fake{len(self.sent)}'}


def _parts(raw):
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    return [(p.get_filename(), p.get_payload(decode=True)) for p in msg.walk() if p.get_filename()]


def run():
    import app as app_module
    from routes import emails as emails_mod
    from models import client_document as cd

    cd.DOCS_ROOT = tempfile.mkdtemp()
    fake = _FakeGmail()
    emails_mod._gmail_service = lambda org_id=1: fake

    flask_app = app_module.app
    flask_app.config['TESTING'] = True
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s['logged_in'] = True
        s['user_role'] = 'admin'
        s['org_id'] = 1
        s['user_id'] = 1

    db = database.get_db()
    cid = db.execute("INSERT INTO clients (first_name, last_name, email, phone, project_type, org_id) "
                     "VALUES ('Ana','Pérez','ana@x.com','7865550000','website',1)").lastrowid
    db.commit()
    db.close()

    # Documento en el perfil del cliente
    r = c.post(f'/clients/{cid}/documents/upload', data={'files': (io.BytesIO(PDF), 'Propuesta Ana.pdf')},
               content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code
    docs = cd.list_documents(cid, 1)
    assert len(docs) == 1
    doc_id = docs[0]['id']

    # 1) El perfil muestra el botón de enviar ese documento por email
    html = c.get(f'/clients/{cid}').get_data(as_text=True)
    assert f'attach=doc%3A{doc_id}' in html or f'attach=doc:{doc_id}' in html, 'falta botón enviar en el perfil'

    # 2) El redactor abre con el documento preseleccionado y con el campo de adjuntar
    html = c.get(f'/emails?client_id={cid}&compose=1&attach=doc:{doc_id}').get_data(as_text=True)
    assert 'id="composeFiles"' in html
    assert f'value="doc:{doc_id}" checked' in html, 'documento no preseleccionado'

    # 3) Envío con PDF subido + documento del perfil
    r = c.post('/emails/send', data={
        'to_email': 'ana@x.com', 'subject': 'Tu propuesta', 'body': 'Hola Ana, adjunto.',
        'client_id': str(cid), 'docs_client_id': str(cid), 'profile_docs': f'doc:{doc_id}',
        'attachments': (io.BytesIO(PDF), 'Factura.pdf'),
    }, content_type='multipart/form-data')
    d = r.get_json()
    assert d['success'] and d['attachments'] == 2, d
    names = dict(_parts(fake.sent[-1]))
    assert names == {'Factura.pdf': PDF, 'Propuesta Ana.pdf': PDF}, names.keys()

    # 4) Copia en Enviados y nota en el cliente
    db = database.get_db()
    row = db.execute("SELECT * FROM emails WHERE client_id=? ORDER BY id DESC", (cid,)).fetchone()
    note = db.execute("SELECT content FROM notes WHERE client_id=? ORDER BY id DESC", (cid,)).fetchone()
    db.close()
    assert 'Adjuntos: Factura.pdf, Propuesta Ana.pdf' in row['body'], row['body']
    assert 'Adjuntos:' in note['content'], note['content']

    # 5) No se puede adjuntar un documento de otro cliente
    db = database.get_db()
    other = db.execute("INSERT INTO clients (first_name, last_name, email, phone, project_type, org_id) VALUES ('Otro','X','o@x.com','7865550001','website',1)").lastrowid
    db.commit()
    db.close()
    r = c.post('/emails/send', data={'to_email': 'o@x.com', 'subject': 's', 'body': 'b',
                                     'docs_client_id': str(other), 'profile_docs': f'doc:{doc_id}'},
               content_type='multipart/form-data')
    assert r.status_code == 400, r.status_code

    # 6) Tipos no permitidos
    r = c.post('/emails/send', data={'to_email': 'o@x.com', 'subject': 's', 'body': 'b',
                                     'attachments': (io.BytesIO(b'x'), 'virus.exe')},
               content_type='multipart/form-data')
    assert r.status_code == 400

    # 7) Respuesta rápida en JSON sigue funcionando sin adjuntos
    r = c.post('/emails/send', json={'to_email': 'ana@x.com', 'subject': 'Re: x', 'body': 'ok', 'client_id': cid})
    assert r.get_json()['success']
    assert _parts(fake.sent[-1]) == []

    print('OK: adjuntos en Emails')


if __name__ == '__main__':
    try:
        run()
    finally:
        os.unlink(_tmp.name)
