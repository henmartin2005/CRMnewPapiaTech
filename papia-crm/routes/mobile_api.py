from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import logging
import re
import secrets
import urllib.parse

import jwt
from flask import Blueprint, current_app, g, jsonify, redirect, request, session
from werkzeug.security import check_password_hash

from database import get_db
from models.client import (
    FOLLOW_UP_METHODS,
    PIPELINE_STAGES,
    PROJECT_TYPES,
    add_followup,
    complete_followup,
    create_client,
    get_all_clients,
    get_all_tasks,
    get_client,
    get_client_followups,
    get_client_notes,
    get_clients_by_stage,
    get_dashboard_stats,
    get_org_pipeline_stages,
    update_client,
    update_pipeline_stage,
)
from models import payment_schedule
from routes.whatsapp import (
    _record_outbound,
    deliver,
    get_conversation,
    get_conversations,
    get_unread_count as get_whatsapp_unread_count,
    mark_read as mark_whatsapp_read,
    messages_to_dicts,
)
from services import ai_agent, push, zernio

log = logging.getLogger(__name__)

mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile")


def _token_secret():
    secret = current_app.secret_key or ""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "org_id": user["org_id"] if "org_id" in user.keys() else 1,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
    }
    return jwt.encode(payload, _token_secret(), algorithm="HS256")


def _row_dict(row):
    return dict(row) if row is not None else None


def _client_payload(row):
    data = _row_dict(row)
    if not data:
        return None
    data["full_name"] = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    data["pending"] = float(data.get("pending") or (data.get("total_cost", 0) - data.get("amount_paid", 0)))
    data["total_cost"] = float(data.get("total_cost") or 0)
    data["amount_paid"] = float(data.get("amount_paid") or 0)
    data["brochure_sent"] = bool(data.get("brochure_sent"))
    return data


def _task_payload(row):
    data = _row_dict(row)
    if not data:
        return None
    data["completed"] = bool(data.get("completed"))
    data["client_name"] = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    return data


def _json_error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def mobile_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _json_error("Missing bearer token", 401)
        token = header.removeprefix("Bearer ").strip()
        try:
            payload = jwt.decode(token, _token_secret(), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return _json_error("Token expired", 401)
        except jwt.InvalidTokenError:
            return _json_error("Invalid token", 401)

        db = get_db()
        user = db.execute(
            "SELECT id, username, display_name, role, is_active, org_id FROM users WHERE id=?",
            (payload.get("sub"),),
        ).fetchone()
        db.close()
        if not user or not user["is_active"]:
            return _json_error("Inactive user", 401)

        g.mobile_user = user
        g.org_id = user["org_id"] if "org_id" in user.keys() else int(payload.get("org_id") or 1)
        return view(*args, **kwargs)

    return wrapped


@mobile_api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return _json_error("Username and password are required")

    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash, display_name, role, is_active, org_id "
        "FROM users WHERE username=? AND is_active=1",
        (username,),
    ).fetchone()
    db.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return _json_error("Invalid credentials", 401)

    return _login_response(user)


def _login_response(user):
    return jsonify({
        "ok": True,
        "token": _issue_token(user),
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "org_id": user["org_id"] if "org_id" in user.keys() else 1,
        },
    })


# ── Login con Google desde la app ────────────────────────────────────────────
# La app abre /api/mobile/login/google en el navegador del sistema. Se reutiliza
# el login con Google de la web (mismo cliente OAuth y misma URL de retorno);
# al terminar, el callback de auth.py llama a mobile_google_finish(), que vuelve
# a la app con un código de un solo uso que la app cambia por el token.

# Solo se vuelve al esquema de la app o al de Expo Go (desarrollo)
_APP_REDIRECT_RE = re.compile(r"^(papiacrmmobile|papiacrm|exp|exps)://", re.IGNORECASE)
_CODE_TTL_SECONDS = 120


