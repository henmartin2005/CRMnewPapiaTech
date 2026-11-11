# Instagram Studio — despliegue

Pestaña nueva en el sidebar (**Instagram Studio**, solo admin) en `/instagram-studio`, separada del Inbox de Instagram:

| Pantalla | Qué hace |
|---|---|
| Analítica | Seguidores y crecimiento (snapshot diario), alcance, engagement, formatos, mejores horarios, top posts |
| Comentarios | Comentarios que llegan por webhook, qué hizo el agente, respuesta manual, revisar borradores, agente por publicación/anuncio |
| Agente IA | Agentes con Claude (comentarios y DMs), reglas, retraso, horario, prueba sin publicar, registro de ejecuciones |
| Estrategia semanal | Reporte de Claude cada lunes, plan de contenido aprobable, envío por email |

## Archivos

Nuevos: `migrate_ig_studio.py`, `services/ig_studio.py`, `routes/ig_studio.py`, `templates/ig_studio/*`,
`static/css/ig-studio.css`, `static/js/ig-studio.js`, `ig_studio_jobs.py`, `tests/test_ig_studio.py`.

Tocados (cambios pequeños):
- `app.py`: import + `migrate_ig_studio()` + `app.register_blueprint(ig_studio_bp)`.
- `templates/base.html`: entrada **Instagram Studio** debajo de Instagram.
- `routes/meta_webhook.py`: al final de `receive()`, antes del `return`:
  ```python
  try:
      from services import ig_studio
      ig_studio.handle_webhook(data)
  except Exception:
      current_app.logger.exception('Instagram Studio: fallo al procesar el webhook')
  ```

> El servidor tiene cambios que no están en `main` (Pizarra, Contratos, Inbox con Instagram Login).
> Si el merge choca, esos 3 bloques son lo único que hay que copiar a mano; el resto son archivos nuevos.

## 1. Meta (app "Papia Tech CRM")

1. Producto **Instagram → API setup with Instagram login**. Copia *Instagram app ID* y *Instagram app secret*.
2. **Business login settings → OAuth redirect URIs**: `https://datos.papiatech.com/instagram-studio/oauth/callback`
3. Permisos: `instagram_business_basic`, `instagram_business_manage_messages`,
   `instagram_business_manage_comments`, `instagram_business_manage_insights`.
   Con las cuentas propias como testers de la app basta el acceso estándar.
4. **Webhooks** (el mismo callback que ya usa el Inbox: `https://datos.papiatech.com/webhook/meta`):
   suscribe el campo **comments** además de **messages**. Al conectar cada cuenta, el CRM llama a
   `me/subscribed_apps` con `comments,messages`.

## 2. `.env` del servidor

```
IG_APP_ID=...
IG_APP_SECRET=...
IG_REDIRECT_URI=https://datos.papiatech.com/instagram-studio/oauth/callback
IG_STUDIO_MODEL=claude-haiku-5-5          # comentarios
IG_STUDIO_STRATEGY_MODEL=claude-sonnet-5-5 # reporte semanal
IG_STUDIO_REPORT_EMAIL=henrry@papiatech.com
```
`ANTHROPIC_API_KEY` ya existe (bot de WhatsApp). El consumo aparece en el panel de uso con fuente `instagram_studio`.
La firma del webhook se valida con el secret que ya usa `meta_webhook.py`; si el Inbox usa el secret
de la app de Instagram, es el mismo.

## 3. Desplegar en PythonAnywhere

```bash
cd ~/CRMnewPapiaTech
git fetch origin && git merge origin/feature/instagram-studio   # o cherry-pick
source .venv/bin/activate
cd papia-crm && python migrate_ig_studio.py
```
Reload de la web app. Entra a **Instagram Studio → Conectar cuenta**, una vez con @henmartin14 y otra con @papiatech.

## 4. Tareas programadas (PythonAnywhere → Tasks)

| Hora (UTC) | Comando |
|---|---|
| 10:05 diario | `cd /home/henmartin2005/CRMnewPapiaTech/papia-crm && /home/henmartin2005/CRMnewPapiaTech/.venv/bin/python ig_studio_jobs.py daily` |
| 13:00 diario | `cd /home/henmartin2005/CRMnewPapiaTech/papia-crm && /home/henmartin2005/CRMnewPapiaTech/.venv/bin/python ig_studio_jobs.py weekly` |

`weekly` solo actúa los lunes (8:00 AM de Miami en horario de invierno). Si tienes tareas por hora disponibles,
agrega `ig_studio_jobs.py pending` cada hora como red de seguridad para comentarios pendientes.

## Cómo decide el agente de comentarios

- Positivo → responde. Pide info/precio → responde en público + DM privado (una vez por comentario) y crea lead.
- Crítica fuerte o piden hablar con Henrry → borrador en **Revisar**, no publica nada.
- Spam o fuera de tema → ignora. Nunca da precios ni inventa datos.
- Ignora sus propias respuestas (evita bucles). Agente apagado o publicación "Sin agente" → queda para ti.

## DMs

El Inbox de Instagram ya tiene su Asistente IA en el servidor. Los agentes de DMs de esta pestaña guardan
el prompt por cuenta; para que ese asistente lo use, en su generación de respuesta:
```python
from services import ig_studio
prompt = ig_studio.dm_prompt_for(org_id, ig_user_id) or PROMPT_ACTUAL
```

## Tests

```bash
cd papia-crm && python -m unittest tests.test_ig_studio -v
```
