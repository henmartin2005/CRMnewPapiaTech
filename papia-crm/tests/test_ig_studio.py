"""Run from papia-crm: python -m unittest tests.test_ig_studio -v. Sin llamadas a Meta ni a Claude."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import database

_tmp = tempfile.TemporaryDirectory()
database.DATABASE = os.path.join(_tmp.name, 'test.db')
from app import app  # noqa: E402
from migrate_ig_studio import migrate  # noqa: E402
from services import ig_studio as igs  # noqa: E402


IG = '17841400000000001'


def _ts(days_ago=1):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y-%m-%dT%H:%M:%S+0000')


class FakeGraph:
    """Simula graph.instagram.com y guarda las llamadas."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, path, token, params=None, data=None, json_body=None, timeout=20):
        self.calls.append((method, path, params, json_body))
        if path == 'me':
            return {'user_id': IG, 'username': 'henmartin14', 'followers_count': 3104, 'follows_count': 200,
                    'media_count': 2}
        if path == 'me/insights':
            return {'data': [{'name': params['metric'], 'total_value': {'value': 900}}]}
        if path == 'me/media':
            return {'data': [
                {'id': 'm1', 'caption': '5 automatizaciones\nque necesitas', 'media_type': 'VIDEO',
                 'media_product_type': 'REELS', 'permalink': 'https://instagram.com/p/m1', 'timestamp': _ts(2),
                 'like_count': 300, 'comments_count': 20},
                {'id': 'm2', 'caption': 'Noticias de IA', 'media_type': 'CAROUSEL_ALBUM',
                 'media_product_type': 'FEED', 'permalink': 'https://instagram.com/p/m2', 'timestamp': _ts(5),
                 'like_count': 100, 'comments_count': 5},
            ]}
        if path.endswith('/insights'):
            mid = path.split('/')[0]
            vals = {'m1': (12800, 9400, 412, 268, 860), 'm2': (6200, 4900, 388, 97, 410)}[mid]
            names = ['views', 'reach', 'saved', 'shares', 'total_interactions']
            return {'data': [{'name': n, 'values': [{'value': v}]} for n, v in zip(names, vals)]}
        if path.endswith('/replies') or path == 'me/messages' or path == 'me/subscribed_apps':
            return {'id': 'ok'}
        raise AssertionError(f'llamada inesperada {method} {path}')


class IgStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Otros módulos de tests cambian database.DATABASE al importarse: crea las tablas en la BD activa.
        database.init_db()
        migrate()

    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret')
        self.browser = app.test_client()
        with self.browser.session_transaction() as s:
            s.update(logged_in=True, user_role='admin', org_id=1, user_id=1, igs_csrf='csrf')
        self.h = {'X-IGS-CSRF': 'csrf'}
        db = database.get_db()
        for t in ('ig_comments', 'ig_media', 'ig_account_daily', 'ig_accounts', 'ig_agent_runs',
                  'ig_plan_items', 'ig_weekly_reports'):
            db.execute(f'DELETE FROM {t}')
        db.execute("INSERT INTO ig_accounts (org_id, ig_user_id, username, access_token, token_expires_at) "
                   "VALUES (1, ?, 'henmartin14', 'tok', '2099-01-01 00:00:00')", (IG,))
        db.execute("UPDATE ig_agents SET enabled=1, reply_delay=0, business_hours=0, auto_dm=1, create_leads=1 "
                   "WHERE kind='comments'")
        db.commit()
        db.close()
        self.graph = FakeGraph()
        p = patch.object(igs, '_graph', self.graph)
        p.start()
        self.addCleanup(p.stop)

    def _webhook(self, cid, text, frm='marta.rojas', frm_id='999'):
        return {'object': 'instagram', 'entry': [{'id': IG, 'changes': [{'field': 'comments', 'value': {
            'id': cid, 'text': text, 'from': {'id': frm_id, 'username': frm},
            'media': {'id': 'm1', 'media_product_type': 'REELS'}}}]}]}

    # ── Páginas ──
    def test_pages_render_for_admin(self):
        for path in ('/instagram-studio', '/instagram-studio/comentarios', '/instagram-studio/agente',
                     '/instagram-studio/estrategia'):
            r = self.browser.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(b'Instagram Studio', r.data)

    def test_sidebar_has_tab(self):
        r = self.browser.get('/instagram-studio')
        self.assertIn(b'sb-label">Instagram Studio<', r.data)

    def test_non_admin_forbidden(self):
        with self.browser.session_transaction() as s:
            s['user_role'] = 'user'
        self.assertEqual(self.browser.get('/instagram-studio').status_code, 403)

    def test_post_requires_csrf(self):
        r = self.browser.post('/instagram-studio/api/sync', json={})
        self.assertEqual(r.status_code, 403)

    # ── Sync y analítica ──
    def test_sync_saves_snapshot_media_and_insights(self):
        acct = igs.list_accounts(1)[0]
        self.assertEqual(igs.sync_account(acct), 2)
        ov = igs.overview(1, None, 30)
        self.assertEqual(ov['followers'], 3104)
        self.assertEqual(ov['reach'], 9400 + 4900)
        self.assertAlmostEqual(ov['er'], (860 + 410) / (9400 + 4900) * 100)
        top = igs.top_posts(1)
        self.assertEqual(top[0]['media_id'], 'm1')
        self.assertEqual(top[0]['format'], 'Reels')
        fmts = {f['name'] for f in igs.format_performance(1)}
        self.assertEqual(fmts, {'Reels', 'Carruseles'})
        self.assertTrue(igs.best_slots(1))
        page = self.browser.get('/instagram-studio')
        self.assertIn(b'5 automatizaciones', page.data)

    def test_chart_data(self):
        series = [{'username': 'a', 'points': [{'day': '2026-11-01', 'followers': 10},
                                               {'day': '2026-11-02', 'followers': 20}]}]
        c = igs.chart_svg_data(series)
        self.assertEqual(c['lines'][0]['points'], '0.0,228.0 800.0,12.0')
        self.assertEqual(len(c['ticks']), 4)
        self.assertFalse(c['relative'])
        two = series + [{'username': 'b', 'points': [{'day': '2026-11-01', 'followers': 500},
                                                     {'day': '2026-11-02', 'followers': 505}]}]
        c2 = igs.chart_svg_data(two)
        self.assertTrue(c2['relative'])
        self.assertEqual([(l['last'], l['gained']) for l in c2['lines']], [(20, 10), (505, 5)])

    # ── Comentarios ──
    def test_info_comment_gets_reply_dm_and_lead(self):
        decision = {'categoria': 'info', 'accion': 'responder_dm', 'respuesta_publica': '¡Hola! Te escribí por DM.',
                    'mensaje_dm': '¿Qué necesitas para tu negocio?', 'crear_lead': True, 'servicio': 'web'}
        with patch.object(igs, 'decide', return_value=(decision, {}, 'test')):
            saved = igs.handle_webhook(self._webhook('c1', 'Info'), background=False)
        self.assertEqual(saved, ['c1'])
        c = igs._load_comment('c1')
        self.assertEqual(c['status'], 'replied_dm')
        self.assertTrue(c['client_id'])
        paths = [call[1] for call in self.graph.calls]
        self.assertIn('c1/replies', paths)
        dm = next(call for call in self.graph.calls if call[1] == 'me/messages')
        self.assertEqual(dm[3]['recipient'], {'comment_id': 'c1'})
        db = database.get_db()
        client = db.execute('SELECT * FROM clients WHERE id=?', (c['client_id'],)).fetchone()
        db.close()
        self.assertEqual(client['project_type'], 'website')
        self.assertEqual(len(igs.recent_runs(1)), 1)

    def test_own_comment_and_duplicates_are_skipped(self):
        self.assertEqual(igs.handle_webhook(self._webhook('c2', 'Gracias!', 'henmartin14', IG), background=False), [])
        with patch.object(igs, 'decide', return_value=({'categoria': 'positivo', 'accion': 'responder',
                                                        'respuesta_publica': 'Gracias'}, {}, 't')):
            igs.handle_webhook(self._webhook('c3', 'Brutal'), background=False)
            self.assertEqual(igs.handle_webhook(self._webhook('c3', 'Brutal'), background=False), [])
        self.assertEqual(igs._load_comment('c3')['status'], 'replied')

    def test_review_keeps_draft_and_manual_approve_publishes(self):
        decision = {'categoria': 'critica', 'accion': 'revisar', 'borrador': 'Te enseño cómo funciona.'}
        with patch.object(igs, 'decide', return_value=(decision, {}, 't')):
            igs.handle_webhook(self._webhook('c4', 'Eso lo hace ChatGPT'), background=False)
        c = igs._load_comment('c4')
        self.assertEqual(c['status'], 'review')
        self.assertNotIn('c4/replies', [x[1] for x in self.graph.calls])
        r = self.browser.post('/instagram-studio/api/comments/c4/approve', json={'text': 'Te lo enseño por DM'},
                              headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(igs._load_comment('c4')['status'], 'replied')

    def test_media_without_agent_stays_for_henrry(self):
        igs.store_comment(IG, self._webhook('c5', 'Hola')['entry'][0]['changes'][0]['value'])
        self.assertTrue(igs.set_media_agent(1, 'm1', off=True))
        self.assertEqual(igs.process_comment('c5', delay=False), 'off')

    # ── Agentes ──
    def test_save_agent_endpoint(self):
        agent = next(a for a in igs.list_agents(1) if a['kind'] == 'comments')
        r = self.browser.post(f"/instagram-studio/api/agents/{agent['id']}",
                              json={'name': 'Comentarios Papia', 'reply_delay': '45', 'business_hours': True},
                              headers=self.h)
        self.assertEqual(r.status_code, 200)
        a = igs.get_agent(1, agent['id'])
        self.assertEqual((a['name'], a['reply_delay'], a['business_hours']), ('Comentarios Papia', 45, 1))

    # ── Estrategia ──
    def test_weekly_report_and_plan_approval(self):
        acct = igs.list_accounts(1)[0]
        igs.sync_account(acct)
        report = {'resumen': 'Creciste con casos reales.', 'que_funciono': ['Reels de clientes'],
                  'que_no_funciono': ['Fotos sin texto'], 'preguntas_frecuentes': [{'tema': 'Precio web', 'veces': 4}],
                  'insights': ['Publica más en @papiatech'],
                  'plan': [{'dia': 'MIÉ', 'tipo': 'CARRUSEL', 'hora': '8:30 PM', 'titulo': 'Noticias de IA',
                            'hook': 'Lo nuevo', 'cuenta': '@henmartin14'},
                           {'dia': 'LUN', 'tipo': 'REEL', 'hora': '12:00 PM', 'titulo': 'Caso real',
                            'hook': 'Esta clínica…', 'cuenta': '@papiatech'}]}
        with patch.object(igs, '_claude', return_value=(report, {}, 'claude-test')) as fake:
            r = self.browser.post('/instagram-studio/api/strategy/generate', json={}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIn('8:30 PM', fake.call_args[0][1])  # el contexto fijo va en el system prompt
        rep = igs.latest_report(1)
        self.assertEqual([i['day'] for i in rep['items']], ['LUN', 'MIÉ'])
        item = rep['items'][0]['id']
        r = self.browser.post(f'/instagram-studio/api/plan/{item}', json={'approved': True}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(igs.latest_report(1)['items'][0]['approved'], 1)
        page = self.browser.get('/instagram-studio/estrategia')
        self.assertIn('Creciste con casos reales.'.encode(), page.data)
        self.assertIn(b'1 de 2 aprobados', page.data)


if __name__ == '__main__':
    unittest.main()
