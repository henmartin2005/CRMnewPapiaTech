"""Run from papia-crm: python -m unittest discover -s tests -v. Sin llamadas externas."""
import os
import tempfile
import unittest
from unittest.mock import patch

import database

_tmp = tempfile.TemporaryDirectory()


class DemosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.DATABASE = os.path.join(_tmp.name, 'demos.db')
        from app import app  # noqa: import tardío para no pisar la BD de otros tests
        from migrate_videos import migrate as mv
        from migrate_demos import migrate as md
        database.init_db(); mv(); md()
        cls.app = app

    def setUp(self):
        self.app.config.update(TESTING=True)
        self.browser = self.app.test_client()
        with self.browser.session_transaction() as state:
            state.update(logged_in=True, user_role='admin', org_id=1, user_id=1, demos_csrf='csrf')
        db = database.get_db()
        db.execute('DELETE FROM demo_logs'); db.execute('DELETE FROM demo_links'); db.execute('DELETE FROM demo_rate')
        db.execute("INSERT OR IGNORE INTO organizations(id,name,slug) VALUES(2,'Other','other')")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) "
                   "VALUES(951,'Inna','Lopez','','other',1)")
        db.commit(); db.close()
        self.demo_id = self.q("SELECT id FROM demos WHERE org_id=1 AND slug='grooming'", one=True)['id']
        self.public = self.app.test_client()

    def q(self, sql, params=(), one=False):
        db = database.get_db()
        try:
            cur = db.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()
        finally:
            db.close()

    def create(self, **extra):
        data = {'csrf': 'csrf', 'demo_id': self.demo_id, 'negocio': 'INNA Beauty Salon',
                'nombre_prospecto': 'Inna', 'telefono': '305-986-5677', 'color': '#FF00AA',
                'max_visitas': '3', 'dias': '7'}
        data.update(extra)
        r = self.browser.post('/demos/enlaces', data=data)
        self.assertEqual(r.status_code, 302, r.data[:300])
        return self.q('SELECT * FROM demo_links ORDER BY id DESC LIMIT 1', one=True)

    def validar(self, codigo, device='dev-1', ua='Mozilla/5.0 (iPhone) Safari/604.1'):
        return self.public.post('/api/demos/validar', json={'codigo': codigo, 'deviceId': device},
                                headers={'User-Agent': ua, 'X-Real-IP': '1.2.3.4'}).get_json()

    # ── Panel ─────────────────────────────────────────────────────────────────
    def test_migration_idempotent_and_seed(self):
        from migrate_demos import migrate
        migrate(); migrate()
        self.assertEqual(len(self.q("SELECT * FROM demos WHERE slug='grooming' AND org_id=1")), 1)

    def test_panel_requires_admin(self):
        with self.browser.session_transaction() as state:
            state['user_role'] = 'user'
        self.assertEqual(self.browser.get('/demos').status_code, 403)
        anon = self.app.test_client()
        self.assertEqual(anon.get('/demos').status_code, 302)

    def test_csrf_required(self):
        r = self.browser.post('/demos/enlaces', data={'demo_id': self.demo_id, 'negocio': 'X', 'nombre_prospecto': 'Y'})
        self.assertEqual(r.status_code, 400)

    def test_create_link_code_format_and_lead(self):
        link = self.create(crear_lead='1')
        self.assertRegex(link['codigo'], r'^INNA-[A-Z0-9]{4}$')
        self.assertIsNotNone(link['cliente_id'])
        lead = self.q('SELECT * FROM clients WHERE id=?', (link['cliente_id'],), one=True)
        self.assertEqual(lead['company'], 'INNA Beauty Salon')
        self.assertEqual(lead['source'], 'demo')
        page = self.browser.get(f"/demos/enlaces/{link['id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(link['codigo'].encode(), page.data)
        self.assertIn(b'wa.me/13059865677', page.data)
        self.assertIn(f"groomingmiami.lovable.app/d/{link['codigo']}".encode(), page.data)
        self.assertEqual(self.browser.get('/demos').status_code, 200)

    def test_cannot_link_other_org_client(self):
        db = database.get_db()
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) VALUES(952,'X','Y','','other',2)")
        db.commit(); db.close()
        r = self.browser.post('/demos/enlaces', data={'csrf': 'csrf', 'demo_id': self.demo_id, 'negocio': 'A',
                                                       'nombre_prospecto': 'B', 'cliente_id': '952'})
        self.assertEqual(r.status_code, 404)

    def test_catalog_add_demo(self):
        r = self.browser.post('/demos/catalogo', data={'csrf': 'csrf', 'nombre': 'Barbería', 'slug': 'barberia',
                                                       'url': 'https://demo.papiatech.com/barberia', 'color': '#111111'})
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(self.q("SELECT 1 FROM demos WHERE slug='barberia' AND org_id=1", one=True))
        bad = self.browser.post('/demos/catalogo', data={'csrf': 'csrf', 'nombre': 'X', 'slug': 'Bad Slug',
                                                         'url': 'http://x', 'color': 'red'})
        self.assertEqual(bad.status_code, 302)
        self.assertIsNone(self.q("SELECT 1 FROM demos WHERE nombre='X'", one=True))

    # ── API pública ───────────────────────────────────────────────────────────
    @patch('services.demo_access.requests.post')
    def test_visits_limit_logs_and_notify(self, post):
        link = self.create(crear_lead='1')
        with patch.dict(os.environ, {'DEMO_NOTIFY_WEBHOOK': 'https://n8n.example/hook'}):
            first = self.validar(link['codigo'].lower())
            self.assertTrue(first['ok'])
            self.assertEqual(first['nicho'], 'grooming')
            self.assertEqual(first['color'], '#FF00AA')
            self.assertEqual(first['negocio'], 'INNA Beauty Salon')
            self.assertEqual(post.call_count, 1)                      # aviso solo en la primera visita
            self.assertTrue(self.validar(link['codigo'])['ok'])
            self.assertTrue(self.validar(link['codigo'])['ok'])
            self.assertEqual(post.call_count, 1)
            blocked = self.validar(link['codigo'])
            self.assertFalse(blocked['ok'])
            self.assertNotIn('token', blocked)
            self.assertEqual(post.call_count, 2)                      # aviso de bloqueo
            self.validar(link['codigo'])
            self.assertEqual(post.call_count, 2)                      # máximo uno por hora
        tipos = [r['tipo'] for r in self.q('SELECT tipo FROM demo_logs WHERE link_id=? ORDER BY id', (link['id'],))]
        self.assertEqual(tipos, ['entrada'] * 3 + ['bloqueado'] * 2)
        tareas = self.q('SELECT * FROM follow_ups WHERE client_id=?', (link['cliente_id'],))
        self.assertEqual(len(tareas), 1)
        self.assertIn('abrió el demo', tareas[0]['summary'])
        self.assertEqual(self.q('SELECT dispositivo FROM demo_logs LIMIT 1', one=True)['dispositivo'], 'Safari · iOS')

    def test_unknown_and_malformed_codes(self):
        self.assertFalse(self.validar('NOPE-ZZZZ')['ok'])
        self.assertFalse(self.validar("x' OR 1=1 --")['ok'])
        self.assertFalse(self.validar('')['ok'])

    def test_expired_inactive_and_unlimited(self):
        link = self.create()
        db = database.get_db()
        db.execute("UPDATE demo_links SET vence_el='2000-01-01 00:00:00' WHERE id=?", (link['id'],)); db.commit(); db.close()
        self.assertFalse(self.validar(link['codigo'])['ok'])
        self.browser.post(f"/demos/enlaces/{link['id']}/accion", data={'csrf': 'csrf', 'accion': 'extender'})
        self.assertTrue(self.validar(link['codigo'])['ok'])
        self.browser.post(f"/demos/enlaces/{link['id']}/accion", data={'csrf': 'csrf', 'accion': 'desactivar'})
        self.assertFalse(self.validar(link['codigo'])['ok'])
        self.browser.post(f"/demos/enlaces/{link['id']}/accion", data={'csrf': 'csrf', 'accion': 'activar'})
        self.browser.post(f"/demos/enlaces/{link['id']}/accion", data={'csrf': 'csrf', 'accion': 'ilimitado_on'})
        for _ in range(6):
            self.assertTrue(self.validar(link['codigo'])['ok'])

    def test_device_binding(self):
        link = self.create(amarrar_dispositivo='1')
        self.assertTrue(self.validar(link['codigo'], device='phone-a')['ok'])
        self.assertFalse(self.validar(link['codigo'], device='phone-b')['ok'])
        self.assertTrue(self.validar(link['codigo'], device='phone-a')['ok'])

    def test_rate_limit(self):
        for _ in range(20):
            self.validar('NOPE-ZZZZ')
        r = self.public.post('/api/demos/validar', json={'codigo': 'NOPE-ZZZZ'}, headers={'X-Real-IP': '1.2.3.4'})
        self.assertEqual(r.status_code, 429)
        other = self.public.post('/api/demos/validar', json={'codigo': 'NOPE-ZZZZ'}, headers={'X-Real-IP': '9.9.9.9'})
        self.assertEqual(other.status_code, 200)

    def test_session_and_events(self):
        link = self.create()
        token = self.validar(link['codigo'])['token']
        s = self.public.post('/api/demos/sesion', json={'token': token}).get_json()
        self.assertTrue(s['ok'])
        self.assertFalse(self.public.post('/api/demos/sesion', json={'token': token + 'x'}).get_json()['ok'])
        ok = self.public.post('/api/demos/evento', json={'token': token, 'tipo': 'seccion_vista',
                                                         'seccion': 'Agenda del dueño', 'duracion': 42})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.public.post('/api/demos/evento', json={'token': 'bad', 'tipo': 'salida'}).status_code, 401)
        self.assertEqual(self.public.post('/api/demos/evento', json={'token': token, 'tipo': 'entrada'}).status_code, 400)
        row = self.q("SELECT * FROM demo_logs WHERE tipo='seccion_vista'", one=True)
        self.assertEqual((row['seccion'], row['duracion_segundos']), ('Agenda del dueño', 42))
        detail = self.browser.get(f"/demos/enlaces/{link['id']}")
        self.assertIn('Agenda del dueño'.encode(), detail.data)

    def test_cors_only_allowed_origins(self):
        ok = self.public.options('/api/demos/validar', headers={
            'Origin': 'https://groomingmiami.lovable.app', 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(ok.headers.get('Access-Control-Allow-Origin'), 'https://groomingmiami.lovable.app')
        bad = self.public.options('/api/demos/validar', headers={
            'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})
        self.assertIsNone(bad.headers.get('Access-Control-Allow-Origin'))
        leads = self.public.options('/api/new-lead', headers={
            'Origin': 'https://groomingmiami.lovable.app', 'Access-Control-Request-Method': 'POST'})
        self.assertIsNone(leads.headers.get('Access-Control-Allow-Origin'))

    def test_import_grooming_idempotent(self):
        import import_grooming
        codes, events = import_grooming.run()
        self.assertEqual(codes, 26)
        self.assertGreater(events, 150)
        self.assertEqual(import_grooming.run(), (0, 0))
        lucas = self.q("SELECT * FROM demo_links WHERE codigo='LUCASMOBILE-NYPC'", one=True)
        self.assertEqual((lucas['visitas_usadas'], lucas['max_visitas']), (3, 3))
        bloqueos = self.q("SELECT COUNT(*) n FROM demo_logs WHERE link_id=? AND tipo='bloqueado'", (lucas['id'],), one=True)
        self.assertEqual(bloqueos['n'], 2)
        self.assertEqual(self.browser.get('/demos').status_code, 200)

    def test_client_profile_card(self):
        link = self.create(cliente_id='951')
        self.validar(link['codigo'])
        page = self.browser.get('/clients/951')
        self.assertEqual(page.status_code, 200)
        self.assertIn(link['codigo'].encode(), page.data)


if __name__ == '__main__':
    unittest.main()