def _with_params(url, params):
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def _ensure_login_codes(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS mobile_login_codes (
            code       TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)


@mobile_api_bp.route("/login/google")
def google_login_start():
    from routes import auth
    redirect_to = (request.args.get("redirect_uri") or "").strip()
    if not _APP_REDIRECT_RE.match(redirect_to):
        return _json_error("Invalid redirect_uri")
    client_id, _ = auth._google_creds()
    if not client_id:
        return redirect(_with_params(redirect_to, {"error": "Login con Google no está configurado."}))
    state = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state
    session["google_oauth_next"] = ""
    session["google_oauth_mode"] = "mobile"
    session["google_oauth_mobile_redirect"] = redirect_to
    params = {
        "client_id": client_id,
        "redirect_uri": auth._google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect(auth.GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))


def mobile_google_fail(message):
    redirect_to = session.pop("google_oauth_mobile_redirect", "")
    if not _APP_REDIRECT_RE.match(redirect_to or ""):
        return _json_error(message)
    return redirect(_with_params(redirect_to, {"error": message}))


def mobile_google_finish(email):
    from routes import auth
    redirect_to = session.get("google_oauth_mobile_redirect", "")
    db = get_db()
    try:
        auth._ensure_google_column(db)
        user = db.execute(
            "SELECT id FROM users WHERE lower(google_email) = ? AND is_active = 1", (email,)
        ).fetchone()
        if not user:
            db.close()
            return mobile_google_fail("Esta cuenta de Google no tiene acceso al CRM.")
        code = secrets.token_urlsafe(32)
        now = int(datetime.now(timezone.utc).timestamp())
        _ensure_login_codes(db)
        db.execute("DELETE FROM mobile_login_codes WHERE expires_at < ?", (now,))
        db.execute("INSERT INTO mobile_login_codes (code, user_id, expires_at) VALUES (?, ?, ?)",
                   (code, user["id"], now + _CODE_TTL_SECONDS))
        db.commit()
    finally:
        try:
            db.close()
        except Exception:
            pass
    session.pop("google_oauth_mobile_redirect", None)
    return redirect(_with_params(redirect_to, {"code": code}))


@mobile_api_bp.route("/login/google/exchange", methods=["POST"])
def google_login_exchange():
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip()
    if not code:
        return _json_error("code is required")
    now = int(datetime.now(timezone.utc).timestamp())
    db = get_db()
    try:
        _ensure_login_codes(db)
        row = db.execute("SELECT user_id, expires_at FROM mobile_login_codes WHERE code = ?", (code,)).fetchone()
        # Un solo uso: se borra aunque esté vencido
        db.execute("DELETE FROM mobile_login_codes WHERE code = ?", (code,))
        db.commit()
        if not row or row["expires_at"] < now:
            return _json_error("Invalid or expired code", 401)
        user = db.execute(
            "SELECT id, username, display_name, role, is_active, org_id FROM users WHERE id = ? AND is_active = 1",
            (row["user_id"],),
        ).fetchone()
    finally:
        db.close()
    if not user:
        return _json_error("Inactive user", 401)
    return _login_response(user)


@mobile_api_bp.route("/me")
@mobile_login_required
def me():
    user = g.mobile_user
    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "org_id": g.org_id,
        },
    })


@mobile_api_bp.route("/metadata")
@mobile_login_required
def metadata():
    return jsonify({
        "ok": True,
        "pipeline_stages": [{"key": key, "label": label} for key, label in get_org_pipeline_stages(g.org_id)],
        "default_pipeline_stages": [{"key": key, "label": label} for key, label in PIPELINE_STAGES],
        "project_types": [{"key": key, "label": label} for key, label in PROJECT_TYPES],
        "follow_up_methods": [{"key": key, "label": label} for key, label in FOLLOW_UP_METHODS],
    })


@mobile_api_bp.route("/dashboard")
@mobile_login_required
def dashboard():
    return jsonify({"ok": True, "stats": get_dashboard_stats(g.org_id)})


