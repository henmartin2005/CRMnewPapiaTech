"""Formulario de onboarding del cliente (portal): lo que el cliente completa para arrancar
su proyecto. Las respuestas se guardan como JSON por cliente; la plantilla define las
secciones y campos. Los archivos (logo, fotos, antes/después) usan las subidas del portal."""
import json

from database import get_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_onboarding (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        INTEGER NOT NULL DEFAULT 1,
    client_id     INTEGER NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
    template_key  TEXT    NOT NULL DEFAULT 'plan_pro_grooming',
    data          TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'draft',      -- draft | submitted
    progress      INTEGER NOT NULL DEFAULT 0,
    submitted_at  TEXT,
    admin_seen    INTEGER NOT NULL DEFAULT 1,             -- 0 = el cliente envió y aún no se revisa
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

MAX_TEXT = 4000
MAX_ROWS = 40

# ── Plantilla ──────────────────────────────────────────────────────────────
# Tipos de campo: text, textarea, email, tel, url, number, choice (opciones), yesno,
# checklist (casillas), table (filas con columnas), uploads (subida de archivos del portal).


def _t(key, label, ftype='text', **kw):
    d = {'key': key, 'label': label, 'type': ftype}
    d.update(kw)
    return d


TEMPLATES = {
    'plan_pro_grooming': {
        'title': 'Formulario de inicio',
        'intro': ('Complete este formulario dentro de los 7 días siguientes a la firma del contrato. '
                  'El plazo de desarrollo de 30 días comienza cuando recibimos esta información y el pago inicial. '
                  'Sus respuestas se guardan solas mientras escribe; puede salir y volver cuando quiera.'),
        'sections': [
            {'key': 'legal', 'title': 'Datos legales y del negocio', 'icon': 'bi-building',
             'lead': 'Van en el contrato y en el registro de mensajes ante Meta y las operadoras. Deben coincidir con sus documentos oficiales.',
             'fields': [
                 _t('legal_name', 'Nombre legal exacto de la empresa (como aparece en Sunbiz)'),
                 _t('entity_type', 'Tipo de empresa', 'choice', options=['LLC', 'Corporation', 'Negocio individual', 'Otro']),
                 _t('ein', 'EIN (número de identificación fiscal federal)', hint='Solo lo ve Papia Tech. Es necesario para registrar los SMS.'),
                 _t('legal_address', 'Dirección legal del negocio'),
                 _t('rep_name', 'Nombre del representante que firma el contrato'),
                 _t('rep_title', 'Cargo del representante'),
                 _t('rep_email', 'Correo del representante', 'email'),
                 _t('rep_phone', 'Teléfono del representante', 'tel'),
                 _t('guarantor', 'Nombre del garante personal (Cláusula 14)'),
                 _t('year_started', 'Año en que comenzó el negocio', 'number'),
                 _t('licenses', '¿Tiene licencias o certificaciones de grooming? ¿Cuáles?', 'textarea'),
             ]},
            {'key': 'brand', 'title': 'Nombre, marca y logo', 'icon': 'bi-palette',
             'fields': [
                 _t('site_name', 'Nombre comercial que quiere mostrar en la página web', hint='Ej.: Lucas Pet Grooming o Lucas Mobile Grooming'),
                 _t('slogan', 'Eslogan o frase del negocio'),
                 _t('colors', 'Colores de su marca', hint='Códigos HEX si los tiene (ej.: #1E88E5) o describa los colores'),
                 _t('font', 'Tipografía de su marca, si tiene una'),
                 _t('brand_manual', '¿Tiene manual de marca?', 'yesno'),
                 _t('ref_sites', 'Páginas web que le gustan como referencia (enlaces)', 'textarea'),
                 _t('style', 'Estilo que busca', 'choice', options=['Divertido', 'Elegante', 'Moderno', 'Familiar', 'Otro']),
                 _t('site_langs', 'Idiomas de la página', 'choice', options=['Español', 'Inglés', 'Ambos']),
                 _t('logo_files', 'Archivos del logo', 'uploads', category='logo',
                    hint='Envíe el logo en PDF (vector), AI o EPS, y también en PNG con fondo transparente. Si tiene versión en blanco o cuadrada, súbala también.'),
             ]},
            {'key': 'content', 'title': 'Contenido de la página: textos y fotos', 'icon': 'bi-images',
             'fields': [
                 _t('story', 'Historia del negocio: cómo empezó y por qué (3 a 5 líneas)', 'textarea'),
                 _t('differentiator', '¿Qué lo diferencia de otros groomers?', 'textarea', hint='Ej.: sin jaulas, productos naturales, mismo groomer siempre'),
                 _t('experience', 'Experiencia del equipo (años, mascotas atendidas)'),
                 _t('show_team', '¿Quiere mostrar al equipo con nombre y foto?', 'yesno'),
                 _t('reviews', 'Reseñas de clientes para publicar, o enlace a sus reseñas de Google', 'textarea'),
                 _t('faqs', 'Preguntas frecuentes de sus clientes, con su respuesta', 'textarea'),
                 _t('hero_message', 'Mensaje principal para la portada (lo primero que se lee)'),
                 _t('photos', 'Fotos del negocio', 'uploads', category='imagen',
                    hint='Vans por fuera y por dentro, el equipo trabajando y mascotas después del servicio (mínimo 10). En tamaño original, no capturas de pantalla.'),
                 _t('before_after', 'Fotos de antes y después', 'uploads', category='antes_despues',
                    hint='Mínimo 5 pares de la misma mascota, desde el mismo ángulo. Póngales nombre tipo "max-antes" y "max-despues".'),
                 _t('photo_rights', 'Confirmo que las fotos son mías o que tengo permiso para usarlas, incluidas las de mascotas de mis clientes', 'checklist',
                    items=['Confirmo']),
             ]},
            {'key': 'services', 'title': 'Servicios, precios y productos', 'icon': 'bi-scissors',
             'lead': 'La duración es obligatoria: con ella el sistema calcula los horarios disponibles de cada van.',
             'fields': [
                 _t('services', 'Servicios principales', 'table', min_rows=4,
                    columns=[{'key': 'name', 'label': 'Servicio', 'w': 3},
                             {'key': 'size', 'label': 'Tamaño', 'type': 'choice', 'options': ['S', 'M', 'L', 'XL', 'Todos']},
                             {'key': 'price', 'label': 'Precio (USD)', 'type': 'number'},
                             {'key': 'minutes', 'label': 'Duración (min)', 'type': 'number'},
                             {'key': 'includes', 'label': 'Qué incluye', 'w': 3}]),
                 _t('addons', 'Adicionales', 'table', min_rows=3,
                    hint='Deshedding, mascarillas, champús especiales, corte de uñas, limpieza dental, etc.',
                    columns=[{'key': 'name', 'label': 'Adicional', 'w': 3},
                             {'key': 'price', 'label': 'Precio (USD)', 'type': 'number'},
                             {'key': 'minutes', 'label': 'Tiempo extra (min)', 'type': 'number'}]),
                 _t('size_def', '¿Cómo define cada tamaño? (peso en libras de S, M, L, XL)', 'textarea'),
                 _t('breed_pricing', '¿El precio cambia por raza o tipo de pelaje? ¿Cómo?', 'textarea'),
                 _t('extra_charges', '¿Cobra extra por nudos, pulgas o mal comportamiento? ¿Cuánto?', 'textarea'),
                 _t('cats', '¿Atiende gatos?', 'yesno'),
                 _t('not_accepted', '¿Hay razas o casos que no acepta?', 'textarea'),
                 _t('products', 'Productos que quiere mostrar en la página (solo informativo, no tienda en línea)', 'textarea'),
             ]},
            {'key': 'ops', 'title': 'Vans, groomers, zonas y horarios', 'icon': 'bi-truck',
             'fields': [
                 _t('vans', 'Vans y groomers (una fila por van)', 'table', min_rows=2,
                    hint='Cada groomer recibirá su propio usuario para entrar desde su teléfono.',
                    columns=[{'key': 'van', 'label': 'Van'},
                             {'key': 'groomer', 'label': 'Groomer asignado', 'w': 2},
                             {'key': 'phone', 'label': 'Teléfono', 'type': 'tel'},
                             {'key': 'email', 'label': 'Correo', 'type': 'email', 'w': 2},
                             {'key': 'zones', 'label': 'Zona(s)'},
                             {'key': 'days', 'label': 'Días'},
                             {'key': 'hours', 'label': 'Horario'}]),
                 _t('zones', 'Zonas y códigos postales', 'table', min_rows=3,
                    default_rows=[{'zone': 'Naples'}, {'zone': 'Cape Coral'}, {'zone': 'Miami'}],
                    columns=[{'key': 'zone', 'label': 'Zona'},
                             {'key': 'zips', 'label': 'Códigos postales', 'w': 3},
                             {'key': 'days', 'label': 'Días habilitados', 'w': 2}]),
                 _t('max_per_day', 'Máximo de citas por van al día', 'number'),
                 _t('travel_minutes', 'Tiempo de traslado entre citas (minutos)', 'number'),
                 _t('start_points', 'Punto de salida de cada van (dirección o zona)', 'textarea'),
                 _t('distance_fee', '¿Cobra extra por distancia o fuera de zona? ¿Cuánto?'),
                 _t('holidays', 'Días feriados o cerrados del año', 'textarea'),
                 _t('zone_admin', '¿Quién abre y cierra las agendas por zona?', hint='Ej.: Miami cerrado hasta nuevo aviso'),
             ]},
            {'key': 'booking', 'title': 'Reservas, depósito y políticas', 'icon': 'bi-calendar-check',
             'fields': [
                 _t('deposit', 'Monto del depósito', hint='Cantidad fija en USD o porcentaje del servicio'),
                 _t('deposit_discount', '¿El depósito se descuenta del total del servicio?', 'yesno'),
                 _t('max_advance', 'Con cuánta anticipación se puede reservar', hint='Ej.: hasta 30 días'),
                 _t('min_advance', 'Anticipación mínima para reservar', hint='Ej.: 24 horas antes'),
                 _t('cancel_policy', 'Política de cancelación (plazo y si se devuelve el depósito)', 'textarea'),
                 _t('noshow_policy', 'Política de no-show (cliente ausente)', 'textarea'),
                 _t('late_policy', 'Política de llegada tarde o reprogramación', 'textarea'),
                 _t('unpaid_deposit', '¿Qué pasa si no se paga el depósito?', 'textarea', hint='La cita queda como «Depósito pendiente»'),
                 _t('booking_fields', 'Datos que debe dar el cliente al reservar, además de nombre, teléfono y dirección', 'textarea'),
                 _t('pet_fields', 'Datos de la mascota que quiere guardar', 'textarea', hint='Raza, edad, peso, temperamento, vacunas, etc.'),
                 _t('vaccines', '¿Pide comprobante de vacunas?', 'yesno'),
             ]},
            {'key': 'payments', 'title': 'Pagos y Stripe', 'icon': 'bi-credit-card',
             'lead': 'El dinero de los depósitos va directo a su cuenta de Stripe; Papia Tech nunca recibe esos fondos.',
             'fields': [
                 _t('has_stripe', '¿Ya tiene cuenta de Stripe?', 'yesno'),
                 _t('stripe_email', 'Correo con el que está registrada la cuenta de Stripe', 'email'),
                 _t('stripe_verified', '¿Ya está verificada con su cuenta bancaria?', 'yesno'),
                 _t('other_methods', 'Otros métodos de pago que acepta', hint='Zelle, efectivo, tarjeta en la van, transferencia'),
                 _t('tips', '¿Acepta propinas? ¿Cómo las cobra?'),
                 _t('tips_panel', '¿Quiere ver las propinas por groomer en el panel?', 'yesno'),
                 _t('refunds_by', '¿Quién del equipo puede hacer reembolsos?'),
                 _t('stripe_tasks', 'Pasos en Stripe', 'checklist',
                    items=['Creé o verifiqué la cuenta de Stripe a nombre del negocio',
                           'Invité a henrry@papiatech.com como usuario «Developer» en Stripe']),
             ]},
            {'key': 'email', 'title': 'Correo electrónico', 'icon': 'bi-envelope',
             'fields': [
                 _t('contact_email', 'Correo que aparecerá en la página web como contacto', 'email'),
                 _t('sender_email', 'Correo desde el que salen los avisos a sus clientes', 'email', hint='Ej.: citas@sudominio.com o lucaspetgrooming@gmail.com'),
                 _t('wants_pro_email', '¿Quiere un correo profesional con su dominio?', 'yesno', hint='Ej.: info@lucaspetgrooming.com. Su costo lo cubre el cliente.'),
                 _t('email_provider', 'Si ya tiene correo con dominio: ¿en qué proveedor está?', 'choice', options=['Google Workspace', 'Outlook / Microsoft 365', 'Otro', 'No tengo']),
                 _t('booking_notify_emails', 'Correo(s) que reciben el aviso de cada reserva nueva'),
                 _t('owner_email', 'Correo del dueño para el acceso de administrador al panel', 'email'),
                 _t('admin_emails', 'Correo(s) de otros administradores, si los hay'),
             ]},
            {'key': 'whatsapp', 'title': 'WhatsApp y SMS', 'icon': 'bi-whatsapp',
             'lead': 'El asistente y los SMS usan un número registrado a nombre de su negocio ante Meta y las operadoras. La aprobación tarda varios días: complete esta sección primero.',
             'fields': [
                 _t('wa_number_choice', '¿Qué número quiere conectar al asistente de WhatsApp?', 'choice',
                    options=['Mi número actual (786-916-7121)', 'Otro número que ya tengo', 'Un número nuevo que configure Papia Tech']),
                 _t('wa_number_other', 'Si eligió «Otro número», escríbalo aquí', 'tel'),
                 _t('wa_current_app', 'Si es su número actual: ¿lo usa hoy en la app WhatsApp Business? ¿Quién lo contesta?'),
                 _t('sms_same_number', '¿El mismo número enviará los SMS de respaldo?', 'yesno'),
                 _t('wa_display_name', 'Nombre que verán sus clientes en WhatsApp'),
                 _t('wa_description', 'Descripción corta del negocio para el perfil de WhatsApp', 'textarea'),
                 _t('fb_page', 'Página de Facebook del negocio (enlace)', 'url'),
                 _t('has_meta_business', '¿Tiene cuenta de Meta Business (business.facebook.com)?', 'yesno'),
                 _t('client_list', '¿Tiene lista de clientes para importar? ¿Cuántos?'),
                 _t('client_consent', '¿Esos clientes aceptaron recibir mensajes de su negocio? ¿Cómo?', 'textarea',
                    hint='Por ley (TCPA) solo se puede escribir a quien dio su consentimiento.'),
                 _t('chat_agents', 'Persona(s) que atenderán los chats cuando el asistente los pase a una persona'),
                 _t('chat_hours', 'Horario de atención humana en el chat'),
                 _t('meta_tasks', 'Pasos en Meta', 'checklist',
                    items=['Agregué a henrry@papiatech.com como administrador o socio en mi Meta Business',
                           'Tengo a mano los documentos para la verificación de Meta (Articles of Organization, EIN, factura con la dirección)']),
             ]},
            {'key': 'ai', 'title': 'Asistente de inteligencia artificial', 'icon': 'bi-robot',
             'lead': 'El asistente responde en español e inglés con la información que usted dé aquí.',
             'fields': [
                 _t('ai_name', 'Nombre del asistente, si quiere darle uno', hint='Ej.: «Luki»'),
                 _t('ai_tone', 'Tono', 'choice', options=['Formal', 'Cercano', 'Divertido']),
                 _t('ai_greeting', 'Saludo de bienvenida que le gustaría', 'textarea'),
                 _t('ai_forbidden', 'Temas que el asistente NO debe responder y debe pasar a una persona', 'textarea'),
                 _t('ai_prices', '¿Puede dar precios exactos o solo estimados?', 'choice', options=['Precios exactos', 'Solo estimados']),
                 _t('ai_handoff', 'Casos que siempre deben pasar a una persona', 'textarea', hint='Ej.: mascota agresiva, enferma o mayor'),
                 _t('ai_after_hours', 'Respuesta fuera de horario o cuando no hay disponibilidad', 'textarea'),
                 _t('ai_more_faqs', 'Otras preguntas frecuentes por WhatsApp', 'textarea'),
             ]},
            {'key': 'domain', 'title': 'Dominio, Google y redes sociales', 'icon': 'bi-globe2',
             'lead': 'El dominio se registra a nombre de su negocio; su costo anual lo cubre el cliente.',
             'fields': [
                 _t('domain', '¿Tiene dominio? ¿Cuál?', hint='Ej.: lucaspetgrooming.com'),
                 _t('registrar', '¿Dónde lo compró?', 'choice', options=['GoDaddy', 'Namecheap', 'Google / Squarespace', 'Wix', 'Otro', 'No tengo dominio']),
                 _t('current_site', 'Página web actual (enlace)', 'url'),
                 _t('domain_options', 'Si no tiene dominio: 3 opciones de nombre que le gustarían', 'textarea'),
                 _t('gbp', 'Perfil de Google Business / Google Maps (enlace)', 'url'),
                 _t('instagram', 'Instagram'),
                 _t('facebook', 'Facebook'),
                 _t('tiktok', 'TikTok'),
                 _t('other_social', 'Otras redes o directorios (Yelp, Nextdoor)'),
                 _t('domain_tasks', 'Accesos', 'checklist',
                    items=['Di acceso al dominio a henrry@papiatech.com (usuario delegado o configuración DNS)',
                           'Agregué a henrry@papiatech.com como administrador de mi perfil de Google Business']),
             ]},
            {'key': 'legal_texts', 'title': 'Textos legales', 'icon': 'bi-file-earmark-text',
             'lead': 'Según el Anexo B, usted aporta y aprueba los textos legales con su propio asesor. El sistema los muestra y guarda la firma del cliente.',
             'fields': [
                 _t('legal_have', 'Textos que ya tengo', 'checklist',
                    items=['Consentimiento y exención de la mascota (edad, enfermedades, lesiones, comportamiento)',
                           'Política de cancelación y no-show', 'Política de privacidad de la página web',
                           'Términos y condiciones del servicio',
                           'Texto de aceptación para recibir SMS y WhatsApp']),
                 _t('legal_template', '¿Quiere que le enviemos un modelo base para que lo revise su abogado?', 'yesno'),
                 _t('legal_files', 'Suba aquí los textos legales que ya tiene', 'uploads', category='documento'),
             ]},
            {'key': 'confirm', 'title': 'Confirmación', 'icon': 'bi-check2-square',
             'fields': [
                 _t('completed_by', 'Completado por'),
                 _t('notes', 'Comentarios o dudas para Papia Tech', 'textarea'),
             ]},
        ],
    },
}
DEFAULT_TEMPLATE = 'plan_pro_grooming'


def get_template(key=None):
    return TEMPLATES.get(key or DEFAULT_TEMPLATE) or TEMPLATES[DEFAULT_TEMPLATE]


def _fields(tpl):
    for s in tpl['sections']:
        for f in s['fields']:
            yield s, f


# ── Persistencia ───────────────────────────────────────────────────────────
def _db():
    db = get_db()
    db.execute(_SCHEMA)
    return db


def ensure_schema():
    _db().close()


def load(client_id, org_id):
    """Devuelve el registro (dict) con 'data' ya decodificado; si no existe, uno vacío."""
    db = _db()
    try:
        row = db.execute("SELECT * FROM client_onboarding WHERE client_id = ? AND org_id = ?",
                         (client_id, org_id)).fetchone()
    finally:
        db.close()
    if not row:
        return {'client_id': client_id, 'org_id': org_id, 'template_key': DEFAULT_TEMPLATE,
                'data': {}, 'status': 'draft', 'progress': 0, 'submitted_at': None,
                'admin_seen': 1, 'updated_at': None, 'exists': False}
    rec = dict(row)
    try:
        rec['data'] = json.loads(rec.get('data') or '{}')
        if not isinstance(rec['data'], dict):
            rec['data'] = {}
    except ValueError:
        rec['data'] = {}
    rec['exists'] = True
    return rec


def _clean_text(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'si' if v else 'no'
    return str(v).strip()[:MAX_TEXT]


def sanitize(tpl, incoming):
    """Solo acepta las claves y formas que define la plantilla."""
    out = {}
    if not isinstance(incoming, dict):
        return out
    for _, f in _fields(tpl):
        k, t = f['key'], f['type']
        if k not in incoming or t == 'uploads':
            continue
        v = incoming[k]
        if t == 'table':
            cols = [c['key'] for c in f['columns']]
            rows = []
            for r in (v if isinstance(v, list) else [])[:MAX_ROWS]:
                if isinstance(r, dict):
                    rows.append({c: _clean_text(r.get(c))[:500] for c in cols})
            out[k] = rows
        elif t == 'checklist':
            allowed = set(f['items'])
            out[k] = [i for i in (v if isinstance(v, list) else []) if i in allowed]
        elif t == 'yesno':
            out[k] = v if v in ('si', 'no') else ''
        elif t == 'choice':
            out[k] = v if v in f['options'] else ''
        else:
            out[k] = _clean_text(v)
    return out


def _answered(f, data, upload_counts):
    t, v = f['type'], data.get(f['key'])
    if t == 'uploads':
        return upload_counts.get(f['category'], 0) > 0
    if t == 'table':
        # En tablas con filas precargadas (ej. zonas), la primera columna no cuenta como respuesta.
        cols = [c['key'] for c in f['columns']]
        if f.get('default_rows'):
            cols = cols[1:]
        return any(any((r.get(c) or '').strip() for c in cols) for r in (v or []))
    if t == 'checklist':
        return bool(v)
    return bool((v or '').strip()) if isinstance(v, str) else bool(v)


def progress(tpl, data, upload_counts):
    """(pct global, {section_key: (respondidos, total)})."""
    per, done_all, total_all = {}, 0, 0
    for s in tpl['sections']:
        tot = len(s['fields'])
        done = sum(1 for f in s['fields'] if _answered(f, data, upload_counts))
        per[s['key']] = (done, tot)
        done_all += done
        total_all += tot
    pct = int(round(done_all * 100.0 / total_all)) if total_all else 0
    return pct, per


def upload_counts(client_id, org_id):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT COALESCE(category, 'documento'), COUNT(*) FROM client_documents "
            "WHERE client_id = ? AND org_id = ? AND uploaded_by = 'client' GROUP BY 1",
            (client_id, org_id)).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        db.close()


def save(client_id, org_id, incoming, submit=False, by_client=True):
    """Fusiona las respuestas nuevas con las guardadas. Devuelve el registro actualizado."""
    rec = load(client_id, org_id)
    tpl = get_template(rec['template_key'])
    data = dict(rec['data'])
    data.update(sanitize(tpl, incoming))
    pct, _ = progress(tpl, data, upload_counts(client_id, org_id))
    status = 'submitted' if (submit or rec['status'] == 'submitted') else 'draft'
    db = _db()
    try:
        if rec['exists']:
            db.execute(
                "UPDATE client_onboarding SET data = ?, progress = ?, status = ?, "
                "submitted_at = CASE WHEN ? THEN datetime('now','localtime') ELSE submitted_at END, "
                "admin_seen = CASE WHEN ? THEN 0 ELSE admin_seen END, "
                "updated_at = datetime('now','localtime') WHERE client_id = ? AND org_id = ?",
                (json.dumps(data, ensure_ascii=False), pct, status, 1 if submit else 0,
                 1 if (submit and by_client) else 0, client_id, org_id))
        else:
            db.execute(
                "INSERT INTO client_onboarding(org_id, client_id, template_key, data, progress, status, "
                "submitted_at, admin_seen) VALUES (?, ?, ?, ?, ?, ?, "
                "CASE WHEN ? THEN datetime('now','localtime') END, ?)",
                (org_id, client_id, rec['template_key'], json.dumps(data, ensure_ascii=False), pct,
                 status, 1 if submit else 0, 0 if (submit and by_client) else 1))
        db.commit()
    finally:
        db.close()
    return load(client_id, org_id)


def mark_seen(client_id, org_id):
    db = _db()
    try:
        db.execute("UPDATE client_onboarding SET admin_seen = 1 WHERE client_id = ? AND org_id = ?",
                   (client_id, org_id))
        db.commit()
    finally:
        db.close()


def summary(client_id, org_id):
    """Para el perfil y la tarjeta del portal: estado + % (recalculado con las subidas actuales)."""
    rec = load(client_id, org_id)
    tpl = get_template(rec['template_key'])
    pct, per = progress(tpl, rec['data'], upload_counts(client_id, org_id))
    sections_done = sum(1 for d, t in per.values() if t and d == t)
    return {'status': rec['status'], 'pct': pct, 'exists': rec['exists'],
            'submitted_at': rec.get('submitted_at'), 'updated_at': rec.get('updated_at'),
            'admin_seen': rec.get('admin_seen', 1), 'sections_done': sections_done,
            'sections_total': len(per)}
