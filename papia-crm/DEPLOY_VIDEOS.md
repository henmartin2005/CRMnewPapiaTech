# Videos · Fase 1

## Preparar Google Cloud

1. Usa el mismo proyecto y cliente OAuth web que Emails/Calendar. Habilita **Google Drive API** en APIs y servicios → Biblioteca.
2. En la pantalla de consentimiento añade únicamente el nuevo permiso `https://www.googleapis.com/auth/drive.file`. Conserva los permisos de Gmail/Calendar existentes. En modo de prueba, añade la cuenta del administrador a los usuarios de prueba.
3. En el cliente OAuth añade **sin sustituir las URI actuales**:
   `https://datos.papiatech.com/videos/oauth2callback`.
   Conserva `https://datos.papiatech.com/emails/oauth2callback` y cualquier callback del login existente.
4. No crees carpetas manualmente. El módulo crea `PapiaTech Videos`, y después `{Nombre del cliente}` o `Sin cliente`. Usa IDs y marcas privadas de la app para recuperar carpetas, incluso si se cambia su nombre. Clientes con el mismo nombre tienen carpetas independientes por ID.
5. Conecta Drive desde Videos con **la misma cuenta de Google** que Emails. La solicitud pide solo `drive.file` y `include_granted_scopes=true`; comprueba la cuenta y los permisos anteriores antes de actualizar `gmail_tokens`. Si se revocó la conexión anterior, reconecta primero Emails y después Drive. No se modifica el OAuth de Emails ni de Calendar.

## PythonAnywhere

Revisa y fusiona el PR `feature/videos` → `main`. En una consola Bash, entra en el repositorio y activa el entorno virtual actual. Haz una copia de seguridad de `papia-crm/papia_crm.db` antes de migrar.

```bash
git switch main
git pull origin main
cd papia-crm
pip install -r requirements.txt
python migrate_videos.py
python -m unittest discover -s tests -v
```

En desarrollo, las cinco pruebas del cliente de subida se ejecutan con `node --test tests/test_videos_upload.mjs` (Node 18 o superior). Node no es necesario en producción.

Usa Python 3.10 o superior, como requiere el código existente. La migración es idempotente y también se ejecuta al arrancar Flask. Las pruebas usan una base temporal y simulan Google; no usan tokens ni archivos reales. `requirements.txt` ya incluye Google Auth, OAuthlib, Google API Client y requests, por eso no cambia.

En la pestaña Web de PythonAnywhere, pulsa **Reload** de `datos.papiatech.com`. Confirma que la app recibe el esquema y dominio públicos correctos; el callback y el encabezado `Origin` de las sesiones de subida se generan a partir del origen de la petición. El valor en producción debe ser `https://datos.papiatech.com`.

## Configuración y dependencias del navegador

No hay variables de entorno nuevas. Se reutilizan las credenciales Google por organización y el respaldo existente `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`; se conservan `GMAIL_REDIRECT_URI` y `SECRET_KEY`. El callback de Videos es independiente de `GMAIL_REDIRECT_URI`.

No se necesita FFmpeg instalado en PythonAnywhere, workers, Celery, COOP/COEP ni almacenamiento de videos en el servidor. Solo viajan JSON y metadatos por Flask; el navegador descarga y sube los videos directamente a Drive. Se rechazan cuerpos superiores a 32 KB en la API de Videos.

