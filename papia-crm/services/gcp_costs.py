"""Gasto real de Google Maps Platform por cliente, leído de la exportación de facturación.

Cómo funciona
-------------
Google Cloud exporta cada día el costo por SKU a BigQuery (Facturación → Exportación de la
facturación → Costo de uso estándar). Este módulo consulta esa tabla con una cuenta de
servicio de solo lectura y suma, por mes y por SKU, lo que va generando el proyecto de Google
Cloud vinculado al cliente. Los usos gratis del mes y los créditos ya vienen descontados
(neto = costo + créditos, los créditos son negativos).

Variables de entorno
--------------------
GCP_COSTS_SA_FILE    Ruta al JSON de la cuenta de servicio (fuera del repo).
                     Por defecto ~/secrets/gcp-costs-sa.json
GCP_BILLING_TABLE    Tabla de la exportación. Por defecto la de la cuenta de facturación de
                     Henrry en el proyecto yeras-services.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from database import get_db

DEFAULT_TABLE = 'yeras-services.billing_export.gcp_billing_export_v1_01B5BD_04CE83_3C8E24'
SCOPES = ['https://www.googleapis.com/auth/bigquery']
CACHE_MINUTES = 60
PROJECT_RE = re.compile(r'^[a-z][a-z0-9\-]{4,28}[a-z0-9]$')
TABLE_RE = re.compile(r'^[a-z][a-z0-9\-]{4,28}[a-z0-9]\.[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}$')

# Servicios de Google Maps Platform (descripción en la exportación de facturación).
MAPS_REGEX = (r'(?i)(maps|places|routes|geocod|directions|distance matrix|address validation|'
              r'roads|street view|elevation|time zone|geolocation|aerial view|air quality|'
              r'pollen|solar|weather)')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_gcp_costs (
    client_id      INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    org_id         INTEGER NOT NULL DEFAULT 1,
    gcp_project_id TEXT    NOT NULL,
    cache_json     TEXT,
    cached_at      TEXT,
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class CostsError(Exception):
    """Error que se le puede mostrar al usuario tal cual."""


def migrate():
    db = get_db()
    try:
        db.execute(_SCHEMA)
        db.commit()
    finally:
        db.close()


# ─────────────────────────── Configuración por cliente ───────────────────────────
def get_config(client_id, org_id):
    db = get_db()
    try:
        db.execute(_SCHEMA)
        row = db.execute('SELECT * FROM client_gcp_costs WHERE client_id=? AND org_id=?',
                         (client_id, org_id)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def set_project(client_id, org_id, project_id):
    project_id = (project_id or '').strip().lower()
    if not PROJECT_RE.match(project_id):
        raise CostsError('ID de proyecto de Google Cloud no válido (ej. yeras-services).')
    db = get_db()
    try:
        db.execute(_SCHEMA)
        db.execute(
            'INSERT INTO client_gcp_costs (client_id, org_id, gcp_project_id) VALUES (?, ?, ?) '
            'ON CONFLICT(client_id) DO UPDATE SET gcp_project_id=excluded.gcp_project_id, '
            'org_id=excluded.org_id, cache_json=NULL, cached_at=NULL',
            (client_id, org_id, project_id))
        db.commit()
    finally:
        db.close()


def unlink(client_id, org_id):
    db = get_db()
    try:
        db.execute(_SCHEMA)
        db.execute('DELETE FROM client_gcp_costs WHERE client_id=? AND org_id=?', (client_id, org_id))
        db.commit()
    finally:
        db.close()


def _save_cache(client_id, data):
    db = get_db()
    try:
        db.execute('UPDATE client_gcp_costs SET cache_json=?, cached_at=? WHERE client_id=?',
                   (json.dumps(data), _now().strftime('%Y-%m-%d %H:%M:%S'), client_id))
        db.commit()
    finally:
        db.close()


def _now():
    return datetime.now(timezone.utc)


# ─────────────────────────────── BigQuery ───────────────────────────────
def _sa_file():
    return os.environ.get('GCP_COSTS_SA_FILE') or os.path.expanduser('~/secrets/gcp-costs-sa.json')


def _table():
    table = os.environ.get('GCP_BILLING_TABLE') or DEFAULT_TABLE
    if not TABLE_RE.match(table):
        raise CostsError('GCP_BILLING_TABLE no tiene el formato proyecto.dataset.tabla.')
    return table


def _session():
    path = _sa_file()
    if not os.path.isfile(path):
        raise CostsError(f'Falta el archivo de la cuenta de servicio en {path}.')
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    return AuthorizedSession(creds)


def run_query(project_id):
    """Devuelve filas [{mes, servicio, sku, costo, creditos, uso, unidad, moneda}] y la última exportación."""
    table = _table()
    billing_project = table.split('.', 1)[0]
    sql = f"""
        SELECT invoice.month AS mes,
               service.description AS servicio,
               sku.description AS sku,
               currency AS moneda,
               SUM(cost) AS costo,
               SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS creditos,
               SUM(usage.amount_in_pricing_units) AS uso,
               ANY_VALUE(usage.pricing_unit) AS unidad,
               MAX(export_time) AS exportado
        FROM `{table}`
        WHERE project.id = @project
          AND REGEXP_CONTAINS(service.description, @maps)
        GROUP BY mes, servicio, sku, moneda
        ORDER BY mes DESC, costo DESC
    """
    body = {
        'query': sql,
        'useLegacySql': False,
        'location': 'US',
        'timeoutMs': 20000,
        'parameterMode': 'NAMED',
        'queryParameters': [
            {'name': 'project', 'parameterType': {'type': 'STRING'},
             'parameterValue': {'value': project_id}},
            {'name': 'maps', 'parameterType': {'type': 'STRING'},
             'parameterValue': {'value': MAPS_REGEX}},
        ],
    }
    url = f'https://bigquery.googleapis.com/bigquery/v2/projects/{billing_project}/queries'
    try:
        resp = _session().post(url, json=body, timeout=30)
    except CostsError:
        raise
    except Exception as exc:  # noqa: BLE001 — red, credenciales
        raise CostsError(f'No se pudo conectar con BigQuery: {exc}') from exc
    if resp.status_code == 404:
        # La tabla la crea Google con la primera exportación (tarda hasta un día).
        return [], None
    if resp.status_code >= 400:
        try:
            msg = resp.json().get('error', {}).get('message', resp.text)
        except ValueError:
            msg = resp.text
        raise CostsError(f'BigQuery respondió {resp.status_code}: {msg[:300]}')
    payload = resp.json()
    if not payload.get('jobComplete', True):
        raise CostsError('La consulta tardó demasiado. Pulsa Actualizar en un momento.')
    fields = [f['name'] for f in payload.get('schema', {}).get('fields', [])]
    rows = []
    for r in payload.get('rows', []):
        rows.append({k: cell.get('v') for k, cell in zip(fields, r.get('f', []))})
    return rows, None


# ─────────────────────────────── Resumen ───────────────────────────────
def _f(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _month_label(yyyymm):
    meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
    try:
        return f'{meses[int(yyyymm[4:6]) - 1]} {yyyymm[:4]}'
    except (ValueError, IndexError, TypeError):
        return yyyymm or '—'


def summarize(rows, project_id):
    meses = {}
    exportado = None
    moneda = 'USD'
    for r in rows:
        mes = r.get('mes') or ''
        neto = _f(r.get('costo')) + _f(r.get('creditos'))
        moneda = r.get('moneda') or moneda
        m = meses.setdefault(mes, {'mes': mes, 'label': _month_label(mes), 'bruto': 0.0,
                                   'creditos': 0.0, 'neto': 0.0, 'skus': []})
        m['bruto'] += _f(r.get('costo'))
        m['creditos'] += _f(r.get('creditos'))
        m['neto'] += neto
        m['skus'].append({
            'servicio': r.get('servicio') or '',
            'sku': r.get('sku') or '',
            'uso': _f(r.get('uso')),
            'unidad': r.get('unidad') or '',
            'bruto': round(_f(r.get('costo')), 4),
            'neto': round(neto, 4),
        })
        ts = r.get('exportado')
        if ts:
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                ts = None
            if ts and (exportado is None or ts > exportado):
                exportado = ts
    lista = sorted(meses.values(), key=lambda m: m['mes'], reverse=True)
    for m in lista:
        for k in ('bruto', 'creditos', 'neto'):
            m[k] = round(m[k], 4)
    mes_actual = _now().strftime('%Y%m')
    actual = next((m for m in lista if m['mes'] == mes_actual), None)
    return {
        'proyecto': project_id,
        'moneda': moneda,
        'total_neto': round(sum(m['neto'] for m in lista), 4),
        'total_bruto': round(sum(m['bruto'] for m in lista), 4),
        'mes_actual': actual or {'mes': mes_actual, 'label': _month_label(mes_actual),
                                 'bruto': 0.0, 'creditos': 0.0, 'neto': 0.0, 'skus': []},
        'meses': lista,
        'sin_datos': not lista,
        'ultima_exportacion': (datetime.fromtimestamp(exportado, timezone.utc).isoformat()
                               if exportado else None),
        'consultado': _now().isoformat(),
    }


def get_costs(client_id, org_id, force=False):
    cfg = get_config(client_id, org_id)
    if not cfg:
        raise CostsError('Este cliente no tiene un proyecto de Google Cloud vinculado.')
    if not force and cfg.get('cache_json') and cfg.get('cached_at'):
        try:
            cached_at = datetime.strptime(cfg['cached_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            if _now() - cached_at < timedelta(minutes=CACHE_MINUTES):
                data = json.loads(cfg['cache_json'])
                data['desde_cache'] = True
                return data
        except ValueError:
            pass
    rows, _ = run_query(cfg['gcp_project_id'])
    data = summarize(rows, cfg['gcp_project_id'])
    _save_cache(client_id, data)
    data['desde_cache'] = False
    return data
