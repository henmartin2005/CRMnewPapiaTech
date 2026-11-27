# Correos festivos — despliegue

Navidad (24 dic), Año Nuevo (1 ene), San Valentín (14 feb), 4 de Julio y Thanksgiving
(se envía el miércoles anterior). **Nadie recibe nada si no lo marcas.**

## 1. Subir el código (PythonAnywhere → Bash console)

```bash
cd /home/henmartin2005/CRMnewPapiaTech
git fetch origin
git checkout feature/correos-festivos
git pull
```

Web → **Reload** `datos.papiatech.com`. Las tablas (`client_festivos`, `festivos_envios`,
`festivos_settings`) se crean solas al arrancar.

## 2. Ajustes (una sola vez)

Menú **Festivos** → cuadro **Ajustes**:
- **Dirección física** de PapiaTech (va en el pie del correo, la pide CAN-SPAM).
- **Email para avisos y pruebas** (por defecto henrry@papiatech.com).

## 3. Marcar clientes

- En el **perfil del cliente** → cuadro *Correos festivos*: activar, y llenar **Proyecto**
  (ej. "tu nueva página web con reservas online") y **Logro** (opcional).
- O en la **lista de clientes**: botón 🎉 de cada fila (gris = no recibe). Filtro *🎉 Festivos*.
- O en **Festivos → Clientes que reciben**: marcar varios y *Guardar selección*.

## 4. Tarea diaria

PythonAnywhere → **Tasks** → Scheduled task, diaria a las **13:00 UTC**
(8:00 AM Miami en invierno, 9:00 AM en verano):

```
/home/henmartin2005/CRMnewPapiaTech/.venv/bin/python /home/henmartin2005/CRMnewPapiaTech/papia-crm/send_festivos.py
```

Probar sin enviar nada:

```bash
.venv/bin/python papia-crm/send_festivos.py --dry-run --fecha 2026-12-21
```

## Cómo funciona

1. **3 días antes** la tarea crea la lista en *pendiente* y te manda un email con quiénes
   la recibirán y el enlace al panel.
2. En **Festivos** puedes ver el correo de cada cliente, **Omitir** a alguien o
   **Enviarte una prueba**.
3. **El día del envío** salen solo los *pendiente* (pausa de 1.5 s entre cada uno).
   Cada correo queda en Emails → Enviados.
4. Nunca se repite: un cliente recibe cada ocasión una sola vez por año.
5. El enlace *"No quiero recibir más estos correos"* pide confirmación y desactiva al
   cliente. En su perfil verás "Se dio de baja".
