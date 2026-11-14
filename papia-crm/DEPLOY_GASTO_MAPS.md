# Gasto de API de Google Maps por cliente

Tarjeta en el perfil del cliente (solo admin) que suma lo que va gastando su proyecto de
Google Cloud en Google Maps Platform: este mes, acumulado, lo que Google descontó (usos
gratis y créditos), detalle por SKU y por mes. Botón **Actualizar** (caché de 1 h).

## De dónde salen los números

Del costo real que exporta Google Cloud: Facturación → Exportación de la facturación →
**Costo de uso estándar** → BigQuery `yeras-services.billing_export`. Google la actualiza una
vez al día; los primeros datos tardan hasta 24 h después de activarla (14 nov 2026).
La consulta filtra por `project.id` del cliente y por servicios de Maps (Maps, Places, Routes,
Geocoding…), así que el costo de BigQuery de la propia exportación no se cuenta.

Cuenta de servicio de solo lectura: `crm-gastos@yeras-services.iam.gserviceaccount.com`
(roles BigQuery Data Viewer + BigQuery Job User en `yeras-services`).

## Instalar en PythonAnywhere

1. Sube el JSON de la cuenta de servicio **fuera del repo**:
   `/home/henmartin2005/secrets/gcp-costs-sa.json` (Files → crea la carpeta `secrets`).
2. Actualiza el código y prueba:

```bash
cd ~/CRMnewPapiaTech && git switch main && git pull origin main
cd papia-crm && python -m unittest discover -s tests -v
```

3. **Reload** en la pestaña Web. La tabla `client_gcp_costs` se crea sola al arrancar.
4. En el cliente → **Editar** → bloque "Gasto de API de Google Maps" → ID del proyecto
   (`yeras-services`) → Vincular.

## Variables de entorno (opcionales)

| Variable | Por defecto |
|---|---|
| `GCP_COSTS_SA_FILE` | `~/secrets/gcp-costs-sa.json` |
| `GCP_BILLING_TABLE` | `yeras-services.billing_export.gcp_billing_export_v1_01B5BD_04CE83_3C8E24` |

La tabla de exportación cubre toda la cuenta de facturación, así que sirve para cualquier
otro cliente cuyo proyecto use esa misma cuenta: solo hay que vincular su ID de proyecto.
