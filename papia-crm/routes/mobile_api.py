from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib

import jwt
from flask import Blueprint, current_app, g, jsonify, request
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
from routes import whatsapp as wa_routes
from routes.whatsapp import (
    find_client_by_phone,
    get_conversation,
    get_conversations,
    get_unread_count as get_whatsapp_unread_count,
    mark_read as mark_whatsapp_read,
    messages_to_dicts,
)

from services import ai_agent, push, zernio

mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile")

_CLIENT_FIELDS = (
    "first_name", "last_name", "email", "phone", "company", "project_type",
    "project_details", "pipeline_stage", "total_cost", "amount_paid", "brochure_sent",
)


def _stage_keys():
    keys = {key for key, _ in PIPELINE_STAGES}
    try:
        keys |= {key for key, _ in get_org_pipeline_stages(g.org_id)}
    except Exception:
        pass
    return keys


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
    })


@mobile_api_bp.route("/clients/<int:client_id>", methods=["PATCH"])
@mobile_login_required
def update_mobile_client(client_id):
    if not get_client(client_id, g.org_id):
        return _json_error("Client not found", 404)
    data = request.get_json(silent=True) or {}
    current = dict(get_client(client_id, g.org_id))
    merged = {key: current.get(key) for key in _CLIENT_FIELDS}
    merged.update({key: value for key, value in data.items() if key in _CLIENT_FIELDS})
    errors = _validate_client_payload(merged)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    update_client(client_id, _client_form_data(merged), org_id=g.org_id)
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
    if stage not in _stage_keys():
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
    if request.args.get("mark_read") == "1":
        mark_whatsapp_read(phone, g.org_id)
    messages = messages_to_dicts(rows)
    for item in messages:
        if item.get("media_url"):
            item["media_url"] = f"/api/mobile/whatsapp/media/{item['id']}"
    client = find_client_by_phone(phone, g.org_id)
    return jsonify({
        "ok": True,
        "phone": phone,
        "messages": messages,
        "client": _client_payload(client) if client else None,
        "bot": ai_agent.contact_bot_state(zernio.to_e164(phone) or phone, g.org_id),
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
    if (data.get("pipeline_stage") or "new_lead") not in _stage_keys():
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


# ── WhatsApp: envío, adjuntos, plantillas y bot ──────────────────────────────

@mobile_api_bp.route("/whatsapp/send", methods=["POST"])
@mobile_login_required
def whatsapp_send():
    """Texto o plantilla. Misma lógica que el CRM web (pausa el bot, guarda en el chat)."""
    response = wa_routes.send_message()
    return _normalize_wa_response(response)


@mobile_api_bp.route("/whatsapp/send-media", methods=["POST"])
@mobile_login_required
def whatsapp_send_media():
    """multipart/form-data: phone, file, kind (audio|image|video|file|sticker), client_id."""
    response = wa_routes.send_media()
    return _normalize_wa_response(response)


def _normalize_wa_response(response):
    body, status = (response if isinstance(response, tuple) else (response, 200))
    data = body.get_json(silent=True) or {}
    out = {
        "ok": bool(data.get("success")),
        "id": data.get("sid"),
        "error": data.get("error"),
        "template_required": bool(data.get("template_required")),
    }
    return jsonify(out), status


@mobile_api_bp.route("/whatsapp/templates")
@mobile_login_required
def whatsapp_templates():
    data = wa_routes.templates_json().get_json(silent=True) or {}
    return jsonify({"ok": True, "templates": data.get("templates", []), "error": data.get("error")})


@mobile_api_bp.route("/whatsapp/media/<int:msg_id>")
@mobile_login_required
def whatsapp_media(msg_id):
    return wa_routes.media(msg_id)


@mobile_api_bp.route("/whatsapp/bot", methods=["POST"])
@mobile_login_required
def whatsapp_bot_toggle():
    data = request.get_json(silent=True) or {}
    raw = (data.get("phone") or "").strip()
    if not raw:
        return _json_error("phone is required")
    phone = zernio.to_e164(raw) or raw
    ai_agent.set_contact_enabled(phone, bool(data.get("enabled")), g.org_id)
    return jsonify({"ok": True, "bot": ai_agent.contact_bot_state(phone, g.org_id)})


# ── Dispositivos (notificaciones push) ───────────────────────────────────────

@mobile_api_bp.route("/devices", methods=["POST"])
@mobile_login_required
def register_device():
    data = request.get_json(silent=True) or {}
    try:
        push.register_device(
            g.org_id, g.mobile_user["id"], data.get("token"),
            platform=(data.get("platform") or "ios").strip(),
            environment=(data.get("environment") or "production").strip(),
            app_version=data.get("app_version") or "",
            device_name=data.get("device_name") or "",
        )
    except ValueError as exc:
        return _json_error(str(exc))
    if "notify_whatsapp" in data:
        push.set_device_prefs(data.get("token"), bool(data.get("notify_whatsapp")))
    return jsonify({"ok": True, "push_configured": push.is_configured()})


@mobile_api_bp.route("/devices", methods=["DELETE"])
@mobile_login_required
def unregister_device():
    data = request.get_json(silent=True) or {}
    push.unregister_device(data.get("token") or request.args.get("token"))
    return jsonify({"ok": True})


# ── Contratos (Papia Sign) ───────────────────────────────────────────────────

def _contract_models():
    from models import contract as C
    from services import contract_flow as FLOW
    from services import contract_notify as N
    return C, FLOW, N


def _actor_name():
    user = g.mobile_user
    return user["display_name"] or user["username"] or "CRM"


def _recipient_payload(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "email": r.get("email") or "",
        "phone": r.get("phone") or "",
        "role": r["role"],
        "routing_order": r["routing_order"],
        "status": r["status"],
        "status_label": r.get("status_label") or r["status"],
        "signed_at": r.get("signed_at"),
        "viewed_at": r.get("viewed_at"),
        "decline_reason": r.get("decline_reason"),
    }


def _envelope_payload(env, full=False):
    signers = [r for r in env.get("recipients", []) if r["role"] == "signer"]
    data = {
        "id": env["id"],
        "uid": env["uid"],
        "title": env["title"],
        "status": env["status"],
        "status_label": env.get("status_label") or env["status"],
        "client_id": env.get("client_id"),
        "client_name": (env.get("client_name") or "").strip(),
        "message": env.get("message") or "",
        "page_count": env.get("page_count"),
        "created_at": env.get("created_at"),
        "sent_at": env.get("sent_at"),
        "completed_at": env.get("completed_at"),
        "expires_at": env.get("expires_at"),
        "last_activity": env.get("last_activity"),
        "signer_total": env.get("signer_total", len(signers)),
        "signer_done": env.get("signer_done", len([r for r in signers if r["status"] == "completed"])),
        "recipients": [_recipient_payload(r) for r in env.get("recipients", [])],
        "document_url": f"/api/mobile/contracts/{env['id']}/document",
        "final_url": f"/api/mobile/contracts/{env['id']}/final" if env["status"] == "completed" else None,
        "web_url": f"/contratos/{env['id']}",
    }
    if full:
        data["has_fields"] = bool(env.get("fields"))
        data["events"] = [
            {"label": e["label"], "event": e["event"], "actor": e.get("actor") or "",
             "detail": e.get("detail") or "", "device": e.get("device") or "",
             "created_at": e["created_at"]}
            for e in env.get("events", [])
        ]
    return data


@mobile_api_bp.route("/contracts")
@mobile_login_required
def contracts_list():
    C, _, _ = _contract_models()
    tab = request.args.get("tab", "all")
    if tab not in dict(C.TABS):
        tab = "all"
    client_id = request.args.get("client_id", type=int)
    envelopes = C.list_envelopes(g.org_id, tab, (request.args.get("q") or "").strip(), client_id=client_id)
    return jsonify({
        "ok": True,
        "tabs": [{"key": k, "label": v} for k, v in C.TABS],
        "stats": C.envelope_stats(g.org_id),
        "contracts": [_envelope_payload(e) for e in envelopes],
    })


@mobile_api_bp.route("/contracts/<int:env_id>")
@mobile_login_required
def contract_detail(env_id):
    C, _, _ = _contract_models()
    env = C.get_envelope(env_id, g.org_id)
    if not env:
        return _json_error("Contract not found", 404)
    return jsonify({"ok": True, "contract": _envelope_payload(env, full=True)})


@mobile_api_bp.route("/contracts", methods=["POST"])
@mobile_login_required
def contract_create():
    """
    multipart/form-data:
      document     PDF (obligatorio)
      title, message, client_id, require_otp (1/0), expires_days, reminder_days
      recipients   JSON: [{name, email, phone, role: signer|cc, routing_order, notify_email, notify_whatsapp}]
      mode         'send'  → agrega una página de firmas al final, coloca los campos y envía (por defecto)
                   'draft' → lo deja como borrador para colocar los campos en el CRM web
    """
    import json as _json
    C, FLOW, _ = _contract_models()
    f = request.files.get("document")
    if not f or not f.filename:
        return _json_error("Sube el documento en PDF.")
    data = f.read(C.MAX_PDF_BYTES + 1)
    title = (request.form.get("title") or f.filename.rsplit(".", 1)[0]).strip()[:200]
    client_id = request.form.get("client_id", type=int)
    if client_id and not get_client(client_id, g.org_id):
        client_id = None
    try:
        recipients = _json.loads(request.form.get("recipients") or "[]")
        if not isinstance(recipients, list):
            raise ValueError
    except ValueError:
        return _json_error("recipients debe ser una lista JSON")
    mode = (request.form.get("mode") or "send").strip()
    signers = [r for r in recipients if (r.get("role") or "signer") == "signer" and (r.get("name") or "").strip()]

    try:
        if mode == "send":
            if not signers:
                raise C.ContractError("Agrega al menos un firmante.")
            from services.signature_page import append_signature_page, MAX_SIGNERS
            if len(signers) > MAX_SIGNERS:
                raise C.ContractError(f"Desde la app se pueden enviar hasta {MAX_SIGNERS} firmantes. "
                                      "Para más, prepáralo en el CRM web.")
            C.inspect_pdf(data)
            data, layout = append_signature_page(data, [s["name"].strip() for s in signers], title)
        env_id = C.create_envelope(
            g.org_id, title, data, f.filename, client_id=client_id,
            message=request.form.get("message", ""), created_by=g.mobile_user["id"],
            created_by_name=_actor_name(),
            require_otp=request.form.get("require_otp", "1") not in ("0", "false", ""),
            expires_days=request.form.get("expires_days", 14, type=int),
            reminder_days=request.form.get("reminder_days", 3, type=int),
        )
        C.set_recipients(env_id, g.org_id, recipients)
    except C.ContractError as exc:
        return _json_error(str(exc))

    if mode != "send":
        env = C.get_envelope(env_id, g.org_id)
        return jsonify({"ok": True, "contract": _envelope_payload(env, full=True),
                        "next": "prepare_on_web"}), 201

    env = C.get_envelope(env_id, g.org_id)
    signer_rows = [r for r in env["recipients"] if r["role"] == "signer"]
    fields = []
    for index, rc in enumerate(signer_rows):
        for spec in layout[index]:
            fields.append({**spec, "recipient_id": rc["id"]})
    C.save_fields(env_id, g.org_id, fields)
    return _send_envelope(env_id, status=201)


@mobile_api_bp.route("/contracts/<int:env_id>/send", methods=["POST"])
@mobile_login_required
def contract_send(env_id):
    """Envía un borrador que ya tiene los campos colocados (por ejemplo, preparado en la web)."""
    C, _, _ = _contract_models()
    if not C.get_envelope(env_id, g.org_id, with_children=False):
        return _json_error("Contract not found", 404)
    return _send_envelope(env_id)


def _send_envelope(env_id, status=200):
    C, FLOW, _ = _contract_models()
    try:
        pairs = C.send_envelope(env_id, g.org_id, actor=_actor_name())
    except C.ContractError as exc:
        return _json_error(str(exc))
    env = C.get_envelope(env_id, g.org_id)
    FLOW.dispatch_invites(env, pairs, env.get("created_by_name") or _actor_name())
    FLOW.client_note(env.get("client_id"), f'Contrato enviado para firma: {env["title"]} (sobre {env["uid"]})')
    env = C.get_envelope(env_id, g.org_id)
    failed = [e for e in env["events"] if e["event"] == "notify_failed"]
    return jsonify({"ok": True, "contract": _envelope_payload(env, full=True),
                    "warning": "Algún aviso no se pudo entregar. Revisa el historial." if failed else None}), status


@mobile_api_bp.route("/contracts/<int:env_id>/resend/<int:rid>", methods=["POST"])
@mobile_login_required
def contract_resend(env_id, rid):
    C, _, N = _contract_models()
    env = C.get_envelope(env_id, g.org_id)
    if not env:
        return _json_error("Contract not found", 404)
    rc = next((r for r in env["recipients"] if r["id"] == rid), None)
    if env["status"] != "sent" or not C.is_turn(rc):
        return _json_error("Ese destinatario no tiene una firma pendiente.")
    token = C.issue_token(rid)
    channels = N.invite(env, rc, token, env.get("created_by_name") or _actor_name(), reminder=True)
    C.touch_reminder(rid)
    if not channels:
        return _json_error("No se pudo entregar el enlace. Revisa el historial.", 502)
    return jsonify({"ok": True, "channels": channels})


@mobile_api_bp.route("/contracts/<int:env_id>/void", methods=["POST"])
@mobile_login_required
def contract_void(env_id):
    C, _, _ = _contract_models()
    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not C.void(env_id, g.org_id, reason, actor=_actor_name()):
        return _json_error("Solo se pueden anular sobres pendientes de firma.")
    return jsonify({"ok": True, "contract": _envelope_payload(C.get_envelope(env_id, g.org_id), full=True)})


@mobile_api_bp.route("/contracts/<int:env_id>", methods=["DELETE"])
@mobile_login_required
def contract_delete(env_id):
    C, _, _ = _contract_models()
    if not C.delete_draft(env_id, g.org_id):
        return _json_error("Solo se pueden eliminar borradores. Para un sobre enviado usa anular.")
    return jsonify({"ok": True})


@mobile_api_bp.route("/contracts/<int:env_id>/document")
@mobile_login_required
def contract_document(env_id):
    from flask import send_file
    C, FLOW, _ = _contract_models()
    env = C.get_envelope(env_id, g.org_id, with_children=False)
    if not env:
        return _json_error("Contract not found", 404)
    return send_file(C.original_path(env), mimetype="application/pdf",
                     download_name=FLOW.safe_filename(env["title"]), max_age=0)


@mobile_api_bp.route("/contracts/<int:env_id>/final")
@mobile_login_required
def contract_final(env_id):
    from flask import send_file
    C, FLOW, _ = _contract_models()
    env = C.get_envelope(env_id, g.org_id, with_children=False)
    if not env or env["status"] != "completed":
        return _json_error("El contrato aún no está completado.", 404)
    C.log_event(env_id, g.org_id, "downloaded", actor=_actor_name(), detail="App móvil",
                ip=request.remote_addr or "", user_agent=request.headers.get("User-Agent", "")[:300])
    return send_file(C.final_path(env), mimetype="application/pdf",
                     download_name=FLOW.safe_filename(env["title"], "_firmado"), max_age=0)