@mobile_api_bp.route("/clients", methods=["GET"])
@mobile_login_required
def clients():
    search = request.args.get("q", "").strip() or None
    rows = get_all_clients(g.org_id, search)
    return jsonify({"ok": True, "clients": [_client_payload(row) for row in rows]})


@mobile_api_bp.route("/clients", methods=["POST"])
@mobile_login_required
def create_mobile_client():
    data = request.get_json(silent=True) or {}
    errors = _validate_client_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    client_id = create_client(_client_form_data(data), org_id=g.org_id)
    return jsonify({"ok": True, "client": _client_payload(get_client(client_id, g.org_id))}), 201


@mobile_api_bp.route("/clients/<int:client_id>", methods=["GET"])
@mobile_login_required
def client_detail(client_id):
    client = get_client(client_id, g.org_id)
    if not client:
        return _json_error("Client not found", 404)
    return jsonify({
        "ok": True,
        "client": _client_payload(client),
        "notes": [_row_dict(row) for row in get_client_notes(client_id)],
        "followups": [_task_payload(row) for row in get_client_followups(client_id)],
        "installments": payment_schedule.list_installments(client_id, g.org_id),
    })


@mobile_api_bp.route("/clients/<int:client_id>", methods=["PATCH"])
@mobile_login_required
def update_mobile_client(client_id):
    if not get_client(client_id, g.org_id):
        return _json_error("Client not found", 404)
    data = request.get_json(silent=True) or {}
    errors = _validate_client_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    update_client(client_id, _client_form_data(data), org_id=g.org_id)
    return jsonify({"ok": True, "client": _client_payload(get_client(client_id, g.org_id))})


@mobile_api_bp.route("/clients/<int:client_id>/followups", methods=["POST"])
@mobile_login_required
def create_mobile_followup(client_id):
    if not get_client(client_id, g.org_id):
        return _json_error("Client not found", 404)
    data = request.get_json(silent=True) or {}
    summary = (data.get("summary") or "").strip()
    reminder_at = (data.get("reminder_at") or data.get("next_at") or "").strip()
    if not summary or not reminder_at:
        return _json_error("summary and reminder_at are required")
    method = (data.get("method") or "other").strip()
    if method not in [key for key, _ in FOLLOW_UP_METHODS]:
        return _json_error("Invalid follow-up method")
    add_followup(
        client_id=client_id,
        method=method,
        summary=summary,
        result=(data.get("result") or "").strip(),
        next_at=reminder_at,
        reminder_comment=(data.get("reminder_comment") or "").strip(),
    )
    return jsonify({"ok": True}), 201


@mobile_api_bp.route("/pipeline")
@mobile_login_required
def pipeline():
    stages = get_clients_by_stage(g.org_id)
    return jsonify({
        "ok": True,
        "stages": [
            {
                "key": key,
                "label": label,
                "clients": [_client_payload(client) for client in stages.get(key, [])],
            }
            for key, label in get_org_pipeline_stages(g.org_id)
        ],
    })


@mobile_api_bp.route("/pipeline/move", methods=["POST"])
@mobile_login_required
def move_pipeline_client():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    stage = data.get("stage")
    if stage not in [key for key, _ in PIPELINE_STAGES]:
        return _json_error("Invalid stage")
    if not get_client(client_id, g.org_id):
        return _json_error("Client not found", 404)
    update_pipeline_stage(client_id, stage, org_id=g.org_id)
    return jsonify({"ok": True})


@mobile_api_bp.route("/tasks")
@mobile_login_required
def tasks():
    rows = get_all_tasks(g.org_id)
    return jsonify({"ok": True, "tasks": [_task_payload(row) for row in rows]})


@mobile_api_bp.route("/whatsapp")
@mobile_login_required
def whatsapp_summary():
    conversations = [_whatsapp_conversation_payload(row) for row in get_conversations(g.org_id)]
    return jsonify({
        "ok": True,
        "unread": get_whatsapp_unread_count(g.org_id),
        "conversations": conversations,
    })


