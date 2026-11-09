"""
Instagram Studio — analítica, agente de comentarios y estrategia semanal.

Conecta las cuentas con *Instagram API with Instagram Login* (graph.instagram.com),
guarda un snapshot diario de seguidores (Meta no da historial largo), sincroniza
publicaciones e insights, responde comentarios con Claude y genera cada lunes un
reporte con plan de contenido.

Variables de entorno:
  IG_APP_ID / IG_APP_SECRET   app de Instagram dentro de la app de Meta "Papia Tech CRM"
  IG_REDIRECT_URI             https://datos.papiatech.com/instagram-studio/oauth/callback
  IG_GRAPH_VERSION            v25.0 por defecto (la misma del webhook)
  IG_STUDIO_MODEL             modelo para comentarios (claude-haiku-5-5)
  IG_STUDIO_STRATEGY_MODEL    modelo para la estrategia semanal (claude-sonnet-5-5)
  IG_STUDIO_REPORT_EMAIL      a dónde se manda el reporte semanal
  ANTHROPIC_API_KEY           la misma que usa el bot de WhatsApp
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from database import get_db

log = logging.getLogger(__name__)

MIAMI = ZoneInfo('America/New_York')
ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
SCOPES = [
    'instagram_business_basic',
    'instagram_business_manage_messages',
    'instagram_business_manage_comments',
    'instagram_business_manage_insights',
]
DAYS_ES = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM']
WEEKDAYS_LONG = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
FORMAT_LABELS = {'REELS': 'Reels', 'CAROUSEL_ALBUM': 'Carruseles', 'IMAGE': 'Fotos', 'VIDEO': 'Videos'}
SERVICE_MAP = {
    'web': 'website', 'website': 'website', 'pagina': 'website', 'página': 'website',
    'landing': 'website', 'tienda': 'website', 'shopify': 'website', 'ecommerce': 'website',
    'crm': 'crm', 'app': 'mobile_app', 'mobile_app': 'mobile_app', 'aplicacion': 'mobile_app',
    'aplicación': 'mobile_app', 'consultoria': 'consulting', 'consultoría': 'consulting',
    'consulting': 'consulting',
}
DEFAULT_STRATEGY_CONTEXT = (
    "Henrry publica contenido educativo de tecnología e IA para dueños de negocios latinos.\n"
    "Fijo cada semana: carrusel de noticias de IA/tecnología el miércoles a las 8:30 PM en @henmartin14.\n"
    "Los carruseles llevan foto real de Henrry junto a su avatar 3D.\n"
    "El contenido de PapiaTech va a @henmartin14 y a @papiatech."
)

# Acciones posibles del agente de comentarios
ACTIONS = {
    'responder': 'replied',
    'responder_dm': 'replied_dm',
    'revisar': 'review',
    'ignorar': 'ignored',
}
STATUS_LABELS = {
    'pending': 'Pendiente',
    'replied': 'Respondió',
    'replied_dm': 'Respondió + DM',
    'review': 'Revisar',
    'ignored': 'Ignoró',
    'manual': 'Respondiste tú',
    'off': 'Agente apagado',
    'error': 'Error',
}

DECIDE_TOOL = {
    'name': 'decidir_accion',
    'description': 'Decide qué hacer con un comentario de Instagram y redacta los textos.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'categoria': {'type': 'string',
                          'enum': ['positivo', 'info', 'precio', 'pregunta', 'critica', 'spam', 'fuera_de_tema']},
            'accion': {'type': 'string', 'enum': list(ACTIONS.keys()),
                       'description': 'responder = solo respuesta pública; responder_dm = respuesta pública + DM; '
                                      'revisar = crítica o duda difícil, deja borrador para Henrry; ignorar = spam, '
                                      'negativo sin fondo o fuera de tema.'},
            'respuesta_publica': {'type': 'string', 'description': 'Respuesta corta al comentario (1-2 líneas).'},
            'mensaje_dm': {'type': 'string', 'description': 'Primer DM privado (solo si accion = responder_dm).'},
            'borrador': {'type': 'string', 'description': 'Borrador sugerido para Henrry (solo si accion = revisar).'},
            'crear_lead': {'type': 'boolean', 'description': 'true si la persona muestra intención de comprar.'},
            'servicio': {'type': 'string', 'description': 'website, crm, mobile_app, consulting u other'},
        },
        'required': ['categoria', 'accion'],
    },
}

REPORT_TOOL = {
    'name': 'reporte_semanal',
    'description': 'Reporte semanal de Instagram con plan de contenido para la semana que empieza.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'resumen': {'type': 'string', 'description': '2-3 frases: lo más importante de la semana pasada.'},
            'que_funciono': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4},
            'que_no_funciono': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4},
            'preguntas_frecuentes': {
                'type': 'array', 'maxItems': 6,
                'items': {'type': 'object', 'properties': {'tema': {'type': 'string'}, 'veces': {'type': 'integer'}},
                          'required': ['tema', 'veces']}},
            'insights': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3,
                         'description': '3 hallazgos accionables, una frase cada uno.'},
            'plan': {
                'type': 'array', 'minItems': 5, 'maxItems': 12,
                'items': {'type': 'object', 'properties': {
                    'dia': {'type': 'string', 'enum': DAYS_ES},
                    'tipo': {'type': 'string', 'enum': ['REEL', 'CARRUSEL', 'POST', 'HISTORIA']},
                    'hora': {'type': 'string', 'description': 'Hora de Miami, ej. 8:30 PM'},
                    'titulo': {'type': 'string'},
                    'hook': {'type': 'string', 'description': 'Primera frase / gancho'},
                    'cuenta': {'type': 'string', 'description': '@henmartin14, @papiatech o ambas'},
                }, 'required': ['dia', 'tipo', 'hora', 'titulo', 'hook', 'cuenta']}},
        },
        'required': ['resumen', 'que_funciono', 'que_no_funciono', 'preguntas_frecuentes', 'insights', 'plan'],
    },
}


class GraphError(RuntimeError):
    pass


# ── Utilidades ──────────────────────────────────────────────────────────────

def _env(*names, default=''):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


def graph_base():
    return f"https://graph.instagram.com/{_env('IG_GRAPH_VERSION', default='v25.0')}"


def now_utc_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def today_miami():
    return datetime.now(MIAMI).date()


def _parse_ts(value):
    """ISO de Meta ('2026-11-05T01:30:00+0000') o 'YYYY-MM-DD HH:MM:SS' (UTC) → datetime aware."""
    if not value:
        return None
    s = str(value)
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(s[:24] if 'T' in s else s[:19], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _setting(key, default=''):
    db = get_db()
    try:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        db.close()


def set_setting(key, value):
    db = get_db()
    try:
        db.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        db.commit()
    finally:
        db.close()


def strategy_context(org_id=1):
    return _setting(f'ig_studio_strategy_context_{org_id}', DEFAULT_STRATEGY_CONTEXT)


# ── Graph API ───────────────────────────────────────────────────────────────

def _graph(method, path, token, params=None, data=None, json_body=None, timeout=20):
    url = path if path.startswith('http') else f"{graph_base()}/{path.lstrip('/')}"
    params = dict(params or {})
    params['access_token'] = token
    resp = requests.request(method, url, params=params, data=data, json=json_body, timeout=timeout)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or (isinstance(body, dict) and body.get('error')):
        err = (body.get('error') or {}) if isinstance(body, dict) else {}
        raise GraphError(f"{resp.status_code} {err.get('message') or resp.text[:200]}")
    return body


def authorize_url(state):
    return 'https://www.instagram.com/oauth/authorize?' + urlencode({
        'client_id': _env('IG_APP_ID', 'INSTAGRAM_APP_ID'),
        'redirect_uri': redirect_uri(),
        'response_type': 'code',
        'scope': ','.join(SCOPES),
        'state': state,
        'enable_fb_login': '0',
        'force_reauth': 'true',
    })


def redirect_uri():
    return _env('IG_REDIRECT_URI', default='https://datos.papiatech.com/instagram-studio/oauth/callback')


def is_configured():
    return bool(_env('IG_APP_ID', 'INSTAGRAM_APP_ID') and _env('IG_APP_SECRET', 'INSTAGRAM_APP_SECRET'))


def exchange_code(code):
    """code → token largo (60 días). Devuelve (token, expires_in, scopes)."""
    resp = requests.post('https://api.instagram.com/oauth/access_token', data={
        'client_id': _env('IG_APP_ID', 'INSTAGRAM_APP_ID'),
        'client_secret': _env('IG_APP_SECRET', 'INSTAGRAM_APP_SECRET'),
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri(),
        'code': code.split('#')[0],
    }, timeout=20)
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise GraphError(body.get('error_message') or body.get('error', {}).get('message') or resp.text[:200])
    short = body.get('access_token') or (body.get('data') or [{}])[0].get('access_token')
    perms = body.get('permissions') or (body.get('data') or [{}])[0].get('permissions') or ''
    long = _graph('GET', 'https://graph.instagram.com/access_token', short, params={
        'grant_type': 'ig_exchange_token',
        'client_secret': _env('IG_APP_SECRET', 'INSTAGRAM_APP_SECRET'),
    })
    scopes = ','.join(perms) if isinstance(perms, list) else str(perms)
    return long['access_token'], int(long.get('expires_in') or 5184000), scopes


def save_account(org_id, token, expires_in, scopes=''):
    me = _graph('GET', 'me', token, params={
        'fields': 'user_id,username,name,profile_picture_url,followers_count,follows_count,media_count'})
    ig_id = str(me.get('user_id') or me.get('id'))
    expires = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    try:
        db.execute("""INSERT INTO ig_accounts (org_id, ig_user_id, username, name, picture_url, access_token,
                                                token_expires_at, scopes, is_active, updated_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                      ON CONFLICT(org_id, ig_user_id) DO UPDATE SET
                        username=excluded.username, name=excluded.name, picture_url=excluded.picture_url,
                        access_token=excluded.access_token, token_expires_at=excluded.token_expires_at,
                        scopes=excluded.scopes, is_active=1, last_error='', updated_at=CURRENT_TIMESTAMP""",
                   (org_id, ig_id, me.get('username', ''), me.get('name', ''),
                    me.get('profile_picture_url', ''), token, expires, scopes))
        db.commit()
    finally:
        db.close()
    _save_snapshot(org_id, ig_id, me)
    try:
        # Suscribe la cuenta a los webhooks de comentarios y mensajes
        _graph('POST', 'me/subscribed_apps', token, params={'subscribed_fields': 'comments,messages'})
    except GraphError as exc:
        log.warning('Instagram Studio: no se pudo suscribir @%s a webhooks: %s', me.get('username'), exc)
    return ig_id, me.get('username', '')


def list_accounts(org_id, active_only=True):
    db = get_db()
    try:
        sql = "SELECT * FROM ig_accounts WHERE org_id=?" + (" AND is_active=1" if active_only else "")
        return [dict(r) for r in db.execute(sql + " ORDER BY username", (org_id,)).fetchall()]
    finally:
        db.close()


def get_account(org_id, ig_user_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM ig_accounts WHERE org_id=? AND ig_user_id=?",
                         (org_id, str(ig_user_id))).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def disconnect_account(org_id, ig_user_id):
    db = get_db()
    try:
        db.execute("UPDATE ig_accounts SET is_active=0 WHERE org_id=? AND ig_user_id=?", (org_id, str(ig_user_id)))
        db.commit()
    finally:
        db.close()


def refresh_tokens(days_before=15):
    """Renueva tokens que vencen pronto. Correr a diario."""
    limit = (datetime.now(timezone.utc) + timedelta(days=days_before)).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    rows = db.execute("SELECT * FROM ig_accounts WHERE is_active=1 AND (token_expires_at IS NULL OR token_expires_at < ?)",
                      (limit,)).fetchall()
    db.close()
    done = 0
    for acct in rows:
        try:
            data = _graph('GET', 'https://graph.instagram.com/refresh_access_token', acct['access_token'],
                          params={'grant_type': 'ig_refresh_token'})
            exp = (datetime.now(timezone.utc) + timedelta(seconds=int(data.get('expires_in') or 5184000)))
            db = get_db()
            db.execute("UPDATE ig_accounts SET access_token=?, token_expires_at=?, last_error='' WHERE id=?",
                       (data['access_token'], exp.strftime('%Y-%m-%d %H:%M:%S'), acct['id']))
            db.commit()
            db.close()
            done += 1
        except Exception as exc:  # token caducado: hay que reconectar
            _mark_error(acct['id'], f'Token: {exc}')
    return done


def _mark_error(acct_id, msg):
    db = get_db()
    try:
        db.execute("UPDATE ig_accounts SET last_error=? WHERE id=?", (str(msg)[:300], acct_id))
        db.commit()
    finally:
        db.close()


# ── Sincronización ──────────────────────────────────────────────────────────

def _save_snapshot(org_id, ig_user_id, profile, reach=None, profile_views=None):
    day = today_miami().isoformat()
    db = get_db()
    try:
        db.execute("""INSERT INTO ig_account_daily (org_id, ig_user_id, day, followers, follows, media_count,
                                                    reach, profile_views)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                      ON CONFLICT(org_id, ig_user_id, day) DO UPDATE SET
                        followers=excluded.followers, follows=excluded.follows, media_count=excluded.media_count,
                        reach=COALESCE(excluded.reach, ig_account_daily.reach),
                        profile_views=COALESCE(excluded.profile_views, ig_account_daily.profile_views)""",
                   (org_id, str(ig_user_id), day, profile.get('followers_count'), profile.get('follows_count'),
                    profile.get('media_count'), reach, profile_views))
        db.commit()
    finally:
        db.close()


def _account_day_metric(token, metric):
    """Valor de ayer (día completo) de una métrica de cuenta. None si Meta no la da."""
    since = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    try:
        data = _graph('GET', 'me/insights', token, params={
            'metric': metric, 'period': 'day', 'metric_type': 'total_value', 'since': since, 'until': until})
        item = (data.get('data') or [{}])[0]
        if item.get('total_value'):
            return item['total_value'].get('value')
        values = item.get('values') or []
        return values[-1].get('value') if values else None
    except GraphError:
        return None


def _media_insights(token, media):
    """Pide las métricas en bloque; si Meta rechaza alguna, reintenta con un set básico."""
    sets = ['views,reach,saved,shares,total_interactions', 'reach,saved,shares,total_interactions',
            'reach,saved,total_interactions']
    for metrics in sets:
        try:
            data = _graph('GET', f"{media['id']}/insights", token, params={'metric': metrics})
            out = {}
            for item in data.get('data') or []:
                vals = item.get('values') or [{}]
                out[item.get('name')] = (item.get('total_value') or {}).get('value', vals[0].get('value'))
            return out
        except GraphError:
            continue
    return {}


def sync_account(acct, media_limit=60):
    """Perfil + snapshot diario + publicaciones recientes con insights."""
    token = acct['access_token']
    org_id = acct['org_id']
    me = _graph('GET', 'me', token, params={
        'fields': 'user_id,username,name,profile_picture_url,followers_count,follows_count,media_count'})
    reach = _account_day_metric(token, 'reach')
    views = _account_day_metric(token, 'profile_views')
    _save_snapshot(org_id, acct['ig_user_id'], me, reach, views)

    fields = ('id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp,'
              'like_count,comments_count')
    url, params, items = 'me/media', {'fields': fields, 'limit': 30}, []
    while url and len(items) < media_limit:
        page = _graph('GET', url, token, params=params)
        items.extend(page.get('data') or [])
        url = (page.get('paging') or {}).get('next')
        params = {}
    stale = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')
    recent = datetime.now(timezone.utc) - timedelta(days=90)
    db = get_db()
    try:
        for m in items[:media_limit]:
            product = m.get('media_product_type') or ''
            mtype = 'REELS' if product == 'REELS' else (m.get('media_type') or '')
            db.execute("""INSERT INTO ig_media (media_id, org_id, ig_user_id, caption, media_type, product_type,
                                                permalink, thumb_url, posted_at, like_count, comments_count)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(media_id) DO UPDATE SET caption=excluded.caption,
                            media_type=excluded.media_type, product_type=excluded.product_type,
                            permalink=excluded.permalink, thumb_url=excluded.thumb_url,
                            posted_at=excluded.posted_at, like_count=excluded.like_count,
                            comments_count=excluded.comments_count""",
                       (m['id'], org_id, acct['ig_user_id'], (m.get('caption') or '')[:2000], mtype, product,
                        m.get('permalink', ''), m.get('thumbnail_url') or m.get('media_url') or '',
                        _ts_to_db(m.get('timestamp')), m.get('like_count') or 0, m.get('comments_count') or 0))
        db.commit()
        for m in items[:media_limit]:
            row = db.execute("SELECT insights_at, posted_at FROM ig_media WHERE media_id=?", (m['id'],)).fetchone()
            posted = _parse_ts(row['posted_at'])
            if row['insights_at'] and (row['insights_at'] > stale or (posted and posted < recent)):
                continue
            ins = _media_insights(token, m)
            if ins:
                db.execute("""UPDATE ig_media SET views=?, reach=?, saved=?, shares=?, total_interactions=?,
                                                  insights_at=? WHERE media_id=?""",
                           (ins.get('views'), ins.get('reach'), ins.get('saved'), ins.get('shares'),
                            ins.get('total_interactions'), now_utc_str(), m['id']))
                db.commit()
        db.execute("UPDATE ig_accounts SET last_sync_at=?, last_error='', username=?, picture_url=? WHERE id=?",
                   (now_utc_str(), me.get('username') or acct['username'], me.get('profile_picture_url') or '',
                    acct['id']))
        db.commit()
    finally:
        db.close()
    return len(items)


def _ts_to_db(value):
    dt = _parse_ts(value)
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if dt else None


def sync_all(org_id=None):
    db = get_db()
    sql = "SELECT * FROM ig_accounts WHERE is_active=1" + (" AND org_id=?" if org_id else "")
    rows = [dict(r) for r in db.execute(sql, (org_id,) if org_id else ()).fetchall()]
    db.close()
    result = {}
    for acct in rows:
        try:
            result[acct['username']] = sync_account(acct)
        except Exception as exc:
            log.exception('Instagram Studio: sync de @%s falló', acct['username'])
            _mark_error(acct['id'], exc)
            result[acct['username']] = f'error: {exc}'
    return result


# ── Analítica (lo que pinta la pestaña) ─────────────────────────────────────

def _acct_filter(ig_user_id, col='ig_user_id'):
    return (f" AND {col}=?", (str(ig_user_id),)) if ig_user_id else ('', ())


def _er(row):
    reach = row['reach'] or 0
    inter = row['total_interactions']
    if inter is None:
        inter = (row['like_count'] or 0) + (row['comments_count'] or 0) + (row['saved'] or 0) + (row['shares'] or 0)
    return (inter / reach * 100) if reach else None


def overview(org_id, ig_user_id=None, days=30):
    since_day = (today_miami() - timedelta(days=days)).isoformat()
    since_ts = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    prev_ts = (datetime.now(timezone.utc) - timedelta(days=days * 2)).strftime('%Y-%m-%d %H:%M:%S')
    f, p = _acct_filter(ig_user_id)
    db = get_db()
    try:
        accts = [dict(r) for r in db.execute(
            "SELECT ig_user_id, username FROM ig_accounts WHERE org_id=? AND is_active=1" + f, (org_id, *p))]
        followers_now = followers_then = 0
        have_then = False
        for a in accts:
            last = db.execute("SELECT followers FROM ig_account_daily WHERE org_id=? AND ig_user_id=? "
                              "ORDER BY day DESC LIMIT 1", (org_id, a['ig_user_id'])).fetchone()
            first = db.execute("SELECT followers FROM ig_account_daily WHERE org_id=? AND ig_user_id=? AND day>=? "
                               "ORDER BY day ASC LIMIT 1", (org_id, a['ig_user_id'], since_day)).fetchone()
            followers_now += (last['followers'] or 0) if last else 0
            if first and first['followers'] is not None:
                followers_then += first['followers']
                have_then = True

        def media_totals(start, end=None):
            sql = ("SELECT * FROM ig_media WHERE org_id=? AND posted_at>=?" + (" AND posted_at<?" if end else "") + f)
            args = (org_id, start, end, *p) if end else (org_id, start, *p)
            rows = db.execute(sql, args).fetchall()
            reach = sum(r['reach'] or 0 for r in rows)
            inter = sum((r['total_interactions'] if r['total_interactions'] is not None else
                         (r['like_count'] or 0) + (r['comments_count'] or 0)) for r in rows)
            return reach, inter, len(rows)

        reach, inter, n_posts = media_totals(since_ts)
        reach_prev, inter_prev, _ = media_totals(prev_ts, since_ts)
        er = (inter / reach * 100) if reach else None
        er_prev = (inter_prev / reach_prev * 100) if reach_prev else None

        cf, cp = _acct_filter(ig_user_id)
        handled = db.execute(
            "SELECT status, COUNT(*) n FROM ig_comments WHERE org_id=? AND received_at>=?" + cf + " GROUP BY status",
            (org_id, since_ts, *cp)).fetchall()
        by_status = {r['status']: r['n'] for r in handled}
        auto = sum(by_status.get(s, 0) for s in ('replied', 'replied_dm', 'ignored'))
        total = sum(by_status.values())
    finally:
        db.close()
    return {
        'followers': followers_now,
        'followers_delta': (followers_now - followers_then) if have_then else None,
        'reach': reach,
        'reach_delta_pct': ((reach - reach_prev) / reach_prev * 100) if reach_prev else None,
        'er': er,
        'er_delta': (er - er_prev) if (er is not None and er_prev is not None) else None,
        'posts': n_posts,
        'ai_handled': auto,
        'ai_handled_pct': (auto / total * 100) if total else None,
        'comments_total': total,
    }


def growth_series(org_id, ig_user_id=None, days=30):
    since_day = (today_miami() - timedelta(days=days)).isoformat()
    f, p = _acct_filter(ig_user_id, 'd.ig_user_id')
    db = get_db()
    try:
        rows = db.execute(
            "SELECT d.ig_user_id, a.username, d.day, d.followers FROM ig_account_daily d "
            "JOIN ig_accounts a ON a.org_id=d.org_id AND a.ig_user_id=d.ig_user_id "
            "WHERE d.org_id=? AND d.day>=? AND a.is_active=1" + f + " ORDER BY d.day",
            (org_id, since_day, *p)).fetchall()
    finally:
        db.close()
    series = {}
    for r in rows:
        s = series.setdefault(r['ig_user_id'], {'username': r['username'], 'points': []})
        if r['followers'] is not None:
            s['points'].append({'day': r['day'], 'followers': r['followers']})
    return list(series.values())


def chart_svg_data(series, width=800, height=240, pad=12):
    """Convierte las series a coordenadas para un <polyline> sin librerías de gráficos.

    Con una cuenta se dibujan los seguidores totales. Con varias, los seguidores ganados desde el
    primer día del periodo: si no, la cuenta grande aplasta a la pequeña y ninguna se ve crecer.
    """
    relative = len([s for s in series if s['points']]) > 1
    if relative:
        series = [{**s, 'points': [{'day': p['day'], 'followers': p['followers'] - s['points'][0]['followers'],
                                    'total': p['followers']} for p in s['points']]} for s in series if s['points']]
    days = sorted({p['day'] for s in series for p in s['points']})
    values = [p['followers'] for s in series for p in s['points']]
    if not days or not values:
        return {'lines': [], 'labels': [], 'ticks': [], 'relative': False}
    lo, hi = min(values), max(values)
    if hi == lo:
        hi, lo = hi + 1, max(lo - 1, 0)
    span_x = max(len(days) - 1, 1)
    idx = {d: i for i, d in enumerate(days)}
    lines = []
    for s in series:
        pts = [(idx[p['day']] / span_x * width,
                pad + (hi - p['followers']) / (hi - lo) * (height - 2 * pad)) for p in s['points']]
        last = s['points'][-1] if s['points'] else {}
        lines.append({'username': s['username'], 'last': last.get('total', last.get('followers', 0)),
                      'gained': last.get('followers', 0) if relative else None,
                      'points': ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)})
    step = max(len(days) // 4, 1)
    labels = [days[i] for i in range(0, len(days), step)]
    if labels[-1] != days[-1]:
        labels.append(days[-1])
    ticks = [round(hi - (hi - lo) * i / 3) for i in range(4)]
    return {'lines': lines, 'labels': labels, 'ticks': ticks, 'relative': relative}


def format_performance(org_id, ig_user_id=None, days=90):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    f, p = _acct_filter(ig_user_id)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM ig_media WHERE org_id=? AND posted_at>=?" + f, (org_id, since, *p)).fetchall()
    finally:
        db.close()
    groups = {}
    for r in rows:
        er = _er(r)
        if er is None:
            continue
        groups.setdefault(FORMAT_LABELS.get(r['media_type'], r['media_type'] or 'Otros'), []).append(er)
    out = [{'name': k, 'rate': sum(v) / len(v), 'posts': len(v)} for k, v in groups.items()]
    out.sort(key=lambda x: -x['rate'])
    top = out[0]['rate'] if out else 1
    for o in out:
        o['pct'] = round(o['rate'] / top * 100) if top else 0
    return out


def best_slots(org_id, ig_user_id=None, days=90, limit=3):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    f, p = _acct_filter(ig_user_id)
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM ig_media WHERE org_id=? AND posted_at>=?" + f, (org_id, since, *p)).fetchall()
    finally:
        db.close()
    slots = {}
    for r in rows:
        er, dt = _er(r), _parse_ts(r['posted_at'])
        if er is None or not dt:
            continue
        local = dt.astimezone(MIAMI)
        slots.setdefault((local.weekday(), local.hour), []).append((er, FORMAT_LABELS.get(r['media_type'], '')))
    ranked = sorted(slots.items(), key=lambda kv: -sum(e for e, _ in kv[1]) / len(kv[1]))[:limit]
    out = []
    for (wd, hour), vals in ranked:
        h12 = datetime(2000, 1, 1, hour).strftime('%I:00 %p').lstrip('0')
        fmt = max({v[1] for v in vals}, key=lambda x: sum(1 for v in vals if v[1] == x))
        out.append({'when': f'{WEEKDAYS_LONG[wd]} {h12}', 'why': f'{fmt} · {sum(e for e, _ in vals) / len(vals):.1f}%'})
    return out


def top_posts(org_id, ig_user_id=None, order='er', limit=8, days=90):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    f, p = _acct_filter(ig_user_id, 'm.ig_user_id')
    db = get_db()
    try:
        rows = db.execute("SELECT m.*, a.username FROM ig_media m LEFT JOIN ig_accounts a "
                          "ON a.org_id=m.org_id AND a.ig_user_id=m.ig_user_id "
                          "WHERE m.org_id=? AND m.posted_at>=?" + f, (org_id, since, *p)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d['er'] = _er(r)
        d['format'] = FORMAT_LABELS.get(r['media_type'], r['media_type'] or '')
        d['title'] = (r['caption'] or '').split('\n')[0][:90] or '(sin texto)'
        out.append(d)
    key = {'views': lambda x: x['views'] or 0, 'saved': lambda x: x['saved'] or 0}.get(order, lambda x: x['er'] or 0)
    out.sort(key=key, reverse=True)
    return out[:limit]


def latest_report(org_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM ig_weekly_reports WHERE org_id=? ORDER BY week_start DESC LIMIT 1",
                         (org_id,)).fetchone()
        if not row:
            return None
        rep = dict(row)
        rep['data'] = json.loads(rep['data_json'] or '{}')
        rep['items'] = [dict(r) for r in db.execute(
            "SELECT * FROM ig_plan_items WHERE report_id=? ORDER BY id", (rep['id'],)).fetchall()]
        return rep
    finally:
        db.close()


# ── Agentes ─────────────────────────────────────────────────────────────────

def list_agents(org_id):
    db = get_db()
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM ig_agents WHERE org_id=? ORDER BY kind, id", (org_id,))]
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        for a in rows:
            s = db.execute("SELECT COUNT(*) n, SUM(status='error') e, AVG(latency_ms) l FROM ig_agent_runs "
                           "WHERE agent_id=? AND created_at>=?", (a['id'], since)).fetchone()
            a['runs_24h'] = s['n'] or 0
            a['error_pct'] = ((s['e'] or 0) / s['n'] * 100) if s['n'] else 0
            a['latency_s'] = (s['l'] or 0) / 1000
        return rows
    finally:
        db.close()


def get_agent(org_id, agent_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM ig_agents WHERE org_id=? AND id=?", (org_id, agent_id)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


AGENT_FIELDS = ('name', 'prompt', 'ig_user_id', 'enabled', 'reply_delay', 'cancel_on_new',
                'business_hours', 'auto_dm', 'create_leads', 'model')


def save_agent(org_id, agent_id, data, kind='comments'):
    vals = {}
    for k in AGENT_FIELDS:
        if k in data:
            v = data[k]
            if k in ('enabled', 'cancel_on_new', 'business_hours', 'auto_dm', 'create_leads'):
                v = 1 if v in (True, 1, '1', 'true', 'on') else 0
            elif k == 'reply_delay':
                v = max(0, min(int(v or 0), 600))
            else:
                v = str(v or '').strip()[:6000]
            vals[k] = v
    if 'name' in vals and not vals['name']:
        raise ValueError('El agente necesita un nombre.')
    db = get_db()
    try:
        if agent_id:
            if not db.execute("SELECT 1 FROM ig_agents WHERE org_id=? AND id=?", (org_id, agent_id)).fetchone():
                raise ValueError('Agente no encontrado.')
            if vals:
                sets = ', '.join(f'{k}=?' for k in vals)
                db.execute(f"UPDATE ig_agents SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=? AND org_id=?",
                           (*vals.values(), agent_id, org_id))
        else:
            vals.setdefault('name', 'Nuevo agente')
            cols = ', '.join(['org_id', 'kind', *vals.keys()])
            marks = ', '.join('?' * (len(vals) + 2))
            agent_id = db.execute(f"INSERT INTO ig_agents ({cols}) VALUES ({marks})",
                                  (org_id, kind if kind in ('comments', 'dm') else 'comments',
                                   *vals.values())).lastrowid
        if data.get('is_default') in (True, 1, '1', 'true', 'on'):
            db.execute("UPDATE ig_agents SET is_default=0 WHERE org_id=? AND kind='comments'", (org_id,))
            db.execute("UPDATE ig_agents SET is_default=1 WHERE org_id=? AND id=?", (org_id, agent_id))
        db.commit()
        return agent_id
    finally:
        db.close()


def dm_prompt_for(org_id, ig_user_id):
    """Prompt del agente de DMs de una cuenta: lo puede leer el asistente del Inbox."""
    db = get_db()
    try:
        row = db.execute("SELECT prompt FROM ig_agents WHERE org_id=? AND kind='dm' AND enabled=1 "
                         "AND ig_user_id IN (?, '') ORDER BY ig_user_id DESC LIMIT 1",
                         (org_id, str(ig_user_id))).fetchone()
        return row['prompt'] if row else None
    finally:
        db.close()


def set_media_agent(org_id, media_id, agent_id=None, off=False):
    db = get_db()
    try:
        cur = db.execute("UPDATE ig_media SET agent_id=?, agent_off=? WHERE org_id=? AND media_id=?",
                         (agent_id or None, 1 if off else 0, org_id, media_id))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def _agent_for(db, org_id, ig_user_id, media_id):
    media = db.execute("SELECT agent_id, agent_off FROM ig_media WHERE media_id=?", (media_id,)).fetchone()
    if media and media['agent_off']:
        return None, 'off'
    if media and media['agent_id']:
        row = db.execute("SELECT * FROM ig_agents WHERE id=? AND org_id=?", (media['agent_id'], org_id)).fetchone()
        if row:
            return dict(row), None
    row = db.execute("SELECT * FROM ig_agents WHERE org_id=? AND kind='comments' AND is_default=1 "
                     "AND ig_user_id IN (?, '') ORDER BY ig_user_id DESC LIMIT 1",
                     (org_id, str(ig_user_id))).fetchone()
    return (dict(row), None) if row else (None, 'off')


def _in_business_hours():
    n = datetime.now(MIAMI)
    return n.weekday() < 6 and 9 <= n.hour < 19


# ── Webhook de comentarios ──────────────────────────────────────────────────

def handle_webhook(data, background=True):
    """Llamar desde /webhook/meta después de verificar la firma. Devuelve ids guardados."""
    saved = []
    for entry in (data or {}).get('entry', []):
        ig_id = str(entry.get('id', ''))
        for change in entry.get('changes', []) or []:
            if change.get('field') != 'comments':
                continue
            v = change.get('value') or {}
            saved_id = store_comment(ig_id, v)
            if saved_id:
                saved.append(saved_id)
    for cid in saved:
        if background:
            threading.Thread(target=_process_safe, args=(cid,), daemon=True).start()
        else:
            _process_safe(cid, delay=False)
    return saved


def store_comment(ig_id, v):
    frm = v.get('from') or {}
    if not v.get('id') or str(frm.get('id', '')) == ig_id:
        return None  # sin id o es nuestra propia respuesta
    db = get_db()
    try:
        acct = db.execute("SELECT org_id, username FROM ig_accounts WHERE ig_user_id=? AND is_active=1",
                          (ig_id,)).fetchone()
        if not acct or (frm.get('username') or '').lower() == (acct['username'] or '').lower():
            return None
        media = v.get('media') or {}
        media_id = str(media.get('id', ''))
        if media_id:
            db.execute("""INSERT OR IGNORE INTO ig_media (media_id, org_id, ig_user_id, product_type, is_ad)
                          VALUES (?, ?, ?, ?, ?)""",
                       (media_id, acct['org_id'], ig_id, media.get('media_product_type', ''),
                        1 if (media.get('ad_id') or media.get('media_product_type') == 'AD') else 0))
        cur = db.execute("""INSERT OR IGNORE INTO ig_comments (org_id, ig_user_id, comment_id, media_id, parent_id,
                                                               from_id, from_username, text)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                         (acct['org_id'], ig_id, str(v['id']), media_id, str(v.get('parent_id') or ''),
                          str(frm.get('id', '')), frm.get('username', ''), (v.get('text') or '')[:2000]))
        db.commit()
        return str(v['id']) if cur.rowcount else None
    finally:
        db.close()


