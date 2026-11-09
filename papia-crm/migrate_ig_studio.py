"""Idempotent Instagram Studio migration: python migrate_ig_studio.py.

Tablas:
- ig_accounts        cuentas de Instagram conectadas con Instagram Login (token largo).
- ig_account_daily   snapshot diario por cuenta (seguidores, alcance…): así se mide el crecimiento.
- ig_media           publicaciones con sus métricas y el agente asignado.
- ig_agents          agentes con Claude (comentarios y DMs), uno por cuenta o para ambas.
- ig_comments        comentarios recibidos por webhook y lo que hizo el agente.
- ig_agent_runs      registro de cada ejecución del agente (latencia, estado, tokens).
- ig_weekly_reports  reporte semanal de estrategia generado por Claude.
- ig_plan_items      piezas del plan de contenido de cada reporte (aprobables).
"""
from database import get_db

DEFAULT_COMMENTS_PROMPT = (
    "Respondes comentarios públicos de Instagram en nombre de Henrry y PapiaTech "
    "(webs, CRMs, tiendas Shopify, apps y automatización con IA para negocios latinos en Miami).\n"
    "Respuestas cortas, de una o dos líneas, cercanas y en el idioma del comentario.\n"
    "Si alguien pide info, precio o comenta solo \"info\", respondes en público y continúas por DM "
    "preguntando qué necesita.\n"
    "Nunca das precios ni inventas datos. Ignoras spam y comentarios fuera de tema."
)

DEFAULT_DM_PROMPTS = {
    'Asistente DMs · Henrry': (
        "Eres Henrry, fundador de PapiaTech en Miami. Hablas cercano, directo y en español latino.\n"
        "Objetivo: entender qué necesita la persona, capturar nombre, email y servicio de interés, "
        "y ofrecer una llamada.\nNunca das precios. Si no sabes algo, dices que Henrry lo confirma."
    ),
    'Asistente DMs · PapiaTech': (
        "Eres el asistente de PapiaTech: webs, CRMs, tiendas Shopify y automatización con IA "
        "para negocios latinos.\nTono profesional y cálido. Captura nombre, email y servicio; "
        "ofrece agendar una llamada.\nNunca das precios ni prometes fechas."
    ),
}


