import sqlite3
import os

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

        CREATE INDEX IF NOT EXISTS idx_emails_client ON emails(client_id);
    """)

    conn.commit()

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

    # ── Migrations: columns added after initial deploy ────────────────────────
    try:
        conn.execute("ALTER TABLE clients ADD COLUMN source TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass

    conn.close()
