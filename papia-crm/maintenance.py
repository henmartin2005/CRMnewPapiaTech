"""Mantenimiento recurrente: libro separado, versiones y avisos internos."""
import calendar
import hmac
import json
import secrets
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, flash, g, redirect, render_template_string, request, session, url_for
from database import get_db

TZ = ZoneInfo('America/New_York')
STATES = {'pending': 'Pendiente de activación', 'active': 'Activo', 'suspended': 'Suspendido', 'cancelled': 'Cancelado'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS maintenance_plans (
 id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, client_id INTEGER NOT NULL REFERENCES clients(id),
 config TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(org_id,client_id));
CREATE TABLE IF NOT EXISTS maintenance_versions (
 id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
 config TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS maintenance_periods (
 id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
 org_id INTEGER NOT NULL, client_id INTEGER NOT NULL, period TEXT NOT NULL,
 due_date TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 service TEXT NOT NULL, method TEXT NOT NULL, payee TEXT NOT NULL, payment_email TEXT NOT NULL,
 version_id INTEGER NOT NULL REFERENCES maintenance_versions(id), voided INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(plan_id,period));
CREATE TABLE IF NOT EXISTS maintenance_receipts (
 id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES maintenance_periods(id),
 org_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 paid_on TEXT NOT NULL, evidence TEXT NOT NULL, reference TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(org_id,reference));
CREATE TABLE IF NOT EXISTS maintenance_notices (
 id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES maintenance_periods(id),
 offset_days INTEGER NOT NULL, followup_id INTEGER NOT NULL REFERENCES follow_ups(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(period_id,offset_days));
CREATE INDEX IF NOT EXISTS maintenance_period_due ON maintenance_periods(org_id,due_date);
'''

def today():
    return datetime.now(TZ).date()

def ensure_schema():
    with closing(get_db()) as db:
        db.executescript(SCHEMA)
        db.commit()

def cents(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d <= 0 or d > 10000000 or d != d.quantize(Decimal('.01')):
            raise ValueError('Indica un importe positivo con máximo dos decimales.')
        return int(d * 100)
    except (InvalidOperation, TypeError):
        raise ValueError('Importe inválido.')

def iso(value):
    return date.fromisoformat(value).isoformat() if value else ''

def get_plan(client_id, org_id, db=None):
    if db is None:
        with closing(get_db()) as conn:
            return get_plan(client_id, org_id, conn)
    row = db.execute('SELECT * FROM maintenance_plans WHERE client_id=? AND org_id=?', (client_id, org_id)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['data'] = json.loads(result['config'])
    return result

def validate(data):
    c = dict(data)
    c['amount_cents'] = cents(c.pop('amount'))
    c['due_day'] = int(c.get('due_day', 1))
    c['frequency'] = int(c.get('frequency', 1))
    c['warranty_days'] = int(c.get('warranty_days', 0))
    c['grace_days'] = int(c.get('grace_days', 0))
    c['proposed_day'] = int(c.get('proposed_day') or 0)
    if not 1 <= c['due_day'] <= 31 or c['frequency'] not in (1,3,12):
        raise ValueError('Día o frecuencia inválidos.')
    if not 0 <= c['proposed_day'] <= 31 or not 0 <= c['warranty_days'] <= 3650 or not 0 <= c['grace_days'] <= 3650:
        raise ValueError('Plazo inválido.')
    if c.get('state') not in STATES or not c.get('service','').strip():
        raise ValueError('Indica servicio y estado válidos.')
    for field in ('activation_date','billing_start','delivery_date'):
        c[field] = iso(c.get(field,''))
    offsets = [int(x.strip()) for x in str(c.get('offsets','-5,0,3,7')).split(',')]
    if not offsets or len(offsets)>12 or any(abs(x)>365 for x in offsets):
        raise ValueError('Avisos: hasta 12 plazos entre -365 y 365 días.')
    c['offsets'] = ','.join(str(x) for x in sorted(set(offsets)))
    if c.get('delivery_required'):
        if c['state']=='active' and (not c['delivery_date'] or not c.get('delivery_evidence','').strip()):
            raise ValueError('Para activar se requiere fecha y evidencia de entrega aceptada.')
        if c['delivery_date']:
            minimum = date.fromisoformat(c['delivery_date']) + timedelta(days=c['grace_days'])
            if c['billing_start'] and date.fromisoformat(c['billing_start']) < minimum:
                raise ValueError('El inicio debe respetar los días posteriores a la entrega aceptada.')
    if c['state']=='active' and (not c['billing_start'] or not c['activation_date']):
        raise ValueError('Para activar indica fecha de activación e inicio de facturación.')
    if c['billing_start'] and c['activation_date'] and c['billing_start'] < c['activation_date']:
        raise ValueError('La facturación no puede preceder a la activación.')
    return c

def save_plan(client_id, org_id, config, actor, expected=None):
    with closing(get_db()) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM clients WHERE id=? AND org_id=?',(client_id,org_id)).fetchone():
                raise ValueError('Cliente no encontrado.')
            old = get_plan(client_id,org_id,db)
            current_version = db.execute('SELECT COALESCE(MAX(id),0) FROM maintenance_versions WHERE plan_id=?',(old['id'],)).fetchone()[0] if old else 0
            if expected is not None and current_version != expected:
                raise ValueError('La configuración cambió. Recarga el perfil antes de guardar.')
            if old and old['data']['due_day'] != config['due_day'] and not config.get('amendment_evidence','').strip():
                raise ValueError('El cambio de vencimiento requiere referencia a la aceptación escrita de ambas partes.')
            encoded = json.dumps(config,ensure_ascii=False,sort_keys=True)
            if old and old['config']==encoded:
                return old['id']
            db.execute('INSERT INTO maintenance_plans(org_id,client_id,config) VALUES(?,?,?) ON CONFLICT(org_id,client_id) DO UPDATE SET config=excluded.config,updated_at=CURRENT_TIMESTAMP',(org_id,client_id,encoded))
            plan = get_plan(client_id,org_id,db)
            db.execute('INSERT INTO maintenance_versions(plan_id,config,actor) VALUES(?,?,?)',(plan['id'],encoded,str(actor)))
            if config['state'] in ('suspended','cancelled'):
                db.execute('UPDATE maintenance_periods SET voided=1 WHERE plan_id=? AND due_date>? AND NOT EXISTS(SELECT 1 FROM maintenance_receipts r WHERE r.period_id=maintenance_periods.id)',(plan['id'],today().isoformat()))
                db.execute('UPDATE follow_ups SET completed=1 WHERE id IN (SELECT n.followup_id FROM maintenance_notices n JOIN maintenance_periods p ON p.id=n.period_id WHERE p.plan_id=? AND p.voided=1)',(plan['id'],))
            return plan['id']

def add_months(d,months,day):
    n=d.year*12+d.month-1+months
    year,month=divmod(n,12)
    return date(year,month+1,min(day,calendar.monthrange(year,month+1)[1]))

def tick(org_id=None, as_of=None):
    """Idempotente. Ejecutar diariamente; no envía mensajes ni suspende servicios."""
    now=as_of or today()
    with closing(get_db()) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('SELECT * FROM maintenance_plans'+(' WHERE org_id=?' if org_id is not None else ''), (org_id,) if org_id is not None else ()).fetchall()
            for row in rows:
                c=json.loads(row['config'])
                if c['state']!='active' or not c.get('billing_start') or not c.get('activation_date') or c['activation_date']>now.isoformat():
                    continue
                start=date.fromisoformat(c['billing_start'])
                due=add_months(start,0,c['due_day'])
                if due<start:
                    due=add_months(start,c['frequency'],c['due_day'])
                version=db.execute('SELECT MAX(id) FROM maintenance_versions WHERE plan_id=?',(row['id'],)).fetchone()[0]
                last=db.execute('SELECT MAX(period) FROM maintenance_periods WHERE plan_id=?',(row['id'],)).fetchone()[0]
                if last:
                    # Future periods already issued keep their original snapshots.
                    anchor=date.fromisoformat(last+'-01')
                    due=add_months(anchor,c['frequency'],c['due_day'])
                for _ in range(1200):
                    if due>now+timedelta(days=40):
                        break
                    db.execute('INSERT OR IGNORE INTO maintenance_periods(plan_id,org_id,client_id,period,due_date,amount_cents,service,method,payee,payment_email,version_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(row['id'],row['org_id'],row['client_id'],due.strftime('%Y-%m'),due.isoformat(),c['amount_cents'],c['service'],c.get('method',''),c.get('payee',''),c.get('payment_email',''),version))
                    due=add_months(due,c['frequency'],c['due_day'])
                name=db.execute('SELECT first_name,last_name FROM clients WHERE id=? AND org_id=?',(row['client_id'],row['org_id'])).fetchone()
                name=' '.join(x or '' for x in name).strip()
                periods=db.execute('SELECT p.*, COALESCE((SELECT SUM(r.amount_cents) FROM maintenance_receipts r WHERE r.period_id=p.id),0) paid FROM maintenance_periods p WHERE plan_id=? AND voided=0',(row['id'],)).fetchall()
                for p in periods:
                    if p['paid']>=p['amount_cents']:
                        continue
                    due=date.fromisoformat(p['due_date'])
                    for offset in [int(x) for x in c['offsets'].split(',')]:
                        when=due+timedelta(days=offset)
                        if when>now or (offset<0 and now>=due):
                            continue
                        if db.execute('SELECT 1 FROM maintenance_notices WHERE period_id=? AND offset_days=?',(p['id'],offset)).fetchone():
                            continue
                        amount=(p['amount_cents']-p['paid'])/100
                        prefix='Mantenimiento próximo a vencer' if offset<0 else ('Hoy corresponde cobrar el mantenimiento' if offset==0 else ('Revisar mantenimiento vencido' if offset>=7 else 'Pago de mantenimiento pendiente'))
                        title=f'{prefix} — {name} — $'+f'{amount:,.2f} — {p["due_date"]}'
                        cur=db.execute("INSERT INTO follow_ups(client_id,org_id,method,summary,result,next_date,next_at,reminder_comment) VALUES(?,?,'phone',?,'',?,?,?)",(row['client_id'],row['org_id'],title,when.isoformat(),when.isoformat()+' 09:00:00','Aviso interno de mantenimiento. No autoriza suspensión automática. Período '+p['period']))
                        db.execute('INSERT INTO maintenance_notices(period_id,offset_days,followup_id) VALUES(?,?,?)',(p['id'],offset,cur.lastrowid))

def record_payment(client_id, org_id, period_id, amount, paid_on, evidence, reference, actor):
    amount=cents(amount)
    paid_on=iso(paid_on)
    if not paid_on or paid_on>today().isoformat() or not evidence.strip() or not reference.strip():
        raise ValueError('Indica fecha de recepción, referencia única y evidencia; no se aceptan fechas futuras.')
    with closing(get_db()) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            p=db.execute('SELECT * FROM maintenance_periods WHERE id=? AND client_id=? AND org_id=? AND voided=0',(period_id,client_id,org_id)).fetchone()
            if not p:
                raise ValueError('Período no encontrado.')
            paid=db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM maintenance_receipts WHERE period_id=?',(period_id,)).fetchone()[0]
            if amount>p['amount_cents']-paid:
                raise ValueError('El pago supera el saldo del período.')
            if db.execute('SELECT 1 FROM maintenance_receipts WHERE org_id=? AND reference=?',(org_id,reference.strip())).fetchone():
                raise ValueError('Esta referencia ya fue registrada.')
            db.execute('INSERT INTO maintenance_receipts(period_id,org_id,amount_cents,paid_on,evidence,reference,actor) VALUES(?,?,?,?,?,?,?)',(period_id,org_id,amount,paid_on,evidence.strip(),reference.strip(),str(actor)))
            if paid+amount==p['amount_cents']:
                db.execute('UPDATE follow_ups SET completed=1 WHERE id IN (SELECT followup_id FROM maintenance_notices WHERE period_id=?)',(period_id,))

def summary(client_id,org_id):
    with closing(get_db()) as db:
        plan=get_plan(client_id,org_id,db)
        if not plan:
            return None
        c=plan['data']
        periods=[dict(r) for r in db.execute('SELECT p.*, COALESCE((SELECT SUM(amount_cents) FROM maintenance_receipts r WHERE r.period_id=p.id),0) paid FROM maintenance_periods p WHERE client_id=? AND org_id=? ORDER BY due_date DESC,id DESC',(client_id,org_id))]
        receipts=[dict(r) for r in db.execute('SELECT r.*,p.period FROM maintenance_receipts r JOIN maintenance_periods p ON p.id=r.period_id WHERE p.client_id=? AND p.org_id=? ORDER BY r.id DESC',(client_id,org_id))]
        unpaid=[p for p in periods if not p['voided'] and p['paid']<p['amount_cents']]
        overdue=sum(p['amount_cents']-p['paid'] for p in unpaid if p['due_date']<today().isoformat())
        state=STATES[c['state']]
        if c['state']=='active' and unpaid:
            state='Vencido' if overdue else ('Pago pendiente' if any(p['due_date']<=today().isoformat() for p in unpaid) else 'Activo')
        next_due=min((p['due_date'] for p in unpaid),default='')
        if not next_due and c['state']=='active' and c.get('billing_start'):
            start=date.fromisoformat(c['billing_start'])
            next_date=add_months(start,0,c['due_day'])
            if next_date<start:
                next_date=add_months(start,c['frequency'],c['due_day'])
            if periods:
                last=max(x['period'] for x in periods)
                next_date=add_months(date.fromisoformat(last+'-01'),c['frequency'],c['due_day'])
            next_due=next_date.isoformat()
        plan.update(periods=periods,receipts=receipts,unpaid=unpaid,total_paid=sum(r['amount_cents'] for r in receipts),overdue=overdue,state_label=state,next_due=next_due,version=db.execute('SELECT COALESCE(MAX(id),0) FROM maintenance_versions WHERE plan_id=?',(plan['id'],)).fetchone()[0])
        return plan

bp=Blueprint('maintenance',__name__)

@bp.before_request
def guard():
    if not session.get('logged_in'):
        abort(401)
    if session.get('user_role') not in ('admin','superadmin'):
        abort(403)
    if not hmac.compare_digest(request.form.get('maintenance_csrf',''),session.get('maintenance_csrf','!')):
        abort(400,'Formulario expirado. Recarga el perfil.')
    with closing(get_db()) as db:
        if not db.execute('SELECT 1 FROM clients WHERE id=? AND org_id=?',(request.view_args['client_id'],g.org_id)).fetchone():
            abort(404)

@bp.post('/clients/<int:client_id>/maintenance/config')
def configure(client_id):
    try:
        old=get_plan(client_id,g.org_id)
        data={k:request.form.get(k,'').strip() for k in ('service','amount','due_day','frequency','activation_date','billing_start','delivery_date','delivery_evidence','warranty_days','grace_days','method','payee','payment_email','state','cancellation_terms','offsets','proposed_day','amendment_evidence')}
        data['delivery_required']=bool(old and old['data'].get('delivery_required')) or request.form.get('delivery_required')=='1'
        if old and old['data'].get('delivery_required'):
            data['grace_days']=max(30,int(data['grace_days']))
        save_plan(client_id,g.org_id,validate(data),session.get('user_id'),int(request.form.get('version','0')))
        tick(g.org_id)
        flash('Mantenimiento guardado. Los períodos anteriores conservan sus condiciones.','success')
    except (ValueError,TypeError) as e:
        flash(str(e),'danger')
    return redirect(url_for('clients.detail',client_id=client_id)+'#mantenimiento')

@bp.post('/clients/<int:client_id>/maintenance/payment')
def payment(client_id):
    try:
        record_payment(client_id,g.org_id,int(request.form.get('period_id','0')),request.form.get('amount'),request.form.get('paid_on'),request.form.get('evidence',''),request.form.get('reference',''),session.get('user_id'))
        flash('Pago registrado exclusivamente en mantenimiento.','success')
    except (ValueError,TypeError) as e:
        flash(str(e),'danger')
    return redirect(url_for('clients.detail',client_id=client_id)+'#mantenimiento')

CARD = '''
<div class="section-card" id="mantenimiento">
 <div class="card-header"><span class="card-header-left"><i class="bi bi-arrow-repeat"></i> Mantenimiento mensual</span></div>
 <div class="card-body" style="padding:16px;overflow:auto;max-height:900px">
 {% if p %}
 <strong>{{ c.service }}</strong><p class="num">{{ money(c.amount_cents) }} USD · {{ {1:'Mensual',3:'Trimestral',12:'Anual'}[c.frequency] }}</p>
 <p><b>Estado:</b> {{ p.state_label }}<br><b>Próximo vencimiento:</b> {{ p.next_due or 'Pendiente de activación / sin cuotas pendientes' }}<br><b>Día contractual:</b> {{ c.due_day }}<br><b>Activación:</b> {{ c.activation_date or 'Pendiente' }}<br><b>Inicio de facturación:</b> {{ c.billing_start or 'Pendiente' }}<br><b>Entrega aceptada:</b> {{ c.delivery_date or 'No acreditada' }}<br><b>Garantía:</b> {{ c.warranty_days }} días<br><b>Espera tras entrega:</b> {{ c.grace_days }} días</p>
 {% if c.proposed_day %}<p style="color:#b45309">Día {{ c.proposed_day }} propuesto: pendiente de aceptación escrita de ambas partes. Se mantiene el día {{ c.due_day }}.</p>{% endif %}
 <p><b>Total pagado:</b> <span class="num">{{ money(p.total_paid) }}</span><br><b>Saldo vencido:</b> <span class="num">{{ money(p.overdue) }}</span><br>{{ c.method }} · {{ c.payee }} · {{ c.payment_email }}</p>
 <p>{{ c.cancellation_terms }}</p>
 <details><summary>Períodos e historial de pagos ({{ p.periods|length }})</summary>
 {% for x in p.periods %}<p>{{ x.period }} · vence {{ x.due_date }}<br>{{ x.service }} · <span class="num">{{ money(x.amount_cents) }}</span> · {{ 'Anulado por suspensión/cancelación' if x.voided else ('Pagado' if x.paid>=x.amount_cents else 'Pendiente') }}<br>Recibido: <span class="num">{{ money(x.paid) }}</span> · {{ x.method }}</p>{% else %}<p>Sin períodos emitidos.</p>{% endfor %}
 {% for r in p.receipts %}<p>{{ r.paid_on }} · {{ r.period }} · <span class="num">{{ money(r.amount_cents) }}</span><br>Ref.: {{ r.reference }} · {{ r.evidence }}</p>{% endfor %}
 </details>
 {% if editable and p.unpaid %}<details style="margin-top:12px"><summary>Registrar un pago</summary><form method="post" action="{{ url_for('maintenance.payment',client_id=client.id) }}" class="sched-form">
 <input type="hidden" name="maintenance_csrf" value="{{ csrf }}"><label>Período<select class="form-control" name="period_id" required>{% for x in p.unpaid %}<option value="{{ x.id }}">{{ x.period }} · saldo {{ money(x.amount_cents-x.paid) }}</option>{% endfor %}</select></label>
 <label>Importe recibido USD<input class="form-control" type="number" name="amount" step="0.01" min="0.01" required></label><label>Fecha de recepción<input class="form-control" type="date" name="paid_on" max="{{ current_date }}" required></label><label>Referencia única Zelle / recibo<input class="form-control" name="reference" maxlength="200" required></label><label>Evidencia de recepción<textarea class="form-control" name="evidence" maxlength="2000" required placeholder="Referencia al comprobante o confirmación bancaria"></textarea></label><button class="btn btn-primary btn-sm">Registrar pago recibido</button></form></details>{% endif %}
 {% else %}<p>Sin mantenimiento configurado</p>{% endif %}
 {% if editable %}<details style="margin-top:12px"><summary>{{ 'Editar configuración / suspender o cancelar' if p else '+ Configurar mantenimiento' }}</summary>
 <form method="post" action="{{ url_for('maintenance.configure',client_id=client.id) }}" class="sched-form">
 <input type="hidden" name="maintenance_csrf" value="{{ csrf }}"><input type="hidden" name="version" value="{{ p.version if p else 0 }}">
 <label>Servicio<input class="form-control" name="service" required maxlength="300" value="{{ c.service or '' }}"></label>
 <label>Importe USD<input class="form-control" type="number" name="amount" step="0.01" min="0.01" required value="{{ '%.2f'|format(c.amount_cents/100) if p else '' }}"></label>
 <label>Día contractual<input class="form-control" type="number" name="due_day" min="1" max="31" required value="{{ c.due_day or 1 }}"></label>
 <label>Frecuencia<select class="form-control" name="frequency">{% for n,label in [(1,'Mensual'),(3,'Trimestral'),(12,'Anual')] %}<option value="{{ n }}" {{ 'selected' if c.frequency==n else '' }}>{{ label }}</option>{% endfor %}</select></label>
 {% for key,label in [('activation_date','Fecha de activación'),('billing_start','Inicio de facturación'),('delivery_date','Fecha de entrega aceptada')] %}<label>{{ label }}<input class="form-control" type="date" name="{{ key }}" value="{{ c.get(key,'') }}"></label>{% endfor %}
 <label><input type="checkbox" name="delivery_required" value="1" {{ 'checked' if c.delivery_required else '' }}> Requiere entrega aceptada para activar</label>
 <label>Evidencia de entrega aceptada<input class="form-control" name="delivery_evidence" maxlength="2000" value="{{ c.delivery_evidence or '' }}"></label>
 {% for key,label in [('warranty_days','Garantía (días)'),('grace_days','Días de espera tras entrega')] %}<label>{{ label }}<input class="form-control" type="number" name="{{ key }}" min="0" max="3650" value="{{ c.get(key,0) }}"></label>{% endfor %}
 {% for key,label in [('method','Método de pago'),('payee','Destinatario'),('payment_email','Correo de pago'),('cancellation_terms','Condiciones de cancelación'),('amendment_evidence','Referencia a aceptación escrita de ambas partes para cambio de día')] %}<label>{{ label }}<input class="form-control" name="{{ key }}" maxlength="2000" value="{{ c.get(key,'') }}"></label>{% endfor %}
 <label>Día propuesto pendiente de aceptación (vacío si no aplica)<input class="form-control" type="number" name="proposed_day" min="0" max="31" value="{{ c.proposed_day or '' }}"></label>
 <label>Estado<select class="form-control" name="state">{% for key,label in states.items() %}<option value="{{ key }}" {{ 'selected' if c.get('state','pending')==key else '' }}>{{ label }}</option>{% endfor %}</select></label>
 <label>Avisos internos, días relativos al vencimiento<input class="form-control" name="offsets" required value="{{ c.offsets or '-5,0,3,7' }}"></label>
 <small>Negativo: antes; 0: vencimiento; positivo: después. Cambiar estado aquí registra la gestión administrativa; no corta el hosting. Suspender o cancelar exige respetar el contrato. Los períodos ya emitidos conservan importe y fecha. Días 29–31 se ajustan al último día de meses cortos. Primer cobro: primer día habitual igual o posterior al inicio de facturación.</small>
 <button class="btn btn-primary btn-sm">Guardar configuración</button></form></details>{% endif %}
 </div></div>
'''

def setup(app):
    ensure_schema()
    app.register_blueprint(bp)
    @app.context_processor
    def maintenance_context():
        def maintenance_card(client):
            if not session.get('logged_in'):
                return ''
            p=summary(client['id'],g.org_id)
            session.setdefault('maintenance_csrf',secrets.token_urlsafe(32))
            return render_template_string(CARD,p=p,c=p['data'] if p else {},client=client,csrf=session['maintenance_csrf'],editable=session.get('user_role') in ('admin','superadmin'),states=STATES,money=lambda n:'$'+format(n/100,',.2f'),current_date=today().isoformat())
        return {'maintenance_card':maintenance_card}
    @app.cli.command('maintenance-tick')
    def maintenance_tick():
        tick()
        print('Mantenimiento: períodos y avisos internos actualizados.')
