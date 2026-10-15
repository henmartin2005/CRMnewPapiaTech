"""Documentos y enlaces asociados a cada cliente (PDF, archivos, Google Drive...)."""
import os
import shutil

from database import get_db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_ROOT = os.path.join(BASE_DIR, 'client_docs')

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB por archivo
ALLOWED_EXTS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'txt', 'rtf', 'odt',
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'svg', 'zip',
}
ACCEPT_ATTR = ','.join('.' + e for e in sorted(ALLOWED_EXTS))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        INTEGER NOT NULL DEFAULT 1,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    kind          TEXT    NOT NULL CHECK (kind IN ('file', 'link')),
    title         TEXT    NOT NULL,
    original_name TEXT,
    stored_name   TEXT,
    mime_type     TEXT,
    size_bytes    INTEGER,
    url           TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


# Archivos que el cliente sube desde su portal: no se permite SVG (puede llevar JavaScript
# y se sirve desde el mismo dominio que el CRM).
CLIENT_ALLOWED_EXTS = ALLOWED_EXTS - {'svg'}
CLIENT_ACCEPT_ATTR = ','.join('.' + e for e in sorted(CLIENT_ALLOWED_EXTS))
CLIENT_MAX_FILES = 10
IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'svg'}
CATEGORIES = [('logo', 'Logo del negocio'), ('imagen', 'Imágenes'), ('documento', 'Documentos')]
CATEGORY_LABELS = dict(CATEGORIES)

# Columnas añadidas después de la tabla original
_EXTRA_COLS = (
    ('portal_visible', "INTEGER NOT NULL DEFAULT 0"),
    ('uploaded_by', "TEXT NOT NULL DEFAULT 'admin'"),   # 'admin' | 'client'
    ('category', "TEXT"),                               # logo | imagen | documento
    ('admin_seen', "INTEGER NOT NULL DEFAULT 1"),       # 0 = subido por el cliente y aún no revisado
)


def _db():
    db = get_db()
    db.execute(_SCHEMA)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_docs_client ON client_documents(client_id)"
    )
    cols = {r[1] for r in db.execute("PRAGMA table_info(client_documents)").fetchall()}
    changed = False
    for name, ddl in _EXTRA_COLS:
        if name not in cols:
            db.execute(f"ALTER TABLE client_documents ADD COLUMN {name} {ddl}")
            changed = True
    if changed:
        db.commit()
    return db


def ensure_schema():
    _db().close()


def client_dir(org_id, client_id):
    return os.path.join(DOCS_ROOT, str(int(org_id)), str(int(client_id)))


def file_path(doc):
    return os.path.join(client_dir(doc['org_id'], doc['client_id']), doc['stored_name'])


def _human_size(n):
    if not n:
        return ''
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:.0f} {unit}" if unit == 'B' else f"{n:.1f} {unit}"
        n /= 1024


def _decorate(row):
    d = dict(row)
    if d['kind'] == 'link':
        url = (d.get('url') or '').lower()
        if 'docs.google.com/document' in url:
            d['icon'], d['meta'] = 'bi-file-earmark-text', 'Google Docs'
        elif 'docs.google.com/spreadsheets' in url:
            d['icon'], d['meta'] = 'bi-file-earmark-spreadsheet', 'Google Sheets'
        elif 'docs.google.com/presentation' in url:
            d['icon'], d['meta'] = 'bi-file-earmark-slides', 'Google Slides'
        elif 'drive.google.com' in url or 'docs.google.com' in url:
            d['icon'], d['meta'] = 'bi-google', 'Google Drive'
        else:
            d['icon'], d['meta'] = 'bi-link-45deg', 'Enlace'
    else:
        ext = (d.get('original_name') or '').rsplit('.', 1)[-1].lower()
        icons = {
            'pdf': 'bi-file-earmark-pdf', 'doc': 'bi-file-earmark-word',
            'docx': 'bi-file-earmark-word', 'odt': 'bi-file-earmark-word',
            'xls': 'bi-file-earmark-excel', 'xlsx': 'bi-file-earmark-excel',
            'csv': 'bi-file-earmark-spreadsheet', 'ppt': 'bi-file-earmark-ppt',
            'pptx': 'bi-file-earmark-ppt', 'zip': 'bi-file-earmark-zip',
            'txt': 'bi-file-earmark-text', 'rtf': 'bi-file-earmark-text',
        }
        if ext in IMAGE_EXTS:
            d['icon'] = 'bi-file-earmark-image'
            # HEIC no se muestra en la mayoría de navegadores: sin miniatura
            d['is_image'] = ext != 'heic'
        else:
            d['icon'] = icons.get(ext, 'bi-file-earmark')
        d['meta'] = ' · '.join(x for x in (ext.upper(), _human_size(d.get('size_bytes'))) if x)
    d['from_client'] = d.get('uploaded_by') == 'client'
    d['category_label'] = CATEGORY_LABELS.get(d.get('category') or '', '')
    return d


