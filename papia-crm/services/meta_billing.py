# -*- coding: utf-8 -*-
"""Costos de WhatsApp que cobra Meta (pricing_analytics de la WABA)."""
import os
import threading
import time
from calendar import monthrange
from datetime import datetime, timedelta, timezone

import requests

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))
except Exception:  # pragma: no cover
    LOCAL_TZ = timezone.utc

GRAPH_VERSION = os.getenv('META_GRAPH_VERSION', 'v21.0')
CACHE_SECONDS = 30 * 60
_cache = {}
_lock = threading.Lock()

# Tarifa por mensaje (USD) para estimar cuando Meta no da el costo (cuentas que pagan vía partner/BSP).
# Se pueden ajustar en .env: WA_RATE_MARKETING, WA_RATE_UTILITY, WA_RATE_AUTHENTICATION
RATES = {
    'MARKETING': float(os.getenv('WA_RATE_MARKETING', '0.025')),
    'MARKETING_LITE': float(os.getenv('WA_RATE_MARKETING', '0.025')),
    'UTILITY': float(os.getenv('WA_RATE_UTILITY', '0.004')),
    'AUTHENTICATION': float(os.getenv('WA_RATE_AUTHENTICATION', '0.0135')),
    'AUTHENTICATION_INTERNATIONAL': float(os.getenv('WA_RATE_AUTHENTICATION', '0.0135')),
    'SERVICE': 0.0,
}

CATEGORY_LABELS = {
    'MARKETING': 'Marketing',
    'MARKETING_LITE': 'Marketing (lite)',
    'UTILITY': 'Utilidad',
    'AUTHENTICATION': 'Autenticación',
    'AUTHENTICATION_INTERNATIONAL': 'Autenticación internacional',
    'SERVICE': 'Servicio (respuestas)',
}


def token():
    return (os.getenv('META_WA_TOKEN') or os.getenv('META_WHATSAPP_TOKEN') or '').strip()


def waba_id():
    return (os.getenv('META_WABA_ID') or '').strip()


def _zernio():
    try:
        from services import zernio
        return zernio if zernio.is_configured() and zernio.account_id() else None
    except Exception:
        return None


def source():
    """'zernio' (API key que ya usa el CRM) o 'meta' (token propio de Meta)."""
    if _zernio():
        return 'zernio'
    if token() and waba_id():
        return 'meta'
    return None


def is_configured():
    return source() is not None


def _get(path, params):
    params = dict(params or {})
    params['access_token'] = token()
    r = requests.get(f'https://graph.facebook.com/{GRAPH_VERSION}/{path}', params=params, timeout=25)
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError('Respuesta no válida de Meta')
    if r.status_code >= 400 or 'error' in data:
        err = data.get('error') or {}
        raise RuntimeError(err.get('message') or f'Error {r.status_code} de Meta')
    return data


def _zernio_query(start_dt, end_dt, metric_types):
    z = _zernio()
    return z._request('GET', '/v1/whatsapp/pricing-analytics', headers=z._headers(), params={
        'accountId': z.account_id(),
        'start': start_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'end': end_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'granularity': 'DAILY',
        'dimensions': 'PRICING_CATEGORY,PRICING_TYPE',
        'metricTypes': metric_types,
    }) or {}


def _points_zernio(start_dt, end_dt):
    """Devuelve (puntos, moneda, nombre, estimado). Si Meta no da el costo
    (cuentas facturadas vía partner), pide solo el volumen y estima con RATES."""
    estimated = False
    try:
        data = _zernio_query(start_dt, end_dt, 'COST,VOLUME')
    except Exception as exc:
        if 'cost' not in str(exc).lower():
            raise
        data = _zernio_query(start_dt, end_dt, 'VOLUME')
        estimated = True
    if isinstance(data, dict) and isinstance(data.get('data'), dict) and 'dataPoints' not in data:
        data = data['data']
    pts = []
    for p in (data.get('dataPoints') or []):
        cat = str(p.get('pricingCategory') or '').upper()
        ptype = str(p.get('pricingType') or '').upper()
        cost = p.get('cost')
        if estimated or cost is None:
            free = ptype.startswith('FREE')
            cost = 0.0 if free else _num(p.get('volume')) * RATES.get(cat, 0.0)
        pts.append({'start': p.get('start'), 'cost': cost, 'volume': p.get('volume'),
                    'pricing_category': cat, 'pricing_type': ptype})
    return pts, 'USD', 'Zernio · +' + str(data.get('phoneNumber') or ''), estimated