El cargador `@ffmpeg/ffmpeg` **0.12.15** y su worker pequeño se sirven desde `static/js/vendor/ffmpeg` para cumplir la política de origen del navegador. El core **single-thread** `@ffmpeg/core` **0.12.10** y su WASM se obtienen de jsDelivr mediante `toBlobURL`. No se usa `core-mt` ni SharedArrayBuffer. Montserrat y su licencia OFL están en `static/fonts`. El cargador de FFmpeg tiene licencia MIT; el core incluye FFmpeg y codecs con sus propias licencias (véase el [proyecto oficial](https://github.com/ffmpegwasm/ffmpeg.wasm)).

El navegador debe poder acceder a `cdn.jsdelivr.net`, `www.googleapis.com` y `oauth2.googleapis.com`. Si se configura una CSP, permite `worker-src 'self' blob:`, `connect-src` para Drive y el CDN, y `script-src`/WASM según la política del sitio. No añadas cabeceras globales de aislamiento.

## Datos y comportamiento

- Tablas nuevas: `videos`, `drive_carpetas`, `videos_uploads`. Todas las consultas y carpetas se delimitan por organización. Solo administradores y superadministradores tienen acceso a Videos.
- `cliente_id` referencia la tabla real `clients`. `proyecto_id` queda nullable: esta versión de Pizarra usa `client_layouts` y no tiene entidad de proyecto independiente. Se rechazan vínculos a proyectos inexistentes.
- `deleted_at` implementa soft delete. El archivo se mueve a la papelera, nunca se elimina permanentemente.
- `videos_uploads` conserva intenciones de subida durante siete días para verificar tamaño, MIME, carpeta, organización y nonce antes de registrar. Los reintentos de registro son idempotentes. Se limpian intenciones antiguas al iniciar otra subida.
- Las subidas usan bloques de 8 MB, consultan el rango confirmado tras errores y conservan la sesión en memoria para reintentar. Una sesión caducada se reemplaza al reintentar. El MP4 exportado sigue disponible para descargar si Drive falla.
- Guardar originales es una acción explícita. Exportar guarda automáticamente una nueva versión `editado`, con referencia al video padre cuando existe. El estudio incluye biblioteca de medios con miniaturas, reproductor, ajustes por pestañas y línea de tiempo visual con pistas de video, texto y audio. La previsualización muestra el recorte, la velocidad, el encuadre y una aproximación del texto; la mezcla de música se aplica al MP4 exportado.
- La biblioteca muestra hasta 200 resultados por filtro. El editor permite autocompletar clientes y elegir clips de la biblioteca de la organización.
- El render a 1080p puede ser lento en móviles. Hay avisos sobre 500 MB por archivo y por conjunto; los clips, archivos temporales y resultado viven en la memoria del navegador. Cerrar la pestaña pierde una edición o subida pendiente; no hay persistencia de proyectos en esta fase.
- Una subida cancelada puede dejar una sesión pendiente en Drive. Si se completó pero el usuario cierra antes de registrar, el archivo seguirá en la carpeta de Drive. Se evita borrarlo automáticamente.

## Checklist manual en la instalación real

- [ ] Iniciar sesión como admin, abrir Videos y conceder `drive.file` con la misma cuenta que Emails. Comprobar Gmail y Calendar después de conectar.
- [ ] Cancelar el consentimiento, denegar permisos y probar cuenta equivocada: mensajes claros y tokens anteriores conservados.
- [ ] Desconectar/revocar Google y confirmar que aparece **Reconecta Google Drive** y el botón para conectar.
- [ ] Subir un original sin cliente: comprobar `PapiaTech Videos/Sin cliente` y que se creó una sola raíz.
- [ ] Subir un original con cliente: comprobar su carpeta, filtro, búsqueda y sección Videos en el perfil. Repetir con otro cliente del mismo nombre.
- [ ] Cargar clips locales y desde Drive. Ordenar con arrastre y flechas, recortar inicio/fin, previsualizar, probar 0.5x/1x/1.5x/2x, y combinar un clip sin audio con uno con audio.
- [ ] Elegir 9:16, texto en español con acentos y signos, intervalo, música y volúmenes. Exportar; verificar H.264/AAC, 1080×1920, recortes, texto, música y fade final. Repetir 1:1 y 16:9 en ambos modos crop/pad.
- [ ] Confirmar progreso de procesamiento y subida, descarga local opcional y nueva versión en Drive/biblioteca con cliente y padre correctos.
- [ ] Interrumpir la conexión durante una subida de varios bloques; recuperar conexión y comprobar reanudación. Probar fallo del último bloque y del registro final, así como el botón de reintento.
- [ ] Cancelar durante procesamiento/subida, volver a exportar y comprobar que la página sigue utilizable. Simular Drive lleno: conservar la descarga del MP4.
- [ ] Elegir archivo >500 MB y comprobar advertencia; probar layout móvil y escritorio.
- [ ] Eliminar desde biblioteca: desaparece del CRM y aparece en la papelera de Drive. No se elimina definitivamente.
- [ ] Verificar que un usuario no admin y otra organización no acceden a registros, clientes ni tokens ajenos.
- [ ] Comprobar Dashboard, clientes, Pizarra, WhatsApp, Emails, Calendar y pagos Stripe existentes.
- [ ] En la pestaña Red del navegador, confirmar que los bytes de videos solo van a Google, y que Flask recibe JSON pequeño.

## Referencias técnicas

- [Carga resumible de Drive](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
- [Autorización incremental de Google](https://developers.google.com/identity/protocols/oauth2/web-server#incrementalAuth)
- [Uso del core single-thread de FFmpeg.wasm](https://ffmpegwasm.netlify.app/docs/getting-started/usage/)
