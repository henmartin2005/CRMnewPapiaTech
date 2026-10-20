# Demos · control de acceso a demos para prospectos

Módulo nuevo en **Operación → Demos** (solo admin y superadmin). Reemplaza el sistema de
acceso que vivía en Supabase dentro de Groomingmiami: ahora el CRM guarda los códigos, valida
las visitas y registra la actividad de todos los demos, de cualquier nicho.

## Qué hace

- **Catálogo de demos**: uno por nicho (grooming, barbería, uñas…) con su enlace publicado,
  color por defecto y plantilla del mensaje de WhatsApp. Grooming viene sembrado
  (`https://groomingmiami.lovable.app`).
- **Códigos por prospecto** con formato `NEGOCIO-XXXX` (ej. `INNA-7K2P`): 3 visitas y 7 días por
  defecto, ilimitado, amarre al primer dispositivo, extender, sumar visitas, reiniciar, desactivar.
- **Registro**: entradas, secciones vistas, salidas e intentos bloqueados, con dispositivo
  (`Safari · iOS`) y tiempo en cada sección.
- **Avisos** en la primera visita y en los bloqueos (máximo uno por hora y código):
  1. Nota en el perfil del lead y tarea "Llamar a {negocio}: abrió el demo" (sale en Tasks).
  2. `POST` JSON a `DEMO_NOTIFY_WEBHOOK` (n8n → WhatsApp/SMS para ti).
  3. Correo a `DEMO_NOTIFY_EMAIL` por el Gmail conectado.
- **Badge** en el menú con las entradas nuevas desde tu última visita a Demos, y tarjeta
  **Demo** en el perfil del cliente.
- Al crear el código puedes ligarlo a un lead existente o crear el lead automáticamente
  (`source = 'demo'`).

## Instalar en PythonAnywhere

Haz copia de `papia-crm/papia_crm.db` antes de migrar.

```bash
git switch main && git pull origin main
cd papia-crm
python migrate_demos.py          # idempotente; también corre al arrancar Flask
python import_grooming.py        # trae los 26 códigos de Groomingmiami con su historial (una vez)
python -m unittest discover -s tests -v
```

Pulsa **Reload** en la pestaña Web de `datos.papiatech.com`. No hay dependencias nuevas.

### Variables de entorno (.env) — todas opcionales

| Variable | Para qué |
|---|---|
| `DEMO_SESSION_SECRET` | Firma de la sesión de 45 min. Si falta, se usa `SECRET_KEY`. Recomendado: un valor propio largo. |
| `DEMO_NOTIFY_WEBHOOK` | URL del webhook de n8n que te avisa por WhatsApp/SMS. |
| `DEMO_NOTIFY_SECRET` | Se envía en el header `X-Papia-Demo-Secret` al webhook. |
| `DEMO_NOTIFY_EMAIL` | Correo que recibe los avisos (sale por el Gmail conectado en Emails). |
| `DEMO_ALLOWED_ORIGINS` | Dominios extra que pueden llamar a la API, separados por coma. Ya permitidos: `*.lovable.app`, `demo.papiatech.com`, `papiatech.com`. |

Payload del webhook:

```json
{"evento": "primera_visita", "texto": "INNA Beauty Salon abrió el demo · 1 de 3 · Safari · iOS · Oct 20, 04:12 PM · INNA-7K2P",
 "codigo": "INNA-7K2P", "prospecto": "Inna", "negocio": "INNA Beauty Salon", "telefono": "3059865677",
 "visita": "1 de 3", "dispositivo": "Safari · iOS", "fecha": "Oct 20, 04:12 PM", "cliente_id": 123}
```

`evento` es `primera_visita` o `bloqueado`. En n8n basta un Webhook → Twilio (WhatsApp/SMS) con `{{$json.texto}}`.

## API para los demos (Lovable)

Base: `https://datos.papiatech.com/api/demos`. Todas son `POST` con JSON. CORS solo para los
orígenes permitidos. Límite: 20 validaciones por IP cada 10 minutos.

**`/validar`** — el demo la llama cuando el prospecto entra por su enlace `<demo>/d/CODIGO`.

```json
// petición
{"codigo": "INNA-7K2P", "deviceId": "uuid-guardado-en-localStorage"}
// respuesta válida
{"ok": true, "token": "…", "codigo": "INNA-7K2P", "negocio": "INNA Beauty Salon", "prospecto": "Inna",
 "nicho": "unas", "color": "#DB2777", "visita": 1, "max_visitas": 3, "ilimitado": false,
 "sesion_minutos": 45, "vence_el": "2026-10-27T20:12:00+00:00"}
// inválido, vencido, agotado o inexistente (mismo mensaje a propósito)
{"ok": false, "error": "acceso_no_valido"}
```

Cada llamada válida consume una visita. Guarda el `token` en `sessionStorage` y úsalo durante
la sesión; no vuelvas a llamar `/validar` al recargar la página.

**`/sesion`** — `{"token": "…"}` → `{"ok": true, "codigo", "negocio", "nicho", "color"}` o `{"ok": false}`.
Úsala al recargar para saber si la sesión sigue viva (45 min).

**`/evento`** — `{"token": "…", "tipo": "seccion_vista" | "salida", "seccion": "Agenda del dueño", "duracion": 42}`.
`duracion` en segundos (0–7200). Máximo 400 eventos por código al día.

## Cambio de Groomingmiami al CRM (orden importante)

1. Merge del PR, `git pull`, `migrate_demos.py` e `import_grooming.py` en PythonAnywhere, Reload.
2. Comprobar `https://datos.papiatech.com/demos`: aparecen los códigos de grooming.
3. Recién entonces publicar en Lovable la versión de Groomingmiami que valida contra esta API.
   Si se publica antes, los prospectos verían el demo bloqueado.
4. Los códigos y registros viejos quedan intactos en Supabase como respaldo.

Si entre la exportación (20 oct 2026) y la publicación hubo visitas nuevas, vuelve a exportar
`data/grooming_*` antes del paso 1; `import_grooming.py` salta los códigos ya importados.

## Checklist en producción

- [ ] Abrir Demos, editar Grooming y confirmar el enlace publicado.
- [ ] Crear un código de prueba ligado a un lead y copiar el mensaje.
- [ ] Desde el teléfono, abrir el demo con el código: aparece la entrada, la tarea en Tasks y el aviso.
- [ ] Usar todas las visitas: el siguiente intento queda como bloqueado y llega un solo aviso.
- [ ] Probar `+7 días`, `+2 visitas`, `Reiniciar` y `Desactivar`.