def _process_safe(comment_id, delay=True):
    try:
        process_comment(comment_id, delay=delay)
    except Exception:
        log.exception('Instagram Studio: error procesando comentario %s', comment_id)


def _load_comment(comment_id):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM ig_comments WHERE comment_id=?", (str(comment_id),)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _update_comment(comment_id, **fields):
    fields.setdefault('processed_at', now_utc_str())
    db = get_db()
    try:
        sets = ', '.join(f'{k}=?' for k in fields)
        db.execute(f"UPDATE ig_comments SET {sets} WHERE comment_id=?", (*fields.values(), str(comment_id)))
        db.commit()
    finally:
        db.close()


def _log_run(org_id, agent_id, channel, ig_user_id, text, action, started, status='ok', error='', usage=None):
    usage = usage or {}
    db = get_db()
    try:
        db.execute("""INSERT INTO ig_agent_runs (org_id, agent_id, channel, ig_user_id, input, action, latency_ms,
                                                 status, error, tokens_in, tokens_out)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (org_id, agent_id, channel, ig_user_id, (text or '')[:500], action,
                    int((time.monotonic() - started) * 1000), status, str(error)[:500],
                    int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0)))
        db.commit()
    finally:
        db.close()


def process_comment(comment_id, delay=True):
    c = _load_comment(comment_id)
    if not c or c['status'] != 'pending':
        return c and c['status']
    db = get_db()
    try:
        agent, reason = _agent_for(db, c['org_id'], c['ig_user_id'], c['media_id'])
        acct = db.execute("SELECT * FROM ig_accounts WHERE org_id=? AND ig_user_id=?",
                          (c['org_id'], c['ig_user_id'])).fetchone()
        caption = db.execute("SELECT caption FROM ig_media WHERE media_id=?", (c['media_id'],)).fetchone()
    finally:
        db.close()
    if not agent or not agent['enabled'] or not acct:
        _update_comment(comment_id, status='off')
        return 'off'
    if agent['business_hours'] and not _in_business_hours():
        return 'pending'  # lo retoma el job cuando abra el horario
    if delay and agent['reply_delay']:
        time.sleep(min(agent['reply_delay'], 120))
    if agent['cancel_on_new']:
        newer = _newer_from_same_user(c)
        if newer:
            _update_comment(comment_id, status='ignored', category='agrupado')
            return 'ignored'

    started = time.monotonic()
    try:
        decision, usage, model = decide(agent, acct and acct['username'], c, (caption or {'caption': ''})['caption'],
                                        _earlier_from_same_user(c))
    except Exception as exc:
        _update_comment(comment_id, status='error', error=str(exc)[:300])
        _log_run(c['org_id'], agent['id'], 'comment', c['ig_user_id'], c['text'], 'Error de Claude', started,
                 'error', exc)
        return 'error'

    action = decision.get('accion', 'revisar')
    status = ACTIONS.get(action, 'review')
    reply = (decision.get('respuesta_publica') or '').strip()
    dm = (decision.get('mensaje_dm') or '').strip()
    fields = {'category': decision.get('categoria', ''), 'agent_id': agent['id']}
    try:
        if status in ('replied', 'replied_dm') and reply:
            reply_to_comment(acct['access_token'], comment_id, reply)
            fields['reply_text'] = reply
        elif status in ('replied', 'replied_dm'):
            status = 'review'
        if status == 'replied_dm' and agent['auto_dm'] and dm:
            private_reply(acct['access_token'], comment_id, dm)
            fields['dm_text'] = dm
        elif status == 'replied_dm' and not dm:
            status = 'replied'
        if status == 'review':
            fields['draft_text'] = (decision.get('borrador') or reply)[:1000]
        if decision.get('crear_lead') and agent['create_leads'] and status != 'ignored':
            fields['client_id'] = ensure_lead(c, decision.get('servicio', ''))
    except GraphError as exc:
        fields.update(status='error', error=str(exc)[:300])
        _update_comment(comment_id, **fields)
        _log_run(c['org_id'], agent['id'], 'comment', c['ig_user_id'], c['text'], STATUS_LABELS.get(status, status),
                 started, 'error', exc, usage)
        return 'error'
    fields['status'] = status
    _update_comment(comment_id, **fields)
    _log_run(c['org_id'], agent['id'], 'comment', c['ig_user_id'], c['text'],
             STATUS_LABELS.get(status, status) + (' · lead' if fields.get('client_id') else ''),
             started, 'pending' if status == 'review' else 'ok', usage=usage)
    return status


def _newer_from_same_user(c):
    db = get_db()
    try:
        return db.execute("SELECT 1 FROM ig_comments WHERE from_id=? AND media_id=? AND id>? AND status='pending'",
                          (c['from_id'], c['media_id'], c['id'])).fetchone() is not None
    finally:
        db.close()


def _earlier_from_same_user(c):
    db = get_db()
    try:
        rows = db.execute("SELECT text FROM ig_comments WHERE from_id=? AND media_id=? AND id<? "
                          "ORDER BY id DESC LIMIT 3", (c['from_id'], c['media_id'], c['id'])).fetchall()
        return [r['text'] for r in reversed(rows)]
    finally:
        db.close()


def reply_to_comment(token, comment_id, text):
    return _graph('POST', f'{comment_id}/replies', token, params={'message': text[:2200]})


def private_reply(token, comment_id, text):
    """DM privado a quien comentó (una vez por comentario, dentro de 7 días)."""
    return _graph('POST', 'me/messages', token,
                  json_body={'recipient': {'comment_id': str(comment_id)}, 'message': {'text': text[:1000]}})


def ensure_lead(c, servicio=''):
    """Crea (o reutiliza) el lead del usuario de Instagram y le deja una nota con el comentario."""
    from models.client import add_note, create_client
    db = get_db()
    try:
        row = db.execute("SELECT client_id FROM ig_comments WHERE from_id=? AND org_id=? AND client_id IS NOT NULL "
                         "ORDER BY id DESC LIMIT 1", (c['from_id'], c['org_id'])).fetchone()
    finally:
        db.close()
    note = f"Comentario en Instagram (@{c['from_username']}): {c['text']}"
    if row:
        add_note(row['client_id'], 'note', note)
        return row['client_id']
    username = c['from_username'] or 'instagram'
    client_id = create_client({
        'first_name': username, 'last_name': '(Instagram)', 'email': '',
        'project_type': SERVICE_MAP.get((servicio or '').lower(), 'other'),
        'project_details': f"Lead de Instagram @{username}. {c['text']}"[:1000],
    }, org_id=c['org_id'])
    db = get_db()
    try:
        db.execute("UPDATE clients SET source='instagram' WHERE id=?", (client_id,))
        db.commit()
    except Exception:
        pass  # columna source solo existe tras la migración de leads
    finally:
        db.close()
    add_note(client_id, 'note', note)
    return client_id


# ── Acciones manuales desde la pestaña ──────────────────────────────────────

def manual_reply(org_id, comment_id, text, mark='manual'):
    c = _load_comment(comment_id)
    if not c or c['org_id'] != org_id:
        raise ValueError('Comentario no encontrado.')
    text = (text or '').strip()
    if not text:
        raise ValueError('Escribe la respuesta.')
    acct = get_account(org_id, c['ig_user_id'])
    if not acct:
        raise ValueError('La cuenta ya no está conectada.')
    reply_to_comment(acct['access_token'], comment_id, text)
    _update_comment(comment_id, status=mark, reply_text=text, error='')
    return True


def ignore_comment(org_id, comment_id):
    c = _load_comment(comment_id)
    if not c or c['org_id'] != org_id:
        raise ValueError('Comentario no encontrado.')
    _update_comment(comment_id, status='ignored', category=c['category'] or 'manual')


def retry_comment(org_id, comment_id):
    c = _load_comment(comment_id)
    if not c or c['org_id'] != org_id:
        raise ValueError('Comentario no encontrado.')
    _update_comment(comment_id, status='pending', error='', processed_at=None)
    return process_comment(comment_id, delay=False)


def process_pending(max_age_hours=48):
    """Red de seguridad: procesa comentarios que quedaron pendientes (hilo caído, fuera de horario)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime('%Y-%m-%d %H:%M:%S')
    old = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    ids = [r['comment_id'] for r in db.execute(
        "SELECT comment_id FROM ig_comments WHERE status='pending' AND received_at>=? AND received_at<=? ORDER BY id",
        (since, old)).fetchall()]
    db.close()
    return {cid: _process_safe_result(cid) for cid in ids}


def _process_safe_result(cid):
    try:
        return process_comment(cid, delay=False)
    except Exception as exc:
        return f'error: {exc}'


def list_comments(org_id, status=None, ig_user_id=None, limit=60):
    f, p = _acct_filter(ig_user_id, 'c.ig_user_id')
    s = (" AND c.status=?", (status,)) if status else ('', ())
    db = get_db()
    try:
        rows = db.execute(
            "SELECT c.*, m.caption, m.permalink, a.username AS account FROM ig_comments c "
            "LEFT JOIN ig_media m ON m.media_id=c.media_id "
            "LEFT JOIN ig_accounts a ON a.org_id=c.org_id AND a.ig_user_id=c.ig_user_id "
            "WHERE c.org_id=?" + f + s[0] + " ORDER BY c.received_at DESC, c.id DESC LIMIT ?",
            (org_id, *p, *s[1], limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['post_title'] = ((r['caption'] or '').split('\n')[0][:48]) or 'Publicación'
            d['status_label'] = STATUS_LABELS.get(r['status'], r['status'])
            out.append(d)
        return out
    finally:
        db.close()


def comment_stats(org_id, ig_user_id=None):
    start = datetime.now(MIAMI).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    f, p = _acct_filter(ig_user_id)
    db = get_db()
    try:
        r = db.execute(
            "SELECT COUNT(*) total, SUM(status IN ('replied','replied_dm')) replied, SUM(status='replied_dm') dms, "
            "SUM(client_id IS NOT NULL) leads, SUM(status='review') review FROM ig_comments "
            "WHERE org_id=? AND received_at>=?" + f, (org_id, start.strftime('%Y-%m-%d %H:%M:%S'), *p)).fetchone()
        return {k: (r[k] or 0) for k in ('total', 'replied', 'dms', 'leads', 'review')}
    finally:
        db.close()


MEDIA_BADGES = {'REELS': 'REEL', 'CAROUSEL_ALBUM': 'CARRUSEL', 'IMAGE': 'FOTO', 'VIDEO': 'VIDEO'}


def media_for_assignment(org_id, limit=9):
    db = get_db()
    try:
        rows = [dict(r) for r in db.execute(
            "SELECT m.*, g.name AS agent_name FROM ig_media m LEFT JOIN ig_agents g ON g.id=m.agent_id "
            "WHERE m.org_id=? ORDER BY m.is_ad DESC, m.posted_at DESC LIMIT ?", (org_id, limit)).fetchall()]
        for m in rows:
            m['badge'] = 'ANUNCIO' if m['is_ad'] else MEDIA_BADGES.get(m['media_type'], 'POST')
            m['title'] = ((m['caption'] or '').split('\n')[0])[:40] or 'Sin texto'
        return rows
    finally:
        db.close()


def recent_runs(org_id, limit=20):
    db = get_db()
    try:
        return [dict(r) for r in db.execute(
            "SELECT r.*, g.name AS agent_name, a.username FROM ig_agent_runs r "
            "LEFT JOIN ig_agents g ON g.id=r.agent_id "
            "LEFT JOIN ig_accounts a ON a.org_id=r.org_id AND a.ig_user_id=r.ig_user_id "
            "WHERE r.org_id=? ORDER BY r.id DESC LIMIT ?", (org_id, limit)).fetchall()]
    finally:
        db.close()


def runs_summary(org_id):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    try:
        r = db.execute("SELECT COUNT(*) n, SUM(status='error') e, AVG(latency_ms) l FROM ig_agent_runs "
                       "WHERE org_id=? AND created_at>=?", (org_id, since)).fetchone()
        return {'runs': r['n'] or 0, 'error_pct': ((r['e'] or 0) / r['n'] * 100) if r['n'] else 0,
                'latency_s': (r['l'] or 0) / 1000}
    finally:
        db.close()


# ── Claude ──────────────────────────────────────────────────────────────────

def _claude(model, system, messages, tool, max_tokens=900, timeout=40):
    resp = requests.post(ANTHROPIC_URL, headers={
        'x-api-key': os.getenv('ANTHROPIC_API_KEY', ''),
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }, json={
        'model': model, 'max_tokens': max_tokens, 'system': system, 'messages': messages,
        'tools': [tool], 'tool_choice': {'type': 'tool', 'name': tool['name']},
    }, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f'Claude API {resp.status_code}: {resp.text[:300]}')
    data = resp.json()
    usage = data.get('usage') or {}
    _record_usage(data.get('model') or model, usage)
    for block in data.get('content') or []:
        if block.get('type') == 'tool_use' and block.get('name') == tool['name']:
            return block.get('input') or {}, usage, data.get('model') or model
    raise RuntimeError('Claude no devolvió la decisión estructurada.')


def _record_usage(model, usage):
    """Suma el consumo al mismo panel de uso del bot de WhatsApp (fuente: instagram_studio)."""
    try:
        from services import ai_agent
        ai_agent._usage_ctx.source = 'instagram_studio'
        try:
            ai_agent._record_usage(model, usage)
        finally:
            ai_agent._usage_ctx.source = None
    except Exception:
        pass


def decide(agent, account_username, c, caption, earlier=None):
    model = agent.get('model') or _env('IG_STUDIO_MODEL', default='claude-haiku-5-5')
    system = (
        f"{agent['prompt']}\n\n"
        "Reglas fijas:\n"
        "- Nunca des precios, rangos ni descuentos. Si preguntan precio: responde en público que le escribes "
        "por DM y en el DM pregunta qué necesita y ofrece una llamada corta.\n"
        "- Nunca inventes datos, plazos ni resultados.\n"
        "- Respuesta pública de máximo 2 líneas. Sin hashtags. Puedes mencionar el nombre si es natural.\n"
        "- Si piden hablar con Henrry o es una crítica fuerte, usa accion=revisar con un borrador.\n"
        "- Spam, sorteos, promociones de seguidores o insultos sin fondo: accion=ignorar.\n"
        f"Cuenta que responde: @{account_username or 'papiatech'}."
    )
    context = f"Publicación: {caption[:600] or '(sin texto)'}\n"
    if earlier:
        context += 'Comentarios anteriores de esta persona en el post: ' + ' | '.join(earlier) + '\n'
    context += f"Comentario de @{c['from_username'] or 'usuario'}: {c['text']}"
    decision, usage, model = _claude(model, system, [{'role': 'user', 'content': context}], DECIDE_TOOL, 600, 30)
    return decision, usage, model


# ── Estrategia semanal ──────────────────────────────────────────────────────

def week_start_for(day=None):
    day = day or today_miami()
    return day - timedelta(days=day.weekday())


def _week_stats(org_id, start, end):
    s = datetime.combine(start, datetime.min.time(), MIAMI).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    e = datetime.combine(end, datetime.min.time(), MIAMI).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    try:
        posts = [dict(r) for r in db.execute(
            "SELECT m.*, a.username FROM ig_media m LEFT JOIN ig_accounts a ON a.ig_user_id=m.ig_user_id "
            "AND a.org_id=m.org_id WHERE m.org_id=? AND m.posted_at>=? AND m.posted_at<?", (org_id, s, e))]
        followers = {}
        for a in db.execute("SELECT ig_user_id, username FROM ig_accounts WHERE org_id=? AND is_active=1", (org_id,)):
            first = db.execute("SELECT followers FROM ig_account_daily WHERE org_id=? AND ig_user_id=? AND day>=? "
                               "ORDER BY day LIMIT 1", (org_id, a['ig_user_id'], start.isoformat())).fetchone()
            last = db.execute("SELECT followers FROM ig_account_daily WHERE org_id=? AND ig_user_id=? AND day<? "
                              "ORDER BY day DESC LIMIT 1", (org_id, a['ig_user_id'], end.isoformat())).fetchone()
            followers[a['username']] = {'inicio': first and first['followers'], 'fin': last and last['followers']}
        comments = [r['text'] for r in db.execute(
            "SELECT text FROM ig_comments WHERE org_id=? AND received_at>=? AND received_at<? LIMIT 150", (org_id, s, e))]
        try:
            dms = [r['message'] for r in db.execute(
                "SELECT message FROM meta_messages WHERE org_id=? AND channel='instagram' AND direction='inbound' "
                "AND created_at>=? AND created_at<? LIMIT 150", (org_id, s, e))]
        except Exception:
            dms = []
        leads = db.execute("SELECT COUNT(DISTINCT client_id) n FROM ig_comments WHERE org_id=? AND client_id IS NOT NULL "
                           "AND received_at>=? AND received_at<?", (org_id, s, e)).fetchone()['n']
    finally:
        db.close()
    return {
        'posts': [{
            'cuenta': p['username'], 'formato': FORMAT_LABELS.get(p['media_type'], p['media_type']),
            'texto': (p['caption'] or '')[:160],
            'fecha': (_parse_ts(p['posted_at']) or datetime.now(timezone.utc)).astimezone(MIAMI).strftime('%a %d %I:%M %p'),
            'views': p['views'], 'alcance': p['reach'], 'guardados': p['saved'], 'compartidos': p['shares'],
            'interacciones': p['total_interactions'], 'engagement_pct': round(_er(p), 2) if _er(p) else None,
        } for p in posts],
        'seguidores': followers,
        'leads_instagram': leads,
        'comentarios': comments,
        'dms': dms,
    }


def generate_weekly(org_id=1, force=False):
    start = week_start_for()
    db = get_db()
    exists = db.execute("SELECT id FROM ig_weekly_reports WHERE org_id=? AND week_start=?",
                        (org_id, start.isoformat())).fetchone()
    db.close()
    if exists and not force:
        return exists['id']
    last_week = _week_stats(org_id, start - timedelta(days=7), start)
    prev_week = _week_stats(org_id, start - timedelta(days=14), start - timedelta(days=7))
    prev_week.pop('comentarios', None)
    prev_week.pop('dms', None)
    model = _env('IG_STUDIO_STRATEGY_MODEL', default='claude-sonnet-5-5')
    system = (
        "Eres el estratega de contenido de Instagram de Henrry Martín (PapiaTech, Miami). "
        "Analizas los datos reales de la semana y propones el plan de la semana que empieza. "
        "Sé concreto y directo: nada de consejos genéricos; cada punto se apoya en los datos. "
        "Si faltan datos, dilo en una frase y propone igual con lo que hay. Escribe en español.\n\n"
        f"Contexto fijo:\n{strategy_context(org_id)}"
    )
    payload = {
        'semana_que_empieza': start.isoformat(),
        'semana_pasada': last_week,
        'semana_anterior_para_comparar': prev_week,
    }
    data, _usage, used_model = _claude(model, system, [{
        'role': 'user', 'content': 'Datos (JSON):\n' + json.dumps(payload, ensure_ascii=False, default=str)[:60000]
    }], REPORT_TOOL, max_tokens=3000, timeout=90)
    data['metricas'] = _report_metrics(last_week, prev_week)
    db = get_db()
    try:
        if exists:
            db.execute("DELETE FROM ig_weekly_reports WHERE id=?", (exists['id'],))
            db.execute("DELETE FROM ig_plan_items WHERE report_id=?", (exists['id'],))
        rid = db.execute("INSERT INTO ig_weekly_reports (org_id, week_start, data_json, model) VALUES (?, ?, ?, ?)",
                         (org_id, start.isoformat(), json.dumps(data, ensure_ascii=False), used_model)).lastrowid
        order = {d: i for i, d in enumerate(DAYS_ES)}
        for it in sorted(data.get('plan') or [], key=lambda x: order.get(x.get('dia'), 9)):
            db.execute("""INSERT INTO ig_plan_items (report_id, org_id, day, type, time, title, hook, account)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (rid, org_id, it.get('dia', 'LUN'), it.get('tipo', 'REEL'), it.get('hora', ''),
                        it.get('titulo', '')[:200], it.get('hook', '')[:300], it.get('cuenta', '')[:80]))
        db.commit()
    finally:
        db.close()
    return rid


def _report_metrics(last_week, prev_week):
    def totals(w):
        f = sum(((v['fin'] or 0) - (v['inicio'] or 0)) for v in w['seguidores'].values()
                if v['inicio'] is not None and v['fin'] is not None)
        reach = sum(p['alcance'] or 0 for p in w['posts'])
        return f, reach, w['leads_instagram']
    f1, r1, l1 = totals(last_week)
    f0, r0, l0 = totals(prev_week)
    return {
        'seguidores': f1, 'seguidores_prev': f0,
        'alcance': r1, 'alcance_delta_pct': ((r1 - r0) / r0 * 100) if r0 else None,
        'leads': l1, 'leads_prev': l0,
    }


def set_plan_item(org_id, item_id, approved):
    db = get_db()
    try:
        cur = db.execute("UPDATE ig_plan_items SET approved=? WHERE id=? AND org_id=?",
                         (1 if approved else 0, item_id, org_id))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def report_email_html(rep):
    d = rep['data']
    li = lambda xs: ''.join(f'<li>{_esc(x)}</li>' for x in xs)
    plan = ''.join(f"<tr><td>{_esc(i['day'])}</td><td>{_esc(i['type'])}</td><td>{_esc(i['time'])}</td>"
                   f"<td><b>{_esc(i['title'])}</b><br>{_esc(i['hook'])}</td><td>{_esc(i['account'])}</td></tr>"
                   for i in rep['items'])
    return (f"<h2>Estrategia de Instagram · semana del {_esc(rep['week_start'])}</h2>"
            f"<p>{_esc(d.get('resumen', ''))}</p><h3>Qué funcionó</h3><ul>{li(d.get('que_funciono', []))}</ul>"
            f"<h3>Qué no funcionó</h3><ul>{li(d.get('que_no_funciono', []))}</ul>"
            f"<h3>Plan</h3><table border='1' cellpadding='6' cellspacing='0'>{plan}</table>"
            "<p>Apruébalo en datos.papiatech.com/instagram-studio/estrategia</p>")


def _esc(s):
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def email_report(org_id=1):
    rep = latest_report(org_id)
    if not rep:
        raise ValueError('Todavía no hay reporte.')
    from services.mailer import send_email
    to = _env('IG_STUDIO_REPORT_EMAIL', default='henrry@papiatech.com')
    send_email(org_id, to, f"Estrategia de Instagram · semana del {rep['week_start']}", report_email_html(rep))
    return to
