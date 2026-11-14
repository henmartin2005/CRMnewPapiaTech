"""Run from papia-crm: python -m unittest discover -s tests -v. Sin llamadas externas."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import database

_tmp = tempfile.TemporaryDirectory()


def _bq_payload(rows):
    fields = ['mes', 'servicio', 'sku', 'moneda', 'costo', 'creditos', 'uso', 'unidad', 'exportado']
    return {
        'jobComplete': True,
        'schema': {'fields': [{'name': f} for f in fields]},
        'rows': [{'f': [{'v': r.get(f)} for f in fields]} for r in rows],
    }


class GcpCostsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.DATABASE = os.path.join(_tmp.name, 'gcp.db')
        from app import app  # noqa: import tardío para no pisar la BD de otros tests
        from migrate_videos import migrate as mv
        from migrate_demos import migrate as md
        from services.gcp_costs import migrate as mg
        database.init_db(); mv(); md(); mg()
        cls.app = app

    def setUp(self):
        self.app.config.update(TESTING=True)
        self.browser = self.app.test_client()
        with self.browser.session_transaction() as state:
            state.update(logged_in=True, user_role='admin', org_id=1, user_id=1, gcp_costs_csrf='csrf')
        db = database.get_db()
        db.execute('DELETE FROM client_gcp_costs')
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) "
                   "VALUES(961,'Yeras','Service','','other',1)")
        db.commit(); db.close()

    def link(self, project='yeras-services', csrf='csrf'):
        return self.browser.post('/clients/961/gasto-maps/vincular',
                                 data={'gcp_project_id': project, 'csrf': csrf})

    def test_link_requires_csrf_and_valid_id(self):
        self.assertEqual(self.link(csrf='bad').status_code, 400)
        self.link(project='NO VALIDO')
        from services import gcp_costs
        self.assertIsNone(gcp_costs.get_config(961, 1))
        self.link()
        self.assertEqual(gcp_costs.get_config(961, 1)['gcp_project_id'], 'yeras-services')

    def test_non_admin_forbidden(self):
        with self.browser.session_transaction() as state:
            state['user_role'] = 'user'
        self.assertEqual(self.browser.get('/clients/961/gasto-maps.json').status_code, 403)

    def test_card_only_when_linked(self):
        html = self.browser.get('/clients/961').get_data(as_text=True)
        self.assertNotIn('id="gasto-maps"', html)
        self.link()
        html = self.browser.get('/clients/961').get_data(as_text=True)
        self.assertIn('Gasto de API de Google Maps', html)
        self.assertIn('yeras-services', html)

    def test_sums_net_cost_and_caches(self):
        self.link()
        from services import gcp_costs
        mes = gcp_costs._now().strftime('%Y%m')
        rows = [
            {'mes': mes, 'servicio': 'Places API (New)', 'sku': 'Autocomplete Requests', 'moneda': 'USD',
             'costo': '30.0', 'creditos': '-28.3', 'uso': '10600', 'unidad': 'count', 'exportado': '1794640000'},
            {'mes': mes, 'servicio': 'Places API (New)', 'sku': 'Place Details Essentials', 'moneda': 'USD',
             'costo': '1.0', 'creditos': '-1.0', 'uso': '200', 'unidad': 'count', 'exportado': '1794641000'},
            {'mes': '202610', 'servicio': 'Maps API', 'sku': 'Dynamic Maps', 'moneda': 'USD',
             'costo': '2.5', 'creditos': '0', 'uso': '357', 'unidad': 'count', 'exportado': '1792000000'},
        ]
        resp = MagicMock(status_code=200)
        resp.json.return_value = _bq_payload(rows)
        session = MagicMock()
        session.post.return_value = resp
        with patch.object(gcp_costs, '_session', return_value=session):
            data = self.browser.get('/clients/961/gasto-maps.json').get_json()
            self.assertTrue(data['ok'], data)
            d = data['data']
            self.assertAlmostEqual(d['mes_actual']['neto'], 1.7, places=4)
            self.assertAlmostEqual(d['total_neto'], 4.2, places=4)
            self.assertAlmostEqual(d['total_bruto'], 33.5, places=4)
            self.assertEqual(len(d['meses']), 2)
            self.assertFalse(d['desde_cache'])
            body = session.post.call_args.kwargs['json']
            params = {p['name']: p['parameterValue']['value'] for p in body['queryParameters']}
            self.assertEqual(params['project'], 'yeras-services')
            # Segunda llamada: caché, sin ir a BigQuery
            again = self.browser.get('/clients/961/gasto-maps.json').get_json()['data']
            self.assertTrue(again['desde_cache'])
            self.assertEqual(session.post.call_count, 1)
            # Actualizar fuerza la consulta
            self.browser.get('/clients/961/gasto-maps.json?refresh=1')
            self.assertEqual(session.post.call_count, 2)

    def test_table_not_exported_yet(self):
        self.link()
        from services import gcp_costs
        resp = MagicMock(status_code=404)
        session = MagicMock(); session.post.return_value = resp
        with patch.object(gcp_costs, '_session', return_value=session):
            d = self.browser.get('/clients/961/gasto-maps.json').get_json()['data']
        self.assertTrue(d['sin_datos'])
        self.assertEqual(d['total_neto'], 0)

    def test_missing_credentials_file_is_reported(self):
        self.link()
        with patch.dict(os.environ, {'GCP_COSTS_SA_FILE': os.path.join(_tmp.name, 'no-existe.json')}):
            res = self.browser.get('/clients/961/gasto-maps.json').get_json()
        self.assertFalse(res['ok'])
        self.assertIn('Falta el archivo', res['error'])


if __name__ == '__main__':
    unittest.main()