@mobile_api_bp.route("/whatsapp/messages")
@mobile_login_required
def whatsapp_messages():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return _json_error("phone is required")
    rows = get_conversation(phone, g.org_id)
    if not rows:
        # La app puede mandar el teléfono con otro formato: se prueba en E.164
        e164 = zernio.to_e164(phone)
        if e164 and e164 != phone:
            phone = e164
            rows = get_conversation(phone, g.org_id)
    if request.args.get("mark_read") == "1":
        mark_whatsapp_read(phone, g.org_id)
    return jsonify({
        "ok": True,
        "phone": phone,
        "messages": messages_to_dicts(rows),
    })


@mobile_api_bp.route("/whatsapp/read", methods=["POST"])
@mobile_login_required
def whatsapp_mark_read():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return _json_error("phone is required")
    mark_whatsapp_read(phone, g.org_id)
    return jsonify({"ok": True, "unread": get_whatsapp_unread_count(g.org_id)})


@mobile_api_bp.route("/tasks/<int:followup_id>/complete", methods=["POST"])
@mobile_login_required
def complete_task(followup_id):
    db = get_db()
    row = db.execute("""
        SELECT f.id
        FROM follow_ups f
        JOIN clients c ON c.id = f.client_id
        WHERE f.id=? AND c.org_id=?
    """, (followup_id, g.org_id)).fetchone()
    db.close()
    if not row:
        return _json_error("Task not found", 404)
    complete_followup(followup_id)
    return jsonify({"ok": True})


# ── WhatsApp: enviar y bot por contacto ──────────────────────────────────────

@mobile_api_bp.route("/whatsapp/send", methods=["POST"])
@mobile_login_required
def whatsapp_send():
    """Igual que /whatsapp/send de la web: envía, registra y pausa el bot (intervención humana)."""
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    client_id = data.get("client_id")
    if not phone or not message:
        return _json_error("phone and message are required")
    try:
        wa_id = deliver(phone, message, g.org_id)
        _record_outbound(phone, message, wa_id, g.org_id, client_id)
        try:
            ai_agent.pause(zernio.to_e164(phone), g.org_id)
        except Exception:
            log.exception("No se pudo pausar el bot")
        return jsonify({"ok": True, "id": wa_id})
    except zernio.TemplateRequired as exc:
        return jsonify({"ok": False, "template_required": True, "error": str(exc)}), 409
    except Exception as exc:
        log.exception("Error enviando WhatsApp desde la app")
        return _json_error(str(exc), 500)


@mobile_api_bp.route("/whatsapp/bot", methods=["GET"])
@mobile_login_required
def whatsapp_bot_state():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return _json_error("phone is required")
    state = ai_agent.contact_bot_state(zernio.to_e164(phone) or phone, g.org_id)
    return jsonify({"ok": True, "state": state})


@mobile_api_bp.route("/whatsapp/bot", methods=["POST"])
@mobile_login_required
def whatsapp_bot_toggle():
    data = request.get_json(silent=True) or {}
    raw = (data.get("phone") or "").strip()
    if not raw:
        return _json_error("phone is required")
    phone = zernio.to_e164(raw) or raw
    ai_agent.set_contact_enabled(phone, bool(data.get("enabled")), g.org_id)
    return jsonify({"ok": True, "state": ai_agent.contact_bot_state(phone, g.org_id)})


# ── Pagos (cronograma de cuotas) ─────────────────────────────────────────────

def _installment_state(row, today):
    if row["status"] == "paid":
        return "paid"
    if row["due_date"] < today:
        return "overdue"
    if row["due_date"] == today:
        return "today"
    return "pending"


