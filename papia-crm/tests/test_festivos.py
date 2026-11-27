"""Run from papia-crm: python -m unittest discover -s tests -v. Sin llamadas externas."""
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import database

_tmp = tempfile.TemporaryDirectory()


class FestivosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.DATABASE = os.path.join(_tmp.name, 'festivos.db')
        from app import app  # noqa: import tardío para no pisar la BD de otros tests
        from migrate_videos import migrate as mv
        from migrate_demos import migrate as md
        from services.festivos import migrate as mf
        database.init_db(); mv(); md(); mf()
        cls.app = app

    def setUp(self):
        self.app.config.update(TESTING=True)
        self.browser = self.app.test_client()
        with self.browser.session_transaction() as state:
            state.update(logged_in=True, user_role='admin', org_id=1, user_id=1, festivos_csrf='csrf')
        db = database.get_db()
        db.execute('DELETE FROM festivos_envios')
        db.execute('DELETE FROM client_festivos')
        db.execute('DELETE FROM festivos_settings')
        for cid, name, email in ((971, 'María', 'maria@example.com'), (972, 'Luis', 'luis@example.com'),
                                 (973, 'Sin', '')):
            db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) "
                       "VALUES(?,?,'Test',?,'website',1)", (cid, name, email))
        db.commit(); db.close()

    # ── Fechas ──
    def test_send_dates(self):
        from services.festivos import OCASIONES, next_send_date
        self.assertEqual(OCASIONES['thanksgiving']['fecha_envio'](2027), date(2027, 11, 24))
        self.assertEqual(OCASIONES['thanksgiving']['fecha_envio'](2026), date(2026, 11, 25))
        self.assertEqual(next_send_date('navidad', date(2026, 11, 27)), date(2026, 12, 24))
        self.assertEqual(next_send_date('ano_nuevo', date(2026, 12, 29)), date(2027, 1, 1))
        self.assertEqual(next_send_date('cuatro_julio', date(2026, 11, 27)), date(2027, 7, 4))

    # ── Nadie recibe por defecto ──
    def test_default_disabled_and_profile_save(self):
        from services import festivos
        self.assertEqual(festivos.get_config(971, 1)['enabled'], 0)
        self.assertEqual(self.browser.post('/clients/971/festivos', data={'enabled': '1', 'csrf': 'bad'}).status_code, 400)
        r = self.browser.post('/clients/971/festivos',
                              data={'enabled': '1', 'proyecto': 'tu nueva web', 'logro': '', 'csrf': 'csrf'})
        self.assertEqual(r.status_code, 302)
        cfg = festivos.get_config(971, 1)
        self.assertEqual((cfg['enabled'], cfg['proyecto']), (1, 'tu nueva web'))
        self.assertTrue(cfg['unsubscribe_token'])
        page = self.browser.get('/clients/971').get_data(as_text=True)
        self.assertIn('Correos festivos', page)
        self.assertIn('tu nueva web', page)

    def test_non_admin_forbidden(self):
        with self.browser.session_transaction() as state:
            state['user_role'] = 'user'
        self.assertEqual(self.browser.get('/festivos').status_code, 403)
        self.assertEqual(self.browser.post('/clients/971/festivos/toggle', data={'csrf': 'csrf'}).status_code, 403)

    def test_toggle_and_bulk(self):
        from services import festivos
        r = self.browser.post('/clients/972/festivos/toggle', data={'enabled': '1', 'csrf': 'csrf'})
        self.assertTrue(r.get_json()['enabled'])
        self.browser.post('/festivos/clientes', data={'client_ids': ['971'], 'csrf': 'csrf'})
        self.assertEqual(festivos.enabled_ids(1), {971})
        self.assertIn('🎉', self.browser.get('/clients/?festivos=1').get_data(as_text=True))

    # ── Envíos ──
    def test_prepare_send_no_duplicates(self):
        from services import festivos
        festivos.save_config(971, 1, True, 'tu nueva web', 'ya recibes reservas')
        festivos.save_config(973, 1, True)  # sin email → no entra
        sent = []
        with self.app.app_context(), \
             patch('services.festivos._send', side_effect=lambda *a, **k: sent.append(a)):
            festivos.run_daily(today=date(2026, 12, 29))          # 3 días antes de Año Nuevo: lista + aviso
            self.assertEqual(len(festivos.list_envios(1, clave='ano_nuevo', anio=2027, estado='pendiente')), 1)
            self.assertEqual(len(sent), 1)                         # solo el aviso al admin
            self.assertIn('henrry@papiatech.com', sent[0][1])
            festivos.run_daily(today=date(2026, 12, 30))           # el aviso no se repite
            self.assertEqual(len(sent), 1)
            with patch('time.sleep'):
                festivos.run_daily(today=date(2027, 1, 1))
                festivos.run_daily(today=date(2027, 1, 1))         # segunda corrida: nada más
        correos = [s for s in sent if s[1] == 'maria@example.com']
        self.assertEqual(len(correos), 1)
        asunto, html = correos[0][2], correos[0][3]
        self.assertEqual(asunto, '🎉 María, 2027 es tu año')
        self.assertIn('tu nueva web', html)
        self.assertIn('ya recibes reservas', html)
        self.assertIn('/festivos/baja/', html)
        self.assertEqual(festivos.list_envios(1, clave='ano_nuevo', anio=2027)[0]['estado'], 'enviado')

    def test_omitted_not_sent(self):
        from services import festivos
        festivos.save_config(971, 1, True)
        festivos.prepare(1, 'navidad', date(2026, 12, 24))
        envio = festivos.list_envios(1, clave='navidad')[0]
        self.browser.post(f"/festivos/envio/{envio['id']}/omitir", data={'csrf': 'csrf'})
        sent = []
        with self.app.app_context(), patch('services.festivos._send', side_effect=lambda *a, **k: sent.append(a)):
            festivos.send_pending(1, 'navidad', date(2026, 12, 24), pause=0)
        self.assertEqual(sent, [])

    def test_unsubscribe_flow(self):
        from services import festivos
        festivos.save_config(971, 1, True)
        festivos.prepare(1, 'navidad', date(2026, 12, 24))
        token = festivos.get_config(971, 1)['unsubscribe_token']
        anon = self.app.test_client()
        self.assertEqual(anon.get(f'/festivos/baja/{token}').status_code, 200)  # solo confirma
        self.assertEqual(festivos.get_config(971, 1)['enabled'], 1)
        self.assertIn('Listo', anon.post(f'/festivos/baja/{token}').get_data(as_text=True))
        cfg = festivos.get_config(971, 1)
        self.assertEqual(cfg['enabled'], 0)
        self.assertTrue(cfg['unsubscribed_at'])
        self.assertEqual(festivos.list_envios(1, clave='navidad')[0]['estado'], 'omitido')
        self.assertIn('no válido', anon.post('/festivos/baja/token-que-no-existe-123').get_data(as_text=True))

    def test_panel_and_previews_render(self):
        from services import festivos
        festivos.save_config(971, 1, True, 'tu tienda online')
        page = self.browser.get('/festivos?ocasion=ano_nuevo').get_data(as_text=True)
        self.assertIn('Clientes que reciben', page)
        for clave in festivos.OCASIONES:
            r = self.browser.get(f'/festivos/preview/{clave}?client_id=971')
            self.assertEqual(r.status_code, 200, clave)
        self.assertIn('tu tienda online', self.browser.get('/festivos/preview/ano_nuevo?client_id=971').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
