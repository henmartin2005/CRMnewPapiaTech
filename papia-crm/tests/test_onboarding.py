"""Run from papia-crm: python -m unittest tests.test_onboarding -v. Sin llamadas externas."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import database

_tmp = tempfile.TemporaryDirectory()


class OnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.DATABASE = os.path.join(_tmp.name, 'onboarding.db')
        import models.client_document as cd
        cd.DOCS_ROOT = os.path.join(_tmp.name, 'client_docs')
        from app import app
        database.init_db()
        cls.app = app

    def setUp(self):
        self.app.config.update(TESTING=True)
        db = database.get_db()
        db.execute("INSERT OR IGNORE INTO organizations(id,name,slug) VALUES(2,'Other','other')")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,company,project_type,org_id) "
                   "VALUES(32,'Lucas','Perez','lucaspetgrooming@gmail.com','Lucas Pet Grooming','other',1)")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) "
                   "VALUES(33,'Otro','Cliente','otro@x.com','other',1)")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) "
                   "VALUES(34,'Ajeno','Org2','ajeno@x.com','other',2)")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,project_details,org_id) "
                   "VALUES(35,'Maybis','Rodriguez','maybis@x.com','crm','Organizar empresa de diseño web',1)")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,company,project_type,org_id) "
                   "VALUES(36,'Mell','B','mell@x.com','Maison by Mell Wig Boutique','website',1)")
        db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,company,project_type,org_id) "
                   "VALUES(37,'Jariel','P','jariel@x.com','Jariel Barber Shop','website',1)")
        db.commit()
        from models.onboarding import ensure_schema
        ensure_schema()
        db.execute("DELETE FROM client_onboarding")
        try:
            db.execute("DELETE FROM client_documents")
        except Exception:
            pass
        db.commit(); db.close()
        self.client_b = self.app.test_client()
        with self.client_b.session_transaction() as s:
            s.update(portal_client_ids=[32], portal_email='lucaspetgrooming@gmail.com')
        self.admin = self.app.test_client()
        with self.admin.session_transaction() as s:
            s.update(logged_in=True, user_role='admin', org_id=1, user_id=1)
        self.anon = self.app.test_client()

    def row(self, cid=32):
        db = database.get_db()
        try:
            r = db.execute("SELECT * FROM client_onboarding WHERE client_id=?", (cid,)).fetchone()
            return dict(r) if r else None
        finally:
            db.close()

    def post_save(self, browser, data, cid=32, **extra):
        body = {'client_id': cid, 'data': data}
        body.update(extra)
        return browser.post('/portal/onboarding/save', json=body)

    # ── Acceso ─────────────────────────────────────────────────────────────
    def test_anon_redirected_and_cannot_save(self):
        r = self.anon.get('/portal/onboarding')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/portal/login', r.headers['Location'])
        r = self.post_save(self.anon, {'legal_name': 'X'})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.row())

    def test_client_cannot_touch_other_client(self):
        self.assertEqual(self.client_b.get('/portal/onboarding?c=33').status_code, 404)
        self.assertEqual(self.post_save(self.client_b, {'legal_name': 'X'}, cid=33).status_code, 403)
        self.assertIsNone(self.row(33))

    def test_admin_cannot_touch_other_org(self):
        self.assertEqual(self.admin.get('/portal/admin/onboarding/34').status_code, 404)
        self.assertEqual(self.post_save(self.admin, {'legal_name': 'X'}, cid=34, preview=True).status_code, 403)

    def test_save_requires_json(self):
        r = self.client_b.post('/portal/onboarding/save', data={'client_id': 32})
        self.assertEqual(r.status_code, 400)

    # ── Guardado ───────────────────────────────────────────────────────────
    def test_client_page_renders_all_sections(self):
        r = self.client_b.get('/portal/onboarding')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for key in ('legal', 'brand', 'content', 'services', 'ops', 'booking', 'payments', 'email',
                    'whatsapp', 'ai', 'domain', 'legal_texts', 'confirm'):
            self.assertIn(f'id="{key}"', html)
        self.assertIn('Enviar formulario', html)
        self.assertIn('value="Naples"', html)  # filas precargadas de zonas

    def test_save_sanitizes_and_merges(self):
        r = self.post_save(self.client_b, {
            'legal_name': '  Lucas Pet Grooming LLC  ',
            'entity_type': 'LLC',
            'style': 'Inventado',                      # opción no válida → vacía
            'cats': 'quizas',                           # yesno inválido → vacío
            'stripe_tasks': ['Invité a henrry@papiatech.com como usuario «Developer» en Stripe', 'hack'],
            'services': [{'name': 'Baño', 'size': 'S', 'price': '45', 'minutes': '60', 'evil': 'x'}],
            'unknown_key': 'nope',
            'logo_files': 'no se guarda',
        })
        self.assertEqual(r.status_code, 200, r.data)
        j = r.get_json()
        self.assertTrue(j['ok'])
        self.assertEqual(j['status'], 'draft')
        data = json.loads(self.row()['data'])
        self.assertEqual(data['legal_name'], 'Lucas Pet Grooming LLC')
        self.assertEqual(data['style'], '')
        self.assertEqual(data['cats'], '')
        self.assertEqual(data['stripe_tasks'], ['Invité a henrry@papiatech.com como usuario «Developer» en Stripe'])
        self.assertEqual(data['services'], [{'name': 'Baño', 'size': 'S', 'price': '45', 'minutes': '60', 'includes': ''}])
        self.assertNotIn('unknown_key', data)
        self.assertNotIn('logo_files', data)
        # Un segundo guardado parcial no borra lo anterior
        self.post_save(self.client_b, {'slogan': 'Tu mascota, feliz'})
        data = json.loads(self.row()['data'])
        self.assertEqual(data['legal_name'], 'Lucas Pet Grooming LLC')
        self.assertEqual(data['slogan'], 'Tu mascota, feliz')

    def test_progress_counts_fields_and_zone_prefill(self):
        from models import onboarding as ob
        tpl = ob.get_template('plan_pro_grooming')
        total = sum(len(s['fields']) for s in tpl['sections'])
        pct, per = ob.progress(tpl, {'zones': [{'zone': 'Naples', 'zips': '', 'days': ''}]}, {})
        self.assertEqual(pct, 0)  # la zona precargada sola no cuenta
        pct, per = ob.progress(tpl, {'legal_name': 'X', 'zones': [{'zone': 'Naples', 'zips': '34102'}]}, {'logo': 1})
        self.assertEqual(per['legal'][0], 1)
        self.assertEqual(per['ops'][0], 1)
        self.assertEqual(per['brand'][0], 1)
        self.assertEqual(pct, round(3 * 100 / total))

    def test_submit_notifies_once_and_flags_admin(self):
        with patch('services.push.notify_event') as notify:
            r = self.post_save(self.client_b, {'legal_name': 'X'}, submit=True)
            self.assertEqual(r.get_json()['status'], 'submitted')
            self.post_save(self.client_b, {'slogan': 'Y'}, submit=True)
        self.assertEqual(notify.call_count, 1)
        row = self.row()
        self.assertEqual(row['status'], 'submitted')
        self.assertEqual(row['admin_seen'], 0)
        self.assertTrue(row['submitted_at'])
        # Después de enviado, un guardado normal mantiene el estado
        self.post_save(self.client_b, {'slogan': 'Z'})
        self.assertEqual(self.row()['status'], 'submitted')
        # El admin abre las respuestas → marcado como visto
        r = self.admin.get('/portal/admin/onboarding/32/respuestas')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Enviado', r.get_data(as_text=True))
        self.assertEqual(self.row()['admin_seen'], 1)

    def test_admin_preview_edit_does_not_notify(self):
        with patch('services.push.notify_event') as notify:
            r = self.post_save(self.admin, {'legal_name': 'Desde admin'}, preview=True, submit=True)
        self.assertEqual(r.status_code, 200)
        notify.assert_not_called()
        self.assertEqual(self.row()['admin_seen'], 1)
        r = self.admin.get('/portal/admin/onboarding/32')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('Vista de administrador', html)
        self.assertIn('value="Desde admin"', html)

    # ── Archivos y pantallas relacionadas ─────────────────────────────────
    def test_upload_from_section_returns_to_form(self):
        png = (b'\x89PNG\r\n\x1a\n' + b'0' * 64)
        r = self.client_b.post('/portal/upload', data={
            'client_id': '32', 'category': 'antes_despues', 'back': 'onboarding:content',
            'files': (io.BytesIO(png), 'max-antes.png')}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/portal/onboarding?c=32#content'), r.headers['Location'])
        from models import onboarding as ob
        self.assertEqual(ob.upload_counts(32, 1).get('antes_despues'), 1)
        html = self.client_b.get('/portal/onboarding').get_data(as_text=True)
        self.assertIn('max-antes.png', html)

    def test_portal_home_and_profile_show_onboarding(self):
        html = self.client_b.get('/portal/').get_data(as_text=True)
        self.assertIn('Comenzar onboarding', html)
        self.assertIn('/portal/onboarding?c=32', html)
        html = self.admin.get('/portal/admin/preview/32').get_data(as_text=True)
        self.assertIn('/portal/admin/onboarding/32', html)
        r = self.admin.get('/clients/32')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('Onboarding', html)
        self.assertIn('/portal/admin/onboarding/32/respuestas', html)


    # ── Plantilla según el tipo de negocio ─────────────────────────────────
    def test_template_guessed_per_business(self):
        from models import onboarding as ob
        self.assertEqual(ob.load(32, 1)['template_key'], 'plan_pro_grooming')
        self.assertEqual(ob.load(33, 1)['template_key'], 'web')
        self.assertEqual(ob.load(35, 1)['template_key'], 'crm')
        self.assertEqual(ob.load(36, 1)['template_key'], 'tienda')
        self.assertEqual(ob.load(37, 1)['template_key'], 'citas')
        html = self.admin.get('/portal/admin/onboarding/35').get_data(as_text=True)
        self.assertNotIn('groomer', html.lower())
        self.assertIn('Personas que usarán el sistema', html)

    def test_every_template_is_valid(self):
        from models import onboarding as ob
        for key, tpl in ob.TEMPLATES.items():
            keys = [f['key'] for _, f in ob._fields(tpl)]
            self.assertEqual(len(keys), len(set(keys)), key)
            cats = [f['category'] for _, f in ob._fields(tpl) if f['type'] == 'uploads']
            self.assertEqual(len(cats), len(set(cats)), key)
            if key != 'plan_pro_grooming':
                blob = json.dumps(tpl, ensure_ascii=False).lower()
                self.assertNotIn('groom', blob, key)
                self.assertNotIn('mascota', blob, key)

    def test_empty_grooming_rows_released_by_migration(self):
        db = database.get_db()
        db.execute("INSERT INTO client_onboarding(org_id, client_id, template_key, data) VALUES(1, 35, 'plan_pro_grooming', '{}')")
        db.execute("INSERT INTO client_onboarding(org_id, client_id, template_key, data) VALUES(1, 32, 'plan_pro_grooming', '{\"legal_name\": \"L\"}')")
        db.commit(); db.close()
        from models import onboarding as ob
        ob.ensure_schema()
        self.assertEqual(self.row(35)['template_key'], '')
        self.assertEqual(ob.load(35, 1)['template_key'], 'crm')
        self.assertEqual(self.row(32)['template_key'], 'plan_pro_grooming')

    def test_admin_changes_template_and_keeps_answers(self):
        self.post_save(self.admin, {'legal_name': 'Mabys LLC'}, cid=35, preview=True)
        self.assertEqual(self.row(35)['template_key'], 'crm')
        r = self.admin.post('/portal/admin/onboarding/35/plantilla', data={'template_key': 'web'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.row(35)['template_key'], 'web')
        self.assertEqual(json.loads(self.row(35)['data'])['legal_name'], 'Mabys LLC')
        self.assertEqual(self.admin.post('/portal/admin/onboarding/35/plantilla',
                                         data={'template_key': 'nope'}).status_code, 400)
        self.assertEqual(self.client_b.post('/portal/admin/onboarding/32/plantilla',
                                            data={'template_key': 'web'}).status_code, 302)
        self.assertEqual(self.row(32), None)
        html = self.admin.get('/clients/35').get_data(as_text=True)
        self.assertIn('Tipo de formulario', html)


if __name__ == '__main__':
    unittest.main()