def list_documents(client_id, org_id):
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM client_documents WHERE client_id=? AND org_id=? "
            "ORDER BY created_at DESC, id DESC",
            (client_id, org_id),
        ).fetchall()
        return [_decorate(r) for r in rows]
    finally:
        db.close()


def get_document(doc_id, client_id, org_id):
    db = _db()
    try:
        row = db.execute(
            "SELECT * FROM client_documents WHERE id=? AND client_id=? AND org_id=?",
            (doc_id, client_id, org_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def add_file_document(client_id, org_id, title, original_name, stored_name, mime_type, size_bytes):
    db = _db()
    try:
        db.execute(
            "INSERT INTO client_documents (org_id, client_id, kind, title, original_name, "
            "stored_name, mime_type, size_bytes) VALUES (?, ?, 'file', ?, ?, ?, ?, ?)",
            (org_id, client_id, title, original_name, stored_name, mime_type, size_bytes),
        )
        db.commit()
    finally:
        db.close()


def add_link_document(client_id, org_id, title, url):
    db = _db()
    try:
        db.execute(
            "INSERT INTO client_documents (org_id, client_id, kind, title, url) "
            "VALUES (?, ?, 'link', ?, ?)",
            (org_id, client_id, title, url),
        )
        db.commit()
    finally:
        db.close()


def add_client_upload(client_id, org_id, title, original_name, stored_name, mime_type,
                      size_bytes, category):
    """Archivo subido por el cliente desde el portal: visible para él y marcado como nuevo
    para el administrador."""
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO client_documents (org_id, client_id, kind, title, original_name, "
            "stored_name, mime_type, size_bytes, portal_visible, uploaded_by, category, admin_seen) "
            "VALUES (?, ?, 'file', ?, ?, ?, ?, ?, 1, 'client', ?, 0)",
            (org_id, client_id, title, original_name, stored_name, mime_type, size_bytes,
             category if category in CATEGORY_LABELS else 'documento'),
        )
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def list_client_uploads(client_id, org_id):
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM client_documents WHERE client_id=? AND org_id=? AND uploaded_by='client' "
            "ORDER BY created_at DESC, id DESC",
            (client_id, org_id),
        ).fetchall()
        return [_decorate(r) for r in rows]
    finally:
        db.close()


def mark_client_uploads_seen(client_id, org_id):
    db = _db()
    try:
        db.execute(
            "UPDATE client_documents SET admin_seen=1 WHERE client_id=? AND org_id=? AND admin_seen=0",
            (client_id, org_id),
        )
        db.commit()
    finally:
        db.close()


def set_category(doc_id, client_id, org_id, category):
    if category not in CATEGORY_LABELS:
        return False
    db = _db()
    try:
        cur = db.execute(
            "UPDATE client_documents SET category=? WHERE id=? AND client_id=? AND org_id=?",
            (category, doc_id, client_id, org_id),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def delete_document(doc_id, client_id, org_id):
    doc = get_document(doc_id, client_id, org_id)
    if not doc:
        return False
    db = _db()
    try:
        db.execute("DELETE FROM client_documents WHERE id=?", (doc_id,))
        db.commit()
    finally:
        db.close()
    if doc['kind'] == 'file' and doc.get('stored_name'):
        try:
            os.remove(file_path(doc))
        except OSError:
            pass
    return True


def delete_client_files(client_id, org_id):
    shutil.rmtree(client_dir(org_id, client_id), ignore_errors=True)
