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
    """)

    conn.commit()

    # Migrations: add columns introduced after initial deploy
    try:
        conn.execute("ALTER TABLE clients ADD COLUMN source TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # column already exists

    conn.close()