def _points(start_ts, end_ts):
    field = ('pricing_analytics.start(%d).end(%d).granularity(DAILY)'
             '.metric_types(["COST","VOLUME"])'
             '.dimensions(["PRICING_CATEGORY","PRICING_TYPE"])') % (start_ts, end_ts)
    data = _get(waba_id(), {'fields': field + ',currency,name'})
    pa = data.get('pricing_analytics') or {}
    blocks = pa.get('data') if isinstance(pa, dict) else pa
    pts = []
    for block in blocks or []:
        if isinstance(block, dict) and 'data_points' in block:
            pts.extend(block.get('data_points') or [])
        elif isinstance(block, dict):
            pts.append(block)
    return pts, data.get('currency') or 'USD', data.get('name') or ''


def _day(value):
    """Fecha local de un punto: Meta devuelve epoch (int) o ISO."""
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).date()
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(value or '')[:10]).date()


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _zernio_spend():
    """Lo que factura Zernio (cuentas, número, etc.): dato real de su API de consumo."""
    z = _zernio()
    if not z:
        return None
    try:
        out = {}
        for key, rng in (('cycle', 'cycle'), ('last30', '30d')):
            d = z._request('GET', '/v1/usage', headers=z._headers(),
                           params={'range': rng, 'granularity': 'total'}) or {}
            items = d.get('lineItems') or []
            out[key] = round(sum(_num(i.get('totalUsd')) for i in items), 2)
            if key == 'cycle':
                out['items'] = [{'name': i.get('name') or i.get('product') or '',
                                 'usd': round(_num(i.get('totalUsd')), 2)}
                                for i in items if _num(i.get('totalUsd'))]
        return out
    except Exception as exc:
        return {'error': str(exc)[:200]}


def _build():
    now = datetime.now(timezone.utc)
    today = now.date()
    month_start = today.replace(day=1)
    since = min(month_start, today - timedelta(days=30))
    start_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    estimated = False
    if source() == 'zernio':
        pts, currency, name, estimated = _points_zernio(start_dt, now)
    else:
        pts, currency, name = _points(int(start_dt.timestamp()), int(now.timestamp()))

    days, cats = {}, {}
    totals = {'today': 0.0, 'yesterday': 0.0, 'month': 0.0, 'last30': 0.0,
              'month_paid_msgs': 0, 'month_free_msgs': 0}
    for p in pts:
        d = _day(p.get('start'))
        cost, vol = _num(p.get('cost')), int(_num(p.get('volume')))
        cat = str(p.get('pricing_category') or 'OTRO').upper()
        free = str(p.get('pricing_type') or '').upper().startswith('FREE')
        day = days.setdefault(d, {'cost': 0.0, 'msgs': 0, 'free': 0})
        day['cost'] += cost
        day['msgs'] += vol
        if free:
            day['free'] += vol
        if d == today:
            totals['today'] += cost
        if d == today - timedelta(days=1):
            totals['yesterday'] += cost
        if d > today - timedelta(days=30):
            totals['last30'] += cost
        if d >= month_start:
            totals['month'] += cost
            c = cats.setdefault(cat, {'cost': 0.0, 'msgs': 0, 'free': 0})
            c['cost'] += cost
            c['msgs'] += vol
            if free:
                c['free'] += vol
                totals['month_free_msgs'] += vol
            else:
                totals['month_paid_msgs'] += vol

    elapsed = (today - month_start).days + 1
    dim = monthrange(today.year, today.month)[1]
    projection = totals['month'] / elapsed * dim if elapsed else 0.0
    by_cat = sorted(({'category': k, 'label': CATEGORY_LABELS.get(k, k.title()), **v}
                     for k, v in cats.items()), key=lambda x: -x['cost'])
    day_rows = [{'day': d.isoformat(), **days[d]} for d in sorted(days, reverse=True)[:14]]
    return {
        'configured': True,
        'currency': currency,
        'account': name,
        'month_label': now.strftime('%m/%Y'),
        'today': round(totals['today'], 4),
        'yesterday': round(totals['yesterday'], 4),
        'month': round(totals['month'], 4),
        'last30': round(totals['last30'], 4),
        'projection': round(projection, 4),
        'month_paid_msgs': totals['month_paid_msgs'],
        'month_free_msgs': totals['month_free_msgs'],
        'by_category': by_cat,
        'days': day_rows,
        'source': source(),
        'zernio': _zernio_spend(),
        'estimated': estimated,
        'rates': {k: RATES[k] for k in ('MARKETING', 'UTILITY', 'AUTHENTICATION')},
        'updated_at': now.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M'),
    }


def summary(force=False):
    """Resumen de costos con caché de 30 min (Meta actualiza estos datos con retraso)."""
    if not is_configured():
        return {'configured': False}
    key = source() or ''
    with _lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = _build()
    with _lock:
        _cache[key] = (time.time(), data)
    return data
