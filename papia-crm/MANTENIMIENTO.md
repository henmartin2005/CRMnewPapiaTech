# Mantenimiento recurrente

Implementado el 22 de octubre de 2026. Libro de mantenimiento separado del costo y los pagos de desarrollo. No procesa cobros bancarios ni suspende infraestructura.

Configuración por cliente: tarifa, frecuencia mensual/trimestral/anual, día habitual, activación, facturación, garantía, entrega aceptada y evidencia, condiciones y avisos relativos al vencimiento. Sin tarifa por defecto. Administración restringida a admin/superadmin, organización del cliente y token CSRF.

Cada cambio conserva una versión con actor. Los períodos emitidos conservan importe, servicio, método y fecha originales. Los pagos requieren fecha, evidencia y referencia única; permiten abonos parciales y no incrementan amount_paid del desarrollo. Al saldar un período se completan sus avisos pendientes.

Generación: solo planes activos con fecha de activación e inicio, hasta 40 días por adelantado. El primer vencimiento es el día habitual igual o posterior al inicio. No existe prorrateo automático. Días 29 a 31: último día del mes cuando corresponda. Lucas queda pendiente de activación hasta acreditar entrega aceptada y 30 días de espera; día contractual 5, propuesta día 1 pendiente de aceptación escrita.

Avisos internos mediante follow_ups, por defecto -5, 0, +3 y +7 días. Idempotencia por período y plazo; se omiten preavisos ya vencidos al recuperar una ejecución. No envía email/WhatsApp. Las suspensiones/cancelaciones son administrativas, anulan períodos futuros sin pagos y no cortan el hosting. La ejecución diaria no altera estados de servicio.

## Operación

PythonAnywhere: tarea 1555329, diaria a las 13:05 UTC, sin expiración. Comando:
/home/henmartin2005/CRMnewPapiaTech/.venv/bin/python /home/henmartin2005/CRMnewPapiaTech/papia-crm/run_maintenance.py

Ejecución manual: python run_maintenance.py
Pruebas aisladas: python tests/test_maintenance.py

Tablas nuevas: maintenance_plans, maintenance_versions, maintenance_periods, maintenance_receipts, maintenance_notices. Cambios de integración: app.py, templates/clients/detail.html y static/js/profile_board.js. Respaldo previo: backups/maintenance-20261022-190728, incluye instantánea SQLite y archivos anteriores. No restaurar una base antigua sobre operaciones posteriores; la reversión del código debe preservar las tablas y datos nuevos.
