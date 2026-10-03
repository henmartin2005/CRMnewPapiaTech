"""
whatsapp_templates.py — crea las plantillas de WhatsApp del negocio en Zernio/Meta
(nombre y dueño salen de instance/brand.json).

Uso (con el venv activo, desde papia-crm):
    python whatsapp_templates.py          # crea las que falten (es + en)
    python whatsapp_templates.py status   # lista estado de aprobación

Meta revisa cada plantilla (hasta 24 h). Crear es gratis; se cobra al entregar.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

from services import zernio  # noqa: E402
import instance_config as cfg  # noqa: E402

BRAND = cfg.get('company_name')
OWNER = cfg.get('owner_first_name')
EXAMPLE_BASE = cfg.base_url() or 'https://crm.example.com'

# name: (category, {lang: (body, example_values, [quick_reply_buttons])})
TEMPLATES = {
    'seguimiento_propuesta': ('UTILITY', {
        'es': ("Hola {{1}}, tu propuesta para *{{2}}* ya está lista. Puedes revisarla aquí: {{3}}\n\n"
               "Si tienes alguna pregunta, responde a este mensaje.",
               ['Ana', 'App de reservas', f'{EXAMPLE_BASE}/p/1234'], []),
        'en': ("Hi {{1}}, your proposal for *{{2}}* is ready. You can review it here: {{3}}\n\n"
               "If you have any questions, just reply to this message.",
               ['Ana', 'Booking app', f'{EXAMPLE_BASE}/p/1234'], []),
    }),
    'recordatorio_reunion': ('UTILITY', {
        'es': (f"Hola {{{{1}}}}, te recordamos tu reunión con {BRAND} el {{{{2}}}} a las {{{{3}}}}. ¿Nos confirmas tu asistencia?",
               ['Ana', 'martes 6 de octubre', '3:00 p. m.'], ['Confirmar', 'Reprogramar']),
        'en': (f"Hi {{{{1}}}}, this is a reminder of your meeting with {BRAND} on {{{{2}}}} at {{{{3}}}}. Can you confirm you'll attend?",
               ['Ana', 'Tuesday, October 6', '3:00 PM'], ['Confirm', 'Reschedule']),
    }),
    'recordatorio_pago': ('UTILITY', {
        'es': ("Hola {{1}}, tienes un saldo pendiente de {{2}} correspondiente a *{{3}}*. "
               "Puedes pagar de forma segura aquí: {{4}}\n\n"
               "Si ya realizaste el pago, ignora este mensaje. ¡Gracias!",
               ['Ana', '$450.00', 'Sitio web corporativo', f'{EXAMPLE_BASE}/pay/1234'], []),
        'en': ("Hi {{1}}, you have an outstanding balance of {{2}} for *{{3}}*. "
               "You can pay securely here: {{4}}\n\n"
               "If you have already paid, please disregard this message. Thank you!",
               ['Ana', '$450.00', 'Company website', f'{EXAMPLE_BASE}/pay/1234'], []),
    }),
    'avance_proyecto': ('UTILITY', {
        'es': ("Hola {{1}}, hay una actualización en tu proyecto *{{2}}*: {{3}}\n\n"
               "Si necesitas algo, responde a este mensaje.",
               ['Ana', 'App de reservas', 'ya terminamos el diseño de las pantallas principales'], []),
        'en': ("Hi {{1}}, there's an update on your project *{{2}}*: {{3}}\n\n"
               "If you need anything, just reply to this message.",
               ['Ana', 'Booking app', 'we finished the design of the main screens'], []),
    }),
    'primer_contacto': ('MARKETING', {
        'es': (f"Hola {{{{1}}}}, soy {OWNER} de {BRAND}. Te escribo por tu interés en {{{{2}}}}. "
               "¿Tienes unos minutos para conversar?",
               ['Ana', 'una página web para tu negocio'], ['Sí, hablemos', 'Ahora no']),
        'en': (f"Hi {{{{1}}}}, this is {OWNER} from {BRAND}. I'm reaching out about your interest in {{{{2}}}}. "
               "Do you have a few minutes to talk?",
               ['Ana', 'a website for your business'], ["Yes, let's talk", 'Not now']),
    }),
    'retomar_conversacion': ('MARKETING', {
        'es': ("Hola {{1}}, quería retomar tu consulta sobre {{2}}. ¿Sigues interesado? Estamos para ayudarte.",
               ['Ana', 'la app de reservas'], ['Sí, me interesa', 'Ya no, gracias']),
        'en': ("Hi {{1}}, I wanted to follow up on your inquiry about {{2}}. Are you still interested? We're here to help.",
               ['Ana', 'the booking app'], ["Yes, I'm interested", 'No, thanks']),
    }),
}


def existing():
    data = zernio.list_templates()
    items = data.get('templates') or data.get('data') or []
    return {(t.get('name'), t.get('language')): t.get('status') for t in items}


def build(body, examples, buttons):
    comps = [{'type': 'body', 'text': body, 'example': {'body_text': [examples]}}]
    if buttons:
        comps.append({'type': 'buttons',
                      'buttons': [{'type': 'quick_reply', 'text': b} for b in buttons]})
    return comps


def create_all():
    have = existing()
    for name, (category, langs) in TEMPLATES.items():
        for lang, (body, ex, btns) in langs.items():
            if (name, lang) in have:
                print(f'= {name:24} {lang}  ya existe ({have[(name, lang)]})')
                continue
            try:
                res = zernio._request('POST', '/v1/whatsapp/templates', headers=zernio._headers(), json={
                    'accountId': zernio.account_id(),
                    'name': name,
                    'category': category,
                    'language': lang,
                    'components': build(body, ex, btns),
                })
                t = res.get('template') or res.get('data') or res
                print(f'+ {name:24} {lang}  {category:9} {t.get("status", "enviada")}')
            except zernio.ZernioError as exc:
                print(f'! {name:24} {lang}  ERROR {exc.status}: {exc}')


def status():
    for (name, lang), st in sorted(existing().items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        print(f'- {name:24} {lang:6} {st}')


if __name__ == '__main__':
    if not zernio.is_configured():
        sys.exit('Falta ZERNIO_API_KEY o ZERNIO_WA_ACCOUNT_ID en .env')
    status() if sys.argv[1:] == ['status'] else create_all()