@mobile_api_bp.route("/payments")
@mobile_login_required
def payments():
    today = datetime.now().strftime("%Y-%m-%d")
    db = payment_schedule._db()
    try:
        rows = db.execute("""
            SELECT s.*, c.first_name, c.last_name, c.company, c.phone
            FROM payment_schedule s
            JOIN clients c ON c.id = s.client_id
            WHERE s.org_id = ? AND c.org_id = ?
            ORDER BY s.due_date, s.id
        """, (g.org_id, g.org_id)).fetchall()
    finally:
        db.close()
    items = []
    for r in rows:
        d = dict(r)
        d["state"] = _installment_state(r, today)
        d["client_name"] = f"{d.get('first_name') or ''} {d.get('last_name') or ''}".strip()
        items.append(d)
    return jsonify({"ok": True, "payments": items})


@mobile_api_bp.route("/payments/<int:inst_id>/paid", methods=["POST"])
@mobile_login_required
def payment_mark_paid(inst_id):
    db = payment_schedule._db()
    try:
        row = db.execute("SELECT client_id FROM payment_schedule WHERE id = ? AND org_id = ?",
                         (inst_id, g.org_id)).fetchone()
    finally:
        db.close()
    if not row:
        return _json_error("Payment not found", 404)
    # Suma el monto a amount_paid del cliente, completa el recordatorio y deja nota (igual que la web)
    inst = payment_schedule.mark_paid(inst_id, row["client_id"], g.org_id)
    if inst is None:
        return _json_error("Payment already paid", 409)
    return jsonify({"ok": True, "client": _client_payload(get_client(row["client_id"], g.org_id))})


# ── Notificaciones push ──────────────────────────────────────────────────────

@mobile_api_bp.route("/push-token", methods=["POST"])
@mobile_login_required
def push_token_register():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not push.is_valid_token(token):
        return _json_error("Invalid Expo push token")
    push.register(token, g.mobile_user["id"], g.org_id, (data.get("platform") or "").strip())
    return jsonify({"ok": True})


@mobile_api_bp.route("/push-token", methods=["DELETE"])
@mobile_login_required
def push_token_unregister():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if token:
        push.unregister(token)
    return jsonify({"ok": True})


def _whatsapp_conversation_payload(row):
    first = row["first_name"] or ""
    last = row["last_name"] or ""
    name = f"{first} {last}".strip()
    return {
        "phone": row["phone"],
        "client_id": row["client_id"],
        "client_name": name,
        "last_message": row["last_message"] or "",
        "last_direction": row["last_direction"] or "",
        "last_status": row["last_status"] or "",
        "last_at": row["last_at"] or "",
        "unread": int(row["unread"] or 0),
    }


def _validate_client_payload(data):
    errors = []
    if not (data.get("first_name") or "").strip():
        errors.append("first_name is required")
    if not (data.get("email") or "").strip() and not (data.get("phone") or "").strip():
        errors.append("email or phone is required")
    if data.get("project_type") not in [key for key, _ in PROJECT_TYPES]:
        errors.append("valid project_type is required")
    if data.get("pipeline_stage", "new_lead") not in [key for key, _ in PIPELINE_STAGES]:
        errors.append("invalid pipeline_stage")
    try:
        total = float(data.get("total_cost") or 0)
        paid = float(data.get("amount_paid") or 0)
        if total < 0 or paid < 0:
            errors.append("amounts cannot be negative")
        if paid > total:
            errors.append("amount_paid cannot exceed total_cost")
    except (TypeError, ValueError):
        errors.append("amounts must be numeric")
    return errors


def _client_form_data(data):
    return {
        "first_name": (data.get("first_name") or "").strip(),
        "last_name": (data.get("last_name") or "").strip(),
        "email": (data.get("email") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "company": (data.get("company") or "").strip(),
        "project_type": (data.get("project_type") or "").strip(),
        "project_details": (data.get("project_details") or "").strip(),
        "pipeline_stage": (data.get("pipeline_stage") or "new_lead").strip(),
        "total_cost": data.get("total_cost") or 0,
        "amount_paid": data.get("amount_paid") or 0,
        "brochure_sent": bool(data.get("brochure_sent")),
    }
