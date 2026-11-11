import sys, sqlite3, tempfile, importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import maintenance as m
from datetime import date
from flask import Flask, g, session, render_template_string
with tempfile.TemporaryDirectory() as tmp:
 def conn():
  c=sqlite3.connect(tmp+'/test.db'); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
 m.get_db=conn
 c=conn(); c.executescript('CREATE TABLE clients(id INTEGER PRIMARY KEY,org_id INTEGER,first_name TEXT,last_name TEXT,amount_paid REAL DEFAULT 0); INSERT INTO clients VALUES(1,1,"Prueba","Uno",0),(2,2,"Otra","Org",0); CREATE TABLE follow_ups(id INTEGER PRIMARY KEY,client_id INTEGER,org_id INTEGER,method TEXT,summary TEXT,result TEXT,next_date TEXT,next_at TEXT,reminder_comment TEXT,completed INTEGER DEFAULT 0);'); c.close()
 m.ensure_schema(); m.today=lambda:date(2026,10,22)
 data=dict(service='Hosting',amount='125.50',due_day=5,frequency=1,warranty_days=0,grace_days=30,state='pending',delivery_required=True,offsets='-5,0,3,7',method='Zelle',payee='Papia',payment_email='test@example.com')
 cfg=m.validate(data); m.save_plan(1,1,cfg,'test'); m.tick(as_of=date(2026,10,22)); assert not m.summary(1,1)['periods']
 def rejects(f):
  try: f()
  except ValueError: return
  raise AssertionError('Validation expected')
 rejects(lambda:m.validate(dict(data,state='active')))
 data.update(state='active',delivery_date='2026-08-01',delivery_evidence='Aceptación escrita TEST',activation_date='2026-08-31',billing_start='2026-08-31')
 cfg=m.validate(data); m.save_plan(1,1,cfg,'test'); m.tick(as_of=date(2026,10,22)); s=m.summary(1,1); assert len(s['periods'])==3
 m.tick(as_of=date(2026,10,22)); assert len(m.summary(1,1)['periods'])==3
 c=conn(); n=c.execute('SELECT COUNT(*) FROM maintenance_notices').fetchone()[0]; c.close(); m.tick(as_of=date(2026,10,22)); c=conn(); assert c.execute('SELECT COUNT(*) FROM maintenance_notices').fetchone()[0]==n; c.close()
 p=s['periods'][-1]; rejects(lambda:m.record_payment(1,2,p['id'],'125.50','2026-10-22','evidence','ref','test'))
 rejects(lambda:m.record_payment(1,1,p['id'],'125.50','2026-10-22','','ref','test'))
 m.record_payment(1,1,p['id'],'25.50','2026-10-22','bank test','ref1','test'); m.record_payment(1,1,p['id'],'100','2026-10-22','bank test','ref2','test')
 assert m.summary(1,1)['total_paid']==12550
 rejects(lambda:m.record_payment(1,1,p['id'],'1','2026-10-22','bank test','ref2','test'))
 c=conn(); assert c.execute('SELECT amount_paid FROM clients WHERE id=1').fetchone()[0]==0; assert c.execute('SELECT COUNT(*) FROM follow_ups WHERE completed=0 AND id IN (SELECT followup_id FROM maintenance_notices WHERE period_id=?)',(p['id'],)).fetchone()[0]==0; c.close()
 old=[(x['id'],x['due_date'],x['amount_cents']) for x in m.summary(1,1)['periods']]; data['amount']='200'; m.save_plan(1,1,m.validate(data),'test'); assert old==[(x['id'],x['due_date'],x['amount_cents']) for x in m.summary(1,1)['periods']]
 assert m.add_months(date(2026,1,31),1,31)==date(2026,2,28)
 rejects(lambda:m.save_plan(2,1,m.validate(data),'test'))
 app=Flask('test'); app.secret_key='test-only'; app.add_url_rule('/clients/<int:client_id>',endpoint='clients.detail',view_func=lambda client_id:'ok'); m.setup(app)
 @app.before_request
 def org(): g.org_id=1
 with app.test_request_context('/'):
  session['logged_in']=True; session['user_role']='admin'; g.org_id=1
  rendered=render_template_string('{{ maintenance_card(client)|safe }}',client={'id':1}); assert 'Mantenimiento mensual' in rendered and '125.50' in rendered
  rendered=render_template_string('{{ maintenance_card(client)|safe }}',client={'id':2}); assert 'Sin mantenimiento configurado' in rendered
 client=app.test_client(); assert client.post('/clients/1/maintenance/config').status_code==401
 with client.session_transaction() as s: s['logged_in']=True; s['user_role']='admin'; s['maintenance_csrf']='test-token'
 assert client.post('/clients/1/maintenance/config').status_code==400
 assert client.post('/clients/2/maintenance/config',data={'maintenance_csrf':'test-token'}).status_code==404
 with client.session_transaction() as s: s['user_role']='user'
 assert client.post('/clients/1/maintenance/config',data={'maintenance_csrf':'test-token'}).status_code==403
 print('PASS: activation, deduplication, reminders, receipts, tenant isolation, history, short months, rendering, authentication, CSRF, roles')
