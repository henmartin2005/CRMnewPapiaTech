# Papia CRM — app de iPhone (SwiftUI)

App nativa para iPhone conectada al CRM (`datos.papiatech.com`) a través de `/api/mobile`.

| Módulo | Qué hace |
|---|---|
| WhatsApp | Lista de chats con no leídos, chat con burbujas, envío de texto y fotos, plantillas aprobadas, asistente IA on/off por contacto |
| Notificaciones | Push al entrar un WhatsApp; tocar abre el chat; **responder desde la notificación** sin abrir la app |
| Clientes | Lista con búsqueda y filtro por etapa, perfil (finanzas, seguimientos, historial, contratos), crear y editar |
| Pipeline | Una etapa a la vez con valor y por cobrar; mover clientes deslizando o manteniendo presionada la tarjeta |
| Contratos | Lista por estado, detalle con cada firmante, ver PDF y PDF sellado, reenviar enlace, anular; **enviar contrato nuevo desde el iPhone** |
| Más | Tareas y seguimientos, ajustes de notificaciones, cerrar sesión, módulos próximos |

## Abrirlo en Xcode

Requisitos: Xcode 16 o superior, iOS 17+.

```bash
git clone https://github.com/henmartin2005/CRMnewPapiaTech.git
cd CRMnewPapiaTech
git checkout feature/ios-app
open mobile/ios/PapiaCRM.xcodeproj
```

1. Selecciona el target **PapiaCRM** → **Signing & Capabilities** → elige tu **Team** (cuenta de Apple Developer).
2. Si `com.papiatech.crm` ya está tomado, cambia el **Bundle Identifier**.
3. Elige un simulador (o tu iPhone conectado) y pulsa **⌘R**.
4. Entra con el mismo usuario y contraseña del CRM web. El servidor se puede cambiar en la pantalla de login (“Cambiar servidor”), útil para otras instancias como Maybis CRM.

Las carpetas del proyecto están **sincronizadas**: cualquier archivo `.swift` que agregues dentro de `PapiaCRM/` (desde Xcode o desde Finder) entra solo al target.

## Diseñar la app

Todo el diseño está centralizado para que puedas cambiarlo sin tocar la lógica:

- **Colores** → `PapiaCRM/Resources/Assets.xcassets`. Cada color (BrandNavy, BrandAqua, BrandCoral, BubbleOutgoing, etc.) tiene versión clara y oscura; edítalos con el inspector de Xcode.
- **Tipografía, espacios y radios** → `PapiaCRM/Design/Theme.swift`.
- **Componentes** (avatar, etiquetas, tarjetas, botones, chips) → `PapiaCRM/Design/Components.swift`.
- **Ícono** → `Assets.xcassets/AppIcon` (hay uno provisional; reemplázalo por un PNG de 1024×1024). El logo del login es `Assets.xcassets/Logo`.
- **Canvas**: cada pantalla tiene su `#Preview` con datos de ejemplo (`Core/PreviewData.swift`). Abre cualquier vista y pulsa **⌥⌘↩** para verla y editarla en vivo sin conectarte al servidor.

## Estructura

```
PapiaCRM/
  App/        arranque, sesión (AppState), notificaciones push (PushManager)
  Core/       APIClient (todas las llamadas a /api/mobile), modelos, Keychain, formatos, datos de ejemplo
  Design/     Theme y componentes reutilizables
  Features/
    Login/  WhatsApp/  Clients/  Pipeline/  Contracts/  More/
  Resources/  Assets.xcassets
```

## Notificaciones push (APNs)

Funcionan en un **iPhone real** (en el simulador no llega push de APNs).

**En Xcode:** Signing & Capabilities → **+ Capability** → **Push Notifications** (el archivo `PapiaCRM.entitlements` ya está listo).

**En Apple Developer** → Certificates, IDs & Profiles → **Keys** → crea una llave con **Apple Push Notifications service (APNs)** y descarga el `AuthKey_XXXXXXXXXX.p8` (solo se puede descargar una vez).

**En el servidor (PythonAnywhere):**

```bash
cd ~/CRMnewPapiaTech/papia-crm
source ../.venv/bin/activate
pip install "httpx[http2]" h2
mkdir -p ~/secrets && chmod 700 ~/secrets   # sube ahí el AuthKey_XXXX.p8
```

Agrega al `.env`:

```
APNS_KEY_ID=XXXXXXXXXX            # el Key ID de la llave
APNS_TEAM_ID=YYYYYYYYYY           # tu Team ID (arriba a la derecha en Apple Developer)
APNS_BUNDLE_ID=com.papiatech.crm  # el mismo Bundle Identifier de Xcode
APNS_KEY_PATH=/home/henmartin2005/secrets/AuthKey_XXXXXXXXXX.p8
```

Recarga la web app. Cada iPhone registra su token en `mobile_devices` al iniciar sesión. Los builds de Xcode usan el ambiente *sandbox* y los de TestFlight/App Store usan *production*; la app le dice al servidor cuál usar.

## API móvil (resumen)

Todas con `Authorization: Bearer <token>` salvo el login.

| Método | Ruta | Uso |
|---|---|---|
| POST | `/api/mobile/login` | usuario + contraseña → token (30 días) |
| GET | `/api/mobile/me`, `/metadata`, `/dashboard` | sesión y catálogos |
| GET/POST | `/api/mobile/clients` | listar (`?q=`) / crear |
| GET/PATCH | `/api/mobile/clients/<id>` | detalle / editar (parcial) |
| POST | `/api/mobile/clients/<id>/followups` | nuevo seguimiento |
| GET | `/api/mobile/pipeline` · POST `/pipeline/move` | etapas / mover |
| GET | `/api/mobile/tasks` · POST `/tasks/<id>/complete` | tareas |
| GET | `/api/mobile/whatsapp` · `/whatsapp/messages?phone=` | chats / mensajes |
| POST | `/api/mobile/whatsapp/send` · `/send-media` · `/read` · `/bot` | enviar, adjuntos, leído, asistente IA |
| GET | `/api/mobile/whatsapp/templates` · `/whatsapp/media/<id>` | plantillas / adjuntos |
| POST/DELETE | `/api/mobile/devices` | registrar token de push |
| GET/POST | `/api/mobile/contracts` | listar (`?tab=&q=&client_id=`) / crear (multipart, `mode=send|draft`) |
| GET/DELETE | `/api/mobile/contracts/<id>` | detalle / eliminar borrador |
| POST | `/contracts/<id>/send` · `/resend/<rid>` · `/void` | enviar, reenviar, anular |
| GET | `/contracts/<id>/document` · `/final` | PDF original / sellado |

## Pendiente de análisis

Emails, propuestas, pagos y cronograma, Pizarra, Instagram/Messenger, notas de voz (grabar y reproducir: WhatsApp usa audio OGG/Opus, que iOS no reproduce sin conversión), login con Google y colocar campos de firma sobre el PDF desde el iPhone.
