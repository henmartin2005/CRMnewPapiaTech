# CRM como plantilla: una instancia por cliente

Todos los clientes usan el **mismo código** (este repo). Lo que cambia entre ellos vive
fuera de git:

| Qué | Dónde | En git |
|---|---|---|
| Código | `papia-crm/` | ✔ |
| Valores por defecto | `config_defaults/` | ✔ (no editar por cliente) |
| Presets listos (ej. PapiaTech) | `config_presets/<nombre>/` | ✔ |
| **Marca, servicios, pipeline, prompt del bot** | `instance/` | ✗ |
| **Secretos** (Gmail, WhatsApp, Claude, Stripe…) | `.env` | ✗ |
| **Datos** (clientes, chats, pagos) | `papia_crm.db`, `client_docs/`, `chat_uploads/`, `wa_media/` | ✗ |

## 0. Pasar a la plantilla una instancia que ya existe (ej. datos.papiatech.com)

Sin `instance/`, el CRM arranca con la marca genérica ("Mi Empresa"). **Antes del Reload**:

```bash
cd ~/CRMnewPapiaTech && git pull origin main
cd papia-crm && cp -r config_presets/papiatech instance
source ../.venv/bin/activate && python doctor.py
```

Luego haz Reload. Los datos, el `.env` y la base no cambian.

La plantilla ya no crea el usuario `Testing` / `Testing`. Si existe en una instancia vieja,
bórralo o cámbiale la contraseña en **Configuración del sistema** (lista de usuarios).

## 1. Instalar una instancia nueva (PythonAnywhere)

En una consola Bash de la cuenta del cliente (o de la tuya, en otra carpeta):

```bash
git clone https://github.com/henmartin2005/CRMnewPapiaTech.git crm-<cliente>
cd crm-<cliente>/papia-crm
./setup.sh                 # o: ./setup.sh --preset <nombre> si ya tienes un preset
```

`setup.sh` crea el virtualenv, instala dependencias, crea `instance/` y `.env`
(con `SECRET_KEY`, `VAULT_KEY`, contraseña de admin y secretos de webhook generados),
inicializa la base vacía, corre `doctor.py` e imprime el bloque WSGI exacto.

Después, en la pestaña **Web**:

1. **Add a new web app → Manual configuration** (misma versión de Python).
2. **Virtualenv:** la ruta `.venv` que imprimió el setup.
3. **WSGI file:** reemplaza todo por el bloque que imprimió el setup.
4. **Static files:** `/static/` → `<ruta>/papia-crm/static`
5. **Force HTTPS** activado → **Reload**.

> Dominio propio y llamadas salientes sin restricciones (Twilio, Zernio, Anthropic, Stripe,
> Google) requieren cuenta de pago en PythonAnywhere.

## 2. Personalizar la instancia (`instance/`)

| Archivo | Para qué | ¿Necesita Reload? |
|---|---|---|
| `brand.json` | Nombre comercial y legal, dueño, logo, colores, web, email, WhatsApp, ciudad y zona horaria, orígenes CORS | No* |
| `business.json` | Etapas del pipeline, servicios (`project_types`) y palabras clave del bot, módulos activos, horario de llamadas del bot | **Sí** |
| `business_info.md` | Lo que el bot sabe del negocio (servicios, proceso, formas de pago). Única fuente de verdad del bot | No |
| `agent_prompt.md` | Personalidad y reglas del asistente de WhatsApp | No |

\* La zona horaria y el horario de llamadas se leen al arrancar: tras cambiarlos, haz Reload.

Variables disponibles en `agent_prompt.md` y `business_info.md`: cualquier campo de
`brand.json` como `{{company_name}}`, `{{owner_first_name}}`, `{{city}}`, `{{website}}`,
`{{tz_label}}`. El prompt además recibe `{{now}}`, `{{learned_block}}`,
`{{business_info}}` y `{{client_summary}}`; no las quites.

Notas:

- **Pipeline:** las claves `new_lead`, `contacted`, `proposal_sent`, `active_client` y
  `recurring` las usa el código. Cambia su `label`, `color` y `hex` libremente y agrega
  etapas nuevas, pero no borres ni renombres esas claves. Los nombres también se pueden
  ajustar por organización desde el kanban.
- **Servicios:** `default_project_type` debe existir en `project_types`. Las `keywords`
  sirven para que el bot y el endpoint de leads (`/api/new-lead`) reconozcan el servicio.
- **Módulos:** `modules` define cuáles nacen activos al crear la organización. Después se
  cambian desde `/super` (superadmin).
- **Info del bot:** si guardas texto en el panel del bot (WhatsApp → Bot), ese texto
  tiene prioridad sobre `business_info.md`.
