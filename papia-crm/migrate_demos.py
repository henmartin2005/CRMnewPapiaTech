"""Idempotent Demos migration: python migrate_demos.py.

Tablas:
- demos         catálogo de demos (grooming, barbería, salón…), uno por nicho.
- demo_links    código de acceso por prospecto (NEGOCIO-XXXX).
- demo_logs     entradas, secciones vistas, salidas y bloqueos.
- demo_rate     intentos de validación por IP (anti fuerza bruta).
"""
from database import get_db

SEED_DEMOS = [
    # (org_id, slug, nombre, url, color)
    (1, 'grooming', 'Grooming móvil', 'https://groomingmiami.lovable.app', '#0E9DA2'),
]


def migrate():
    db = get_db()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS demos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id      INTEGER NOT NULL REFERENCES organizations(id),
            slug        TEXT NOT NULL,
            nombre      TEXT NOT NULL,
            url         TEXT NOT NULL,
            color       TEXT NOT NULL DEFAULT '#2A5BFF',
            mensaje     TEXT NOT NULL DEFAULT '',
            activo      INTEGER NOT NULL DEFAULT 1,
            creado_el   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org_id, slug)
        );

        CREATE TABLE IF NOT EXISTS demo_links (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id               INTEGER NOT NULL REFERENCES organizations(id),
            demo_id              INTEGER NOT NULL REFERENCES demos(id),
            codigo               TEXT NOT NULL UNIQUE,
            cliente_id           INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            nombre_prospecto     TEXT NOT NULL,
            negocio              TEXT NOT NULL,
            telefono             TEXT NOT NULL DEFAULT '',
            color                TEXT,
            max_visitas          INTEGER NOT NULL DEFAULT 3,
            visitas_usadas       INTEGER NOT NULL DEFAULT 0,
            vence_el             TEXT NOT NULL,
            activo               INTEGER NOT NULL DEFAULT 1,
            ilimitado            INTEGER NOT NULL DEFAULT 0,
            amarrar_dispositivo  INTEGER NOT NULL DEFAULT 0,
            dispositivo_amarrado TEXT,
            creado_el            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS demo_links_org ON demo_links(org_id, creado_el);
        CREATE INDEX IF NOT EXISTS demo_links_cliente ON demo_links(cliente_id);

        CREATE TABLE IF NOT EXISTS demo_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id           INTEGER NOT NULL REFERENCES demo_links(id) ON DELETE CASCADE,
            fecha_hora        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            tipo              TEXT NOT NULL CHECK (tipo IN ('entrada','seccion_vista','salida','bloqueado')),
            seccion           TEXT,
            dispositivo       TEXT,
            duracion_segundos INTEGER
        );
        CREATE INDEX IF NOT EXISTS demo_logs_link ON demo_logs(link_id, fecha_hora);

        CREATE TABLE IF NOT EXISTS demo_rate (
            ip_hash    TEXT NOT NULL,
            creado_el  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS demo_rate_ip ON demo_rate(ip_hash, creado_el);
        """)
        for org_id, slug, nombre, url, color in SEED_DEMOS:
            if db.execute('SELECT 1 FROM organizations WHERE id=?', (org_id,)).fetchone():
                db.execute(
                    'INSERT OR IGNORE INTO demos(org_id, slug, nombre, url, color) VALUES (?,?,?,?,?)',
                    (org_id, slug, nombre, url, color),
                )
        db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    migrate()
    print('Migración Demos completada.')
