"""Idempotent Videos migration: python migrate_videos.py."""
from database import get_db


def migrate():
    db = get_db()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS drive_carpetas (
            id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id),
            tipo TEXT NOT NULL CHECK(tipo IN ('raiz','cliente')),
            cliente_id INTEGER REFERENCES clients(id), folder_id TEXT NOT NULL,
            nombre TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS drive_folder_owner
            ON drive_carpetas(org_id, tipo, COALESCE(cliente_id,0));
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id),
            cliente_id INTEGER REFERENCES clients(id), proyecto_id INTEGER,
            titulo TEXT NOT NULL, descripcion TEXT NOT NULL DEFAULT '',
            drive_file_id TEXT NOT NULL, drive_folder_id TEXT NOT NULL,
            mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
            duracion_seg REAL, tipo TEXT NOT NULL CHECK(tipo IN ('original','editado')),
            video_padre_id INTEGER REFERENCES videos(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT,
            UNIQUE(org_id,drive_file_id)
        );
        CREATE INDEX IF NOT EXISTS videos_library ON videos(org_id,cliente_id,deleted_at,created_at);
        CREATE TABLE IF NOT EXISTS videos_uploads (
            nonce TEXT PRIMARY KEY, org_id INTEGER NOT NULL, cliente_id INTEGER,
            folder_id TEXT NOT NULL, nombre TEXT NOT NULL, mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    migrate()
    print('Migración Videos completada.')