- **Logo:** `logo_white_url` es el logo del menú lateral y del login (fondo oscuro). Si está
  vacío se muestra el nombre corto como texto.

## 3. Conectar servicios del cliente

`python doctor.py` te dice qué falta y te imprime todas las URLs que hay que registrar.

### Gmail + Google Calendar
1. Google Cloud Console → crea o elige un proyecto → habilita **Gmail API** y **Google Calendar API**.
2. **OAuth consent screen:** agrega el Gmail del cliente como *Test user* (o publica la app).
3. **Credentials → OAuth client ID (Web):** redirect URI `<APP_BASE_URL>/emails/oauth2callback`.
4. Pon `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` y `GMAIL_REDIRECT_URI` en `.env`, haz Reload y
   entra a **Emails → Conectar ahora** con el Gmail del cliente.

### WhatsApp
- **Zernio (recomendado):** crea la API key, pon `ZERNIO_API_KEY` en `.env` y luego:
  ```bash
  python zernio_setup.py accounts     # copia el accountId a ZERNIO_WA_ACCOUNT_ID
  python zernio_setup.py webhook      # registra <APP_BASE_URL>/webhook/zernio
  python whatsapp_templates.py        # crea las plantillas con la marca del cliente
  ```
- **Twilio:** `WHATSAPP_PROVIDER=twilio`, credenciales en `.env` y webhook entrante
  `<APP_BASE_URL>/webhook/whatsapp`.

### Claude (asistente de WhatsApp)
`ANTHROPIC_API_KEY` en `.env`. Actívalo en WhatsApp → Bot. Cuando el dueño ya tenga chats
reales, usa **Aprender ahora** en el panel del bot para que copie su estilo.

### Stripe
Llaves en `.env` (o por organización en Configuración → Pagos). Webhook:
`<APP_BASE_URL>/webhook/stripe`, evento `checkout.session.completed`.

### Contratos (firma electrónica)
- Viene activo. Sin configuración extra: usa el Gmail y el WhatsApp de la instancia.
- **Marca:** en `instance/brand.json` → `esign_name` (ej. "Maybis Sign"; vacío = "<short_name> Sign") y,
  opcional, `colors.esign_dark` (encabezado), `colors.esign_cta` (botones) y `colors.esign_link`.
  Si no los pones se usan `sidebar_bg` y `accent`. El logo del encabezado es `logo_white_url` (o `logo_url`).
- `python whatsapp_templates.py` crea también `firma_contrato` y `codigo_firma`.
- **Tarea diaria** (pestaña Tasks): `cd <ruta>/papia-crm && ../.venv/bin/python contracts_cron.py`
  (recordatorios y vencimientos).
- **Sello del PDF:** cada instancia genera su propio certificado en `contract_files/_keys/`
  (no va al repo; inclúyelo en tus backups). Para la marca verde de Adobe: `SIGN_CERT_FILE` /
  `SIGN_KEY_FILE` en `.env` con un certificado AATL de ese cliente.
- Los PDF y la base de cada instancia quedan en su propia carpeta: nada se comparte entre clientes.

### Meta (Messenger / Instagram), opcional
Webhook `<APP_BASE_URL>/webhook/meta` con el `META_VERIFY_TOKEN` del `.env`.

## 4. Actualizar todas las instancias

Las mejoras se hacen una sola vez en `main`. En cada instancia:

```bash
cd ~/crm-<cliente> && git pull origin main
source .venv/bin/activate && pip install -r papia-crm/requirements.txt
python papia-crm/doctor.py
```

Luego haz Reload en la pestaña Web. `instance/`, `.env` y la base no se tocan nunca.

## 5. Crear un preset reutilizable

Si vas a montar varios negocios parecidos (por ejemplo clínicas o barberías), guarda su
configuración como preset:

```bash
mkdir config_presets/clinica && cp instance/*.json instance/*.md config_presets/clinica/
```

Así la siguiente instancia arranca con `./setup.sh --preset clinica`. **Nunca** pongas
secretos en un preset: los presets van al repo.

## 6. Checklist de entrega al cliente

- [ ] `python doctor.py` sin errores
- [ ] Login con el admin generado y contraseña cambiada
- [ ] Logo, nombre y colores correctos en login, menú, PDF de propuestas y emails
- [ ] Gmail conectado (envía un email de prueba)
- [ ] WhatsApp: mensaje entrante y saliente funcionando
- [ ] Bot: **Probar el asistente** en el panel, con horario y zona horaria correctos
- [ ] Stripe: link de pago de prueba en modo test
- [ ] Formulario del sitio web → `/api/new-lead` crea el lead (revisa CORS)
- [ ] Backup de `VAULT_KEY` guardado fuera del servidor