def migrate():
    db = get_db()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS ig_accounts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id           INTEGER NOT NULL DEFAULT 1,
            ig_user_id       TEXT NOT NULL,
            username         TEXT NOT NULL DEFAULT '',
            name             TEXT NOT NULL DEFAULT '',
            picture_url      TEXT NOT NULL DEFAULT '',
            access_token     TEXT NOT NULL,
            token_expires_at TEXT,
            scopes           TEXT NOT NULL DEFAULT '',
            is_active        INTEGER NOT NULL DEFAULT 1,
            last_sync_at     TEXT,
            last_error       TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org_id, ig_user_id)
        );

        CREATE TABLE IF NOT EXISTS ig_account_daily (
            org_id        INTEGER NOT NULL DEFAULT 1,
            ig_user_id    TEXT NOT NULL,
            day           TEXT NOT NULL,
            followers     INTEGER,
            follows       INTEGER,
            media_count   INTEGER,
            reach         INTEGER,
            profile_views INTEGER,
            PRIMARY KEY (org_id, ig_user_id, day)
        );

        CREATE TABLE IF NOT EXISTS ig_agents (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id         INTEGER NOT NULL DEFAULT 1,
            name           TEXT NOT NULL,
            kind           TEXT NOT NULL DEFAULT 'comments',
            ig_user_id     TEXT NOT NULL DEFAULT '',
            prompt         TEXT NOT NULL DEFAULT '',
            enabled        INTEGER NOT NULL DEFAULT 1,
            is_default     INTEGER NOT NULL DEFAULT 0,
            reply_delay    INTEGER NOT NULL DEFAULT 20,
            cancel_on_new  INTEGER NOT NULL DEFAULT 1,
            business_hours INTEGER NOT NULL DEFAULT 0,
            auto_dm        INTEGER NOT NULL DEFAULT 1,
            create_leads   INTEGER NOT NULL DEFAULT 1,
            model          TEXT NOT NULL DEFAULT '',
            updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ig_media (
            media_id           TEXT PRIMARY KEY,
            org_id             INTEGER NOT NULL DEFAULT 1,
            ig_user_id         TEXT NOT NULL,
            caption            TEXT NOT NULL DEFAULT '',
            media_type         TEXT NOT NULL DEFAULT '',
            product_type       TEXT NOT NULL DEFAULT '',
            permalink          TEXT NOT NULL DEFAULT '',
            thumb_url          TEXT NOT NULL DEFAULT '',
            posted_at          TEXT,
            is_ad              INTEGER NOT NULL DEFAULT 0,
            like_count         INTEGER NOT NULL DEFAULT 0,
            comments_count     INTEGER NOT NULL DEFAULT 0,
            views              INTEGER,
            reach              INTEGER,
            saved              INTEGER,
            shares             INTEGER,
            total_interactions INTEGER,
            insights_at        TEXT,
            agent_id           INTEGER REFERENCES ig_agents(id) ON DELETE SET NULL,
            agent_off          INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ig_media_acct ON ig_media(org_id, ig_user_id, posted_at);

        CREATE TABLE IF NOT EXISTS ig_comments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id        INTEGER NOT NULL DEFAULT 1,
            ig_user_id    TEXT NOT NULL,
            comment_id    TEXT NOT NULL UNIQUE,
            media_id      TEXT NOT NULL DEFAULT '',
            parent_id     TEXT NOT NULL DEFAULT '',
            from_id       TEXT NOT NULL DEFAULT '',
            from_username TEXT NOT NULL DEFAULT '',
            text          TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            category      TEXT NOT NULL DEFAULT '',
            reply_text    TEXT NOT NULL DEFAULT '',
            dm_text       TEXT NOT NULL DEFAULT '',
            draft_text    TEXT NOT NULL DEFAULT '',
            client_id     INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            agent_id      INTEGER,
            error         TEXT NOT NULL DEFAULT '',
            received_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ig_comments_org ON ig_comments(org_id, received_at);

        CREATE TABLE IF NOT EXISTS ig_agent_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id      INTEGER NOT NULL DEFAULT 1,
            agent_id    INTEGER,
            channel     TEXT NOT NULL DEFAULT 'comment',
            ig_user_id  TEXT NOT NULL DEFAULT '',
            input       TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL DEFAULT '',
            latency_ms  INTEGER,
            status      TEXT NOT NULL DEFAULT 'ok',
            error       TEXT NOT NULL DEFAULT '',
            tokens_in   INTEGER NOT NULL DEFAULT 0,
            tokens_out  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_ig_runs_org ON ig_agent_runs(org_id, created_at);

        CREATE TABLE IF NOT EXISTS ig_weekly_reports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id       INTEGER NOT NULL DEFAULT 1,
            week_start   TEXT NOT NULL,
            data_json    TEXT NOT NULL DEFAULT '{}',
            model        TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(org_id, week_start)
        );

        CREATE TABLE IF NOT EXISTS ig_plan_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id  INTEGER NOT NULL REFERENCES ig_weekly_reports(id) ON DELETE CASCADE,
            org_id     INTEGER NOT NULL DEFAULT 1,
            day        TEXT NOT NULL,
            type       TEXT NOT NULL DEFAULT 'REEL',
            time       TEXT NOT NULL DEFAULT '',
            title      TEXT NOT NULL DEFAULT '',
            hook       TEXT NOT NULL DEFAULT '',
            account    TEXT NOT NULL DEFAULT '',
            approved   INTEGER NOT NULL DEFAULT 0
        );
        """)
        # Agentes iniciales de la org 1 (solo si aún no hay ninguno)
        if not db.execute("SELECT 1 FROM ig_agents WHERE org_id=1 LIMIT 1").fetchone():
            db.execute(
                "INSERT INTO ig_agents (org_id, name, kind, prompt, is_default) VALUES (1, ?, 'comments', ?, 1)",
                ('Agente Comentarios Papia', DEFAULT_COMMENTS_PROMPT))
            for name, prompt in DEFAULT_DM_PROMPTS.items():
                db.execute(
                    "INSERT INTO ig_agents (org_id, name, kind, prompt, enabled) VALUES (1, ?, 'dm', ?, 1)",
                    (name, prompt))
        db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    migrate()
    print('Instagram Studio: tablas listas.')
