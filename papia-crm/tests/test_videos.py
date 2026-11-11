"""Run from papia-crm: python -m unittest discover -s tests -v. No Google calls."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import database

_tmp = tempfile.TemporaryDirectory()
database.DATABASE = os.path.join(_tmp.name, 'test.db')
from app import app
from migrate_videos import migrate
from services import video_drive as drive


class VideosTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret')
        self.browser = app.test_client()
        with self.browser.session_transaction() as state:
            state.update(logged_in=True, user_role='admin', org_id=1, videos_csrf='csrf')
        self.headers = {'X-Videos-CSRF': 'csrf'}
        db = database.get_db()
        db.execute('DELETE FROM videos')
        db.execute('DELETE FROM videos_uploads')
        db.execute('DELETE FROM drive_carpetas')
        db.execute('DELETE FROM gmail_tokens')
        db.execute("INSERT OR IGNORE INTO organizations(id,name,slug) VALUES(2,'Other','other')")
        for ident, org in [(901,1), (902,2)]:
            db.execute("INSERT OR IGNORE INTO clients(id,first_name,last_name,email,project_type,org_id) VALUES(?,'Cliente','Prueba','test@example.com','other',?)", (ident,org))
        db.commit()
        db.close()

    def post(self, path, data):
        return self.browser.post('/videos/api/'+path, json=data, headers=self.headers)

    def seed_video(self, org=1, customer=901):
        db = database.get_db()
        result = db.execute("INSERT INTO videos(org_id,cliente_id,titulo,drive_file_id,drive_folder_id,mime_type,size_bytes,tipo) VALUES(?,?,'Clip','file12345','folder','video/mp4',10,'original')",(org,customer))
        db.commit(); ident = result.lastrowid; db.close(); return ident

    def test_migration_idempotent(self):
        migrate(); migrate()
        db = database.get_db()
        self.assertIn('deleted_at', [r['name'] for r in db.execute('PRAGMA table_info(videos)')])
        db.close()

    def test_guard_all_routes(self):
        paths = ['/videos','/videos/editor','/videos/editor/1','/videos/cliente/901', '/videos/auth',
                 '/videos/oauth2callback','/videos/api/token']
        for role, expected in [('user',403),(None,302)]:
            with self.browser.session_transaction() as state:
                state['logged_in'] = role is not None; state['user_role']=role
            for path in paths:
                self.assertEqual(self.browser.get(path).status_code,expected,path)
            for path in ['upload-session','registrar','1/eliminar']:
                self.assertEqual(self.post(path,{}).status_code,expected,path)

    def test_csrf_and_metadata_only(self):
        self.assertEqual(self.browser.post('/videos/api/upload-session',json={}).status_code,403)
        self.assertEqual(self.browser.post('/videos/api/upload-session',data=b'x'*33000,headers=self.headers).status_code,413)
        self.assertEqual(self.post('upload-session',[]).status_code,400)

    @patch('services.video_drive.credentials',side_effect=drive.DriveError('Reconecta Google Drive',401,True))
    def test_missing_scope_pages_and_token(self, _):
        result = self.browser.get('/videos')
        self.assertEqual(result.status_code,200)
        self.assertIn(b'Conectar Google Drive',result.data)
        self.assertEqual(self.browser.get('/videos/editor').status_code,200)
        response = self.browser.get('/videos/api/token')
        self.assertEqual(response.status_code,401)
        self.assertTrue(response.json['reconnect'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_scope_checked_on_refreshed_token(self):
        creds = SimpleNamespace(token='token')
        with app.test_request_context('/videos'):
            from flask import g
            g.org_id=1
            with patch('services.video_drive._load_creds',return_value=creds), patch('services.video_drive.token_info',return_value={'scope':'gmail.send'}):
                with self.assertRaises(drive.DriveError): drive.credentials()

    @patch('services.video_drive.credentials',return_value=(SimpleNamespace(token='token'),{}))
    def test_org_isolation(self, _):
        ident=self.seed_video(2,902)
        self.assertEqual(self.browser.get(f'/videos/editor/{ident}').status_code,404)
        self.assertEqual(self.post(f'{ident}/eliminar',{}).status_code,404)
        self.assertEqual(self.browser.get('/videos/cliente/902').status_code,404)
        self.assertEqual(self.post('upload-session',{'nombre':'x','size':10,'mime_type':'video/mp4','cliente_id':902}).status_code,404)

    @patch('services.video_drive.credentials',return_value=(SimpleNamespace(token='token'),{}))
    @patch('services.video_drive.ensure_folder',return_value='folder')
    @patch('routes.videos.requests.post')
    def test_upload_origin_and_registration_verified(self, post, folders, creds):
        post.return_value=SimpleNamespace(ok=True,status_code=200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=test'})
        self.browser.set_cookie('session', self.browser.get_cookie('session').value, domain='datos.papiatech.com')
        response=self.browser.post('/videos/api/upload-session',json={'nombre':'Clip.mp4','size':10,'mime_type':'video/mp4','cliente_id':901},headers=self.headers,base_url='https://datos.papiatech.com')
        self.assertEqual(response.status_code,200)
        kwargs=post.call_args.kwargs
        self.assertEqual(kwargs['headers']['Origin'],'https://datos.papiatech.com')
        self.assertEqual(kwargs['json']['parents'],['folder'])
        self.assertNotIn('files',kwargs)
        nonce=response.json['upload_id']
        metadata={'name':'Clip.mp4','size':'10','mimeType':'video/mp4','parents':['folder'],
                  'appProperties':{'papiaVideosUpload':nonce,'papiaVideosOrg':'1'}}
        body={'drive_file_id':'file12345','upload_id':nonce,'tipo':'original','duracion_seg':2}
        with patch('services.video_drive.call',return_value={**metadata,'parents':['wrong']}):
            self.assertEqual(self.post('registrar',body).status_code,400)
        with patch('services.video_drive.call',return_value=metadata):
            saved=self.post('registrar',body)
            self.assertEqual(saved.status_code,201)
            self.assertEqual(self.post('registrar',body).json['id'],saved.json['id'])
        db=database.get_db()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],1)
        db.close()

    @patch('services.video_drive.credentials',return_value=(SimpleNamespace(token='token'),{}))
    @patch('services.video_drive.call',return_value={})
    def test_trash_soft_delete(self, call, _):
        ident=self.seed_video()
        self.assertEqual(self.post(f'{ident}/eliminar',{}).status_code,200)
        self.assertEqual(call.call_args.args[:2],('PATCH','/files/file12345'))
        self.assertEqual(call.call_args.kwargs['json'],{'trashed':True})
        self.assertEqual(self.browser.get(f'/videos/editor/{ident}').status_code,404)
        db=database.get_db();self.assertIsNotNone(db.execute('SELECT deleted_at FROM videos WHERE id=?',(ident,)).fetchone()[0]);db.close()

    @patch('services.video_drive.credentials',return_value=(SimpleNamespace(token='token'),{}))
    @patch('services.video_drive.ensure_folder',return_value='folder')
    @patch('routes.videos.requests.post')
    def test_photo_uploads_use_direct_drive_sessions_and_reject_markup(self, post, folders, creds):
        post.return_value=SimpleNamespace(ok=True,status_code=200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=photo'})
        for mime in ['image/jpeg','image/png','image/webp','image/heic','image/heif']:
            response=self.post('upload-session',{'nombre':'Foto','size':10,'mime_type':mime})
            self.assertEqual(response.status_code,200,mime)
            self.assertEqual(post.call_args.kwargs['headers']['X-Upload-Content-Type'],mime)
        self.assertEqual(self.post('upload-session',{'nombre':'Markup','size':10,'mime_type':'image/svg+xml'}).status_code,400)

    def test_folder_creation_and_recovery(self):
        with app.test_request_context('/videos'):
            from flask import g
            g.org_id=1
            calls=[]
            def fake(method,path,creds,**kwargs):
                calls.append((method,path,kwargs))
                if method=='GET' and path=='/files':return {'files':[]}
                if method=='GET':return {'id':path.split('/')[-1],'mimeType':'application/vnd.google-apps.folder','parents':['root']}
                return {'id':'root' if kwargs['json']['name']=='PapiaTech Videos' else 'child'}
            with patch('services.video_drive.call',side_effect=fake):
                self.assertEqual(drive.ensure_folder(SimpleNamespace()),'child')
                self.assertEqual(drive.ensure_folder(SimpleNamespace()),'child')
            self.assertEqual(len([c for c in calls if c[0]=='POST']),2)
            self.assertEqual([c for c in calls if c[0]=='POST'][1][2]['json']['name'],'Sin cliente')

    def test_oauth_state_and_incremental_scope(self):
        flow=SimpleNamespace(code_verifier='verifier',authorization_url=lambda **kw: ('https://accounts.google.com/test','state'))
        with patch('google_auth_oauthlib.flow.Flow.from_client_config',return_value=flow) as factory:
            result=self.browser.get('/videos/auth')
            self.assertEqual(result.status_code,302)
            self.assertEqual(factory.call_args.kwargs['scopes'],[drive.DRIVE_SCOPE])
        self.assertEqual(self.browser.get('/videos/oauth2callback?state=wrong').status_code,400)

    def test_oauth_preserves_refresh_and_rejects_account_change(self):
        db=database.get_db();db.execute("INSERT INTO gmail_tokens(access_token,refresh_token,org_id) VALUES('old','keep-refresh',1)");db.commit();db.close()
        flow=SimpleNamespace(code_verifier=None,credentials=SimpleNamespace(token='new',refresh_token=None),fetch_token=lambda **kw:None)
        for subject, expected in [('different','old'),('same','new')]:
            with self.browser.session_transaction() as state:
                state['videos_oauth']={'state':'ok','org':1,'started':time.time(),'verifier':'v'}
            info={'sub':subject,'scope':drive.DRIVE_SCOPE+' gmail.send'}
            with patch('google_auth_oauthlib.flow.Flow.from_client_config',return_value=flow), patch('services.video_drive.token_info',return_value=info),patch('services.video_drive.verified_credentials',return_value=(SimpleNamespace(token='old'),{'sub':'same','scope':'gmail.send'})):
                self.assertEqual(self.browser.get('/videos/oauth2callback?state=ok&code=code').status_code,302)
            db=database.get_db();row=db.execute('SELECT * FROM gmail_tokens WHERE org_id=1').fetchone();db.close()
            self.assertEqual(row['access_token'],expected);self.assertEqual(row['refresh_token'],'keep-refresh')

    def test_expired_access_token_is_refreshed_once_and_persisted(self):
        from flask import g
        from unittest.mock import Mock
        db=database.get_db()
        db.execute("INSERT INTO gmail_tokens(access_token,refresh_token,org_id) VALUES('expired','keep-refresh',1)")
        db.commit();db.close()
        creds=SimpleNamespace(token='expired')
        def renew(_): creds.token='renewed'
        creds.refresh=Mock(side_effect=renew)
        info={'sub':'same','scope':drive.DRIVE_SCOPE+' gmail.send'}
        with app.test_request_context('/videos'):
            g.org_id=1
            with patch('services.video_drive._load_creds',return_value=creds), patch('services.video_drive.token_info',side_effect=[drive.DriveError('Reconecta Google Drive',401,True),info]) as inspect:
                result, actual=drive.credentials()
                self.assertIs(result,creds);self.assertEqual(actual,info)
                self.assertEqual([c.args[0] for c in inspect.call_args_list],['expired','renewed'])
                creds.refresh.assert_called_once()
        db=database.get_db();row=db.execute('SELECT * FROM gmail_tokens WHERE org_id=1').fetchone();db.close()
        self.assertEqual(row['access_token'],'renewed');self.assertEqual(row['refresh_token'],'keep-refresh')

    def test_revoked_refresh_does_not_overwrite_tokens(self):
        from google.auth.exceptions import RefreshError
        from unittest.mock import Mock
        db=database.get_db()
        db.execute("INSERT INTO gmail_tokens(access_token,refresh_token,org_id) VALUES('expired','keep-refresh',1)")
        db.commit();db.close()
        creds=SimpleNamespace(token='expired',refresh=Mock(side_effect=RefreshError('revoked')))
        with patch('services.video_drive._load_creds',return_value=creds), patch('services.video_drive.token_info',side_effect=drive.DriveError('Reconecta Google Drive',401,True)):
            with self.assertRaises(drive.DriveError) as caught: drive.verified_credentials(1)
        self.assertTrue(caught.exception.reconnect)
        creds.refresh.assert_called_once()
        db=database.get_db();row=db.execute('SELECT * FROM gmail_tokens WHERE org_id=1').fetchone();db.close()
        self.assertEqual(row['access_token'],'expired');self.assertEqual(row['refresh_token'],'keep-refresh')

    def test_oauth_reconnects_after_existing_access_token_expires(self):
        from unittest.mock import Mock
        db=database.get_db()
        db.execute("INSERT INTO gmail_tokens(access_token,refresh_token,org_id) VALUES('expired','keep-refresh',1)")
        db.commit();db.close()
        with self.browser.session_transaction() as state:
            state['videos_oauth']={'state':'ok','org':1,'started':time.time(),'verifier':'v'}
        flow=SimpleNamespace(credentials=SimpleNamespace(token='new-drive',refresh_token=None),fetch_token=lambda **kw:None)
        previous=SimpleNamespace(token='expired')
        def renew(_): previous.token='renewed-gmail'
        previous.refresh=Mock(side_effect=renew)
        def inspect(token):
            if token=='expired':raise drive.DriveError('Reconecta Google Drive',401,True)
            return {'sub':'same','scope':'gmail.send'+(' '+drive.DRIVE_SCOPE if token=='new-drive' else '')}
        with patch('google_auth_oauthlib.flow.Flow.from_client_config',return_value=flow), patch('services.video_drive._load_creds',return_value=previous),patch('services.video_drive.token_info',side_effect=inspect):
            response=self.browser.get('/videos/oauth2callback?state=ok&code=code')
        self.assertEqual(response.status_code,302);previous.refresh.assert_called_once()
        db=database.get_db();row=db.execute('SELECT * FROM gmail_tokens WHERE org_id=1').fetchone();db.close()
        self.assertEqual(row['access_token'],'new-drive');self.assertEqual(row['refresh_token'],'keep-refresh')

    def test_transient_token_check_failure_does_not_refresh(self):
        from unittest.mock import Mock
        creds=SimpleNamespace(token='still-valid',refresh=Mock())
        with patch('services.video_drive._load_creds',return_value=creds), patch('services.video_drive.token_info',side_effect=drive.DriveError('Google no responde.')):
            with self.assertRaises(drive.DriveError) as caught: drive.verified_credentials(1)
        self.assertFalse(caught.exception.reconnect);creds.refresh.assert_not_called()

    @patch('services.video_drive.credentials',side_effect=drive.DriveError('Reconecta Google Drive',401,True))
    def test_existing_views_still_render(self, _):
        for path in ['/','/clients/','/clients/901','/pipeline/','/emails','/followups/','/calendar/','/tasks/','/whatsapp','/payments/client/901','/admin/settings']:
            result=self.browser.get(path)
            self.assertIn(result.status_code,(200,302),path)


if __name__=='__main__':
    unittest.main()
