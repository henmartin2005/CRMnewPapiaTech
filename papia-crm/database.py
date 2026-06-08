import sqlite3
import os
from werkzeug.security import generate_password_hash as _gen_hash

def generate_password_hash(password):
    return _gen_hash(password, method='pbkdf2:sha256')

DATABASE = os.path.join(os.path.dirname(__file__), 'papia_crm.db')


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL DEFAULT 'starter',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS org_modules (
            org_id  INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            module  TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (org_id, module)
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            company TEXT,
            project_type TEXT NOT NULL,
            project_details TEXT,
            pipeline_stage TEXT NOT NULL DEFAULT 'new_lead',
            total_cost REAL NOT NULL DEFAULT 0,
            amount_paid REAL NOT NULL DEFAULT 0,
            brochure_sent INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            note_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            summary TEXT NOT NULL,
            result TEXT,
            next_date DATE,
            next_at DATETIME,
            reminder_comment TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            payment_date DATE DEFAULT (date('now')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id     INTEGER REFERENCES clients(id),
            phone         TEXT NOT NULL,
            direction     TEXT NOT NULL,
            message       TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'received',
            wa_message_id TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_wa_phone     ON whatsapp_messages(phone);
        CREATE INDEX IF NOT EXISTS idx_wa_status    ON whatsapp_messages(status);
        CREATE INDEX IF NOT EXISTS idx_wa_client    ON whatsapp_messages(client_id);

        CREATE TABLE IF NOT EXISTS emails (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id        INTEGER REFERENCES clients(id),
            direction        TEXT NOT NULL DEFAULT 'sent',
            to_email         TEXT NOT NULL DEFAULT '',
            subject          TEXT NOT NULL,
            body             TEXT NOT NULL,
            gmail_message_id TEXT,
            status           TEXT NOT NULL DEFAULT 'sent',
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_templates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            subject    TEXT NOT NULL,
            body       TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gmail_tokens (
            id            INTEGER PRIMARY KEY DEFAULT 1,
            access_token  TEXT,
            refresh_token TEXT,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            client_name TEXT NOT NULL,
            business_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            title TEXT NOT NULL,
            project_type TEXT NOT NULL,
            proposal_language TEXT NOT NULL DEFAULT 'en',
            proposal_purpose TEXT NOT NULL DEFAULT 'landing_page',
            project_description TEXT,
            client_needs TEXT,
            project_objective TEXT,
            selected_services TEXT NOT NULL DEFAULT '[]',
            subtotal REAL NOT NULL DEFAULT 0,
            discount REAL NOT NULL DEFAULT 0,
            taxes REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL DEFAULT 0,
            initial_payment REAL NOT NULL DEFAULT 0,
            remaining_balance REAL NOT NULL DEFAULT 0,
            payment_terms TEXT,
            payment_schedule TEXT,
            payment_methods TEXT,
            start_date DATE,
            estimated_delivery_date DATE,
            business_days INTEGER NOT NULL DEFAULT 0,
            milestones TEXT NOT NULL DEFAULT '[]',
            terms_and_conditions TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft',
            pdf_url TEXT,
            email_draft_id INTEGER,
            sent_at DATETIME,
            accepted_via_whatsapp_at DATETIME,
            whatsapp_acceptance_link TEXT,
            client_approval_status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER REFERENCES proposals(id) ON DELETE SET NULL,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            attachment_name TEXT,
            attachment_url TEXT,
            status TEXT NOT NULL DEFAULT 'ready_to_send',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name  TEXT NOT NULL DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'user',
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_modules (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            module  TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, module)
        );

        CREATE TABLE IF NOT EXISTS payment_links (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            stripe_session_id TEXT,
            stripe_link_url   TEXT NOT NULL,
            amount            REAL NOT NULL,
            currency          TEXT NOT NULL DEFAULT 'usd',
            description       TEXT,
            status            TEXT NOT NULL DEFAULT 'pending',
            paid_at           DATETIME,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_payment_links_client  ON payment_links(client_id);
        CREATE INDEX IF NOT EXISTS idx_payment_links_session ON payment_links(stripe_session_id);
        CREATE TABLE IF NOT EXISTS meta_channel_connections (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id            INTEGER NOT NULL DEFAULT 1,
            channel           TEXT NOT NULL,
            page_id           TEXT NOT NULL,
            page_name         TEXT,
            page_access_token TEXT NOT NULL,
            is_active         INTEGER NOT NULL DEFAULT 1,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org_id, channel, page_id)
        );

        CREATE TABLE IF NOT EXISTS meta_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id          INTEGER NOT NULL DEFAULT 1,
            client_id       INTEGER REFERENCES clients(id),
            channel         TEXT NOT NULL,
            sender_id       TEXT NOT NULL,
            page_id         TEXT NOT NULL DEFAULT '',
            direction       TEXT NOT NULL,
            message         TEXT NOT NULL,
            meta_message_id TEXT,
            status          TEXT NOT NULL DEFAULT 'received',
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_meta_msg_sender ON meta_messages(sender_id);
        CREATE INDEX IF NOT EXISTS idx_meta_msg_org    ON meta_messages(org_id);
        CREATE INDEX IF NOT EXISTS idx_meta_msg_client ON meta_messages(client_id);
        CREATE INDEX IF NOT EXISTS idx_emails_client ON emails(client_id);
        CREATE INDEX IF NOT EXISTS idx_proposals_client ON proposals(client_id);
        CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
        CREATE INDEX IF NOT EXISTS idx_email_drafts_proposal ON email_drafts(proposal_id);
    """)

    conn.commit()

    # ── Seed PapiaTech as org_id=1 ────────────────────────────────────────────
    if not conn.execute("SELECT 1 FROM organizations WHERE id=1").fetchone():
        conn.execute(
            "INSERT INTO organizations (id, name, slug, plan) VALUES (1, 'Papia Technology Solutions', 'papiatech', 'enterprise')"
        )
        conn.commit()

    # ── Seed org_modules for every org that doesn't have them yet ────────────
    _ORG_MODULES = ['whatsapp', 'emails', 'calendar', 'proposals', 'tasks']
    for org_row in conn.execute("SELECT id FROM organizations").fetchall():
        for mod in _ORG_MODULES:
            conn.execute(
                "INSERT OR IGNORE INTO org_modules (org_id, module, enabled) VALUES (?, ?, 1)",
                (org_row['id'], mod),
            )
    conn.commit()

    # ── Multi-tenant migrations: add org_id columns ───────────────────────────
    migrations = [
        "ALTER TABLE clients ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE follow_ups ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE whatsapp_messages ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE emails ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE email_templates ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE proposals ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE gmail_tokens ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE settings ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE clients ADD COLUMN source TEXT DEFAULT ''",
        "ALTER TABLE follow_ups ADD COLUMN next_at DATETIME",
        "ALTER TABLE follow_ups ADD COLUMN reminder_comment TEXT",
        "ALTER TABLE organizations ADD COLUMN stripe_secret_key TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN stripe_webhook_secret TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN app_base_url TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN gmail_client_id TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN gmail_client_secret TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN logo_url TEXT DEFAULT ''",
        "ALTER TABLE organizations ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE payment_links ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass

    proposal_columns = [
        ("proposal_language", "TEXT NOT NULL DEFAULT 'en'"),
        ("proposal_purpose", "TEXT NOT NULL DEFAULT 'landing_page'"),
        ("pdf_url", "TEXT"),
        ("email_draft_id", "INTEGER"),
        ("sent_at", "DATETIME"),
        ("accepted_via_whatsapp_at", "DATETIME"),
        ("whatsapp_acceptance_link", "TEXT"),
        ("client_approval_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ]
    for column, definition in proposal_columns:
        try:
            conn.execute(f"ALTER TABLE proposals ADD COLUMN {column} {definition}")
            conn.commit()
        except Exception:
            pass

    # ── Seed default email templates ──────────────────────────────────────────
    existing = conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
    if existing == 0:
        conn.executemany(
            "INSERT INTO email_templates (name, subject, body) VALUES (?, ?, ?)",
            [
                (
                    'Saludo inicial',
                    'Hola {nombre}, bienvenido a Papia Tech',
                    'Hola {nombre},\n\nMi nombre es Henrry y soy parte del equipo de Papia Technology Solutions LLC.\n\nNos alegra mucho que estés considerando trabajar con nosotros. Estamos especializados en desarrollo web, aplicaciones móviles y soluciones CRM a medida.\n\nMe gustaría agendar una llamada para entender mejor tus necesidades y cómo podemos ayudarte.\n\n¿Tienes disponibilidad esta semana?\n\nQuedo atento,\nHenrry Martín\nPapia Technology Solutions LLC',
                ),
                (
                    'Envío de propuesta',
                    'Propuesta de proyecto — Papia Technology Solutions',
                    'Hola {nombre},\n\nEspero que estés muy bien. Tal como conversamos, adjunto la propuesta detallada para tu proyecto.\n\nEn ella encontrarás:\n• Alcance y entregables\n• Cronograma estimado\n• Inversión total\n\nQuedo disponible para resolver cualquier duda o hacer ajustes según tus necesidades.\n\nSaludos,\nHenrry Martín\nPapia Technology Solutions LLC',
                ),
                (
                    'Follow-up',
                    'Seguimiento — Papia Technology Solutions',
                    'Hola {nombre},\n\nEspero que todo esté marchando bien. Quería dar seguimiento a nuestra conversación y ver si tuviste oportunidad de revisar la información que te compartí.\n\nEstoy aquí para responder cualquier pregunta o brindar información adicional que necesites para tomar una decisión.\n\n¿Hay algo en lo que pueda ayudarte?\n\nSaludos,\nHenrry Martín\nPapia Technology Solutions LLC',
                ),
                (
                    'Recordatorio de pago',
                    'Recordatorio de pago — Papia Technology Solutions',
                    'Hola {nombre},\n\nEspero que estés bien. Te escribimos para recordarte que tienes un saldo pendiente con nosotros.\n\nSi ya realizaste el pago, por favor ignora este mensaje. De lo contrario, te agradecería que te pongas en contacto para coordinar el método de pago más conveniente para ti.\n\nQuedamos a tu disposición.\n\nSaludos,\nHenrry Martín\nPapia Technology Solutions LLC',
                ),
            ]
        )
        conn.commit()

    # ── Seed users ───────────────────────────────────────────────────────────
    ALL_MODULES = ['whatsapp', 'emails', 'calendar', 'proposals', 'tasks']

    admin_user = os.getenv('ADMIN_USERNAME', 'admin').strip()
    admin_pass = os.getenv('ADMIN_PASSWORD', 'admin').strip()
    if not conn.execute("SELECT 1 FROM users WHERE username=?", (admin_user,)).fetchone():
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, role, org_id) VALUES (?, ?, 'Admin', 'superadmin', 1)",
            (admin_user, generate_password_hash(admin_pass)),
        )
        conn.commit()
    else:
        # Migrate existing admin to superadmin if they are currently 'admin'
        conn.execute(
            "UPDATE users SET role='superadmin', org_id=1 WHERE username=? AND role='admin'",
            (admin_user,),
        )
        conn.commit()

    if not conn.execute("SELECT 1 FROM users WHERE username='Testing'").fetchone():
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, role, org_id) VALUES ('Testing', ?, 'Testing', 'user', 1)",
            (generate_password_hash('Testing'),),
        )
        conn.commit()
        testing_id = conn.execute("SELECT id FROM users WHERE username='Testing'").fetchone()['id']
        conn.executemany(
            "INSERT OR IGNORE INTO user_modules (user_id, module, enabled) VALUES (?, ?, 1)",
            [(testing_id, m) for m in ALL_MODULES],
        )
        conn.commit()

    # ── Seed default company settings ────────────────────────────────────────
    defaults = [
        ('company_logo_url',  ''),
        ('signature_name',    'Henrry Martín'),
        ('signature_title',   'CEO & Founder'),
        ('signature_phone',   ''),
        ('signature_email',   ''),
        ('signature_website', 'https://papiatech.com'),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    conn.commit()

    conn.close()
