"""Pruebas de la pestaña Pagos y del cálculo de comisiones de Stripe.
Ejecutar desde papia-crm/:  python3 tests/test_pagos.py
Usa una base de datos temporal; no toca papia_crm.db."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
database.DATABASE = _tmp.name
os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_dummy')
os.environ['STRIPE_WEBHOOK_SECRET'] = ''

from services import stripe_fees as sf  # noqa: E402


def test_fee_math():
    q = sf.quote(250)
    assert q == {'net': 250.0, 'zelle': 250.0, 'card': 257.78, 'fee': 7.78,
                 'receive': 250.0, 'extra': 7.78, 'extra_pct': 3.11}, q
    for net in [0.5, 1, 9.99, 60, 90, 100, 250, 600, 1200, 1600, 12345.67]:
        card = sf.card_amount(net)
        assert round(card - sf.fee_for(card), 2) >= net, net
        assert round((card - 0.01) - sf.fee_for(card - 0.01), 2) < net, net  # mínimo exacto
    assert sf.card_amount(1200) == 1236.15
    assert sf.card_amount(60) == 62.10
    assert sf.card_amount(600) == 618.23
    assert sf.fee_for(257.78) == 7.78


def test_flow():
    import app as app_module
    from routes import payments as payments_mod

    flask_app = app_module.app
    # app.py carga el .env real con override: neutralizar las claves en la prueba.
    os.environ['STRIPE_WEBHOOK_SECRET'] = ''
    os.environ['STRIPE_SECRET_KEY'] = 'sk_test_dummy'
    flask_app.config['TESTING'] = True
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s['logged_in'] = True
        s['user_role'] = 'admin'
        s['org_id'] = 1
        s['user_id'] = 1

    db = database.get_db()
    cur = db.execute("INSERT INTO clients (first_name, last_name, email, phone, project_type, total_cost, amount_paid, org_id) "
                     "VALUES ('Ana','Pérez','ana@x.com','7865550000','website',1600,0,1)")
    cid = cur.lastrowid
    db.commit()
    db.close()

    from models.payment_schedule import add_installment, list_installments
    add_installment(cid, 1, 'Inicial', '2026-11-01', 600, create_task=False)
    add_installment(cid, 1, 'Pago 2', '2026-11-20', 250, create_task=False)
    add_installment(cid, 1, 'Pago 3', '2026-11-27', 250, create_task=False)
    insts = list_installments(cid, 1)

    r = c.get('/pagos?mes=2026-11')
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert 'Cuotas por cobrar' in html and '$257.78' in html and '$618.23' in html

    # Pago Zelle de la cuota inicial
    r = c.post('/pagos/manual', data={'client_id': cid, 'installment_id': insts[0]['id'],
                                     'amount': '600', 'method': 'zelle', 'paid_on': '2026-11-02'})
    assert r.status_code == 302
    # Pago suelto en efectivo
    c.post('/pagos/manual', data={'client_id': cid, 'amount': '50', 'method': 'cash', 'paid_on': '2026-11-05'})

    # Link de Stripe para "Pago 2" (Stripe simulado)
    class FakeSession:
        id = 'cs_test_1'
        url = 'https://checkout.stripe.com/c/pay/cs_test_1'
    captured = {}

    class FakeStripe:
        class checkout:
            class Session:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeSession()
    payments_mod._stripe = lambda key: FakeStripe
    r = c.post(f"/pagos/cuota/{insts[1]['id']}/link", json={'client_id': cid})
    d = r.get_json()
    assert d['success'] and d['charge'] == 257.78 and d['net_amount'] == 250 and d['pass_fees'], d
    assert '/pagar/' in d['url'], d
    token = d['url'].rsplit('/', 1)[1]
    assert c.post(f"/pagos/cuota/{insts[1]['id']}/link", json={'client_id': cid}).get_json()['url'] == d['url']

    # Página pública /pagar/<token> (sin sesión)
    pub = flask_app.test_client()
    r = pub.get(f'/pagar/{token}')
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and '$250.00' in html and '$257.78' in html and 'Pagar con tarjeta' in html
    assert pub.get('/pagar/no-existe').status_code == 404
    r = pub.post(f'/pagar/{token}/tarjeta')
    assert r.status_code == 303 and r.headers['Location'] == FakeSession.url, r.headers
    assert captured['line_items'][0]['price_data']['unit_amount'] == 25778
    assert 'custom_text' in captured
    assert captured['success_url'].endswith(f'/pagar/{token}?pagado=1')
    assert captured['cancel_url'].endswith(f'/pagar/{token}?cancelado=1')
    # Segundo clic: reutiliza el checkout abierto (no crea otro)
    captured.clear()
    r = pub.post(f'/pagar/{token}/tarjeta')
    assert r.status_code == 303 and not captured

    # Email de recordatorio con botón de tarjeta
    from models.payment_schedule import installment_with_client
    from services.payment_reminders import build_reminder
    with flask_app.app_context():
        subj, mail_html = build_reminder(installment_with_client(insts[1]['id'], cid, 1))
    assert f'/pagar/{token}' in mail_html and '257.78' in mail_html, mail_html[-800:]

    # Webhook: checkout completado, comisión real 7.78
    payments_mod.fetch_stripe_fee = lambda stripe, pi: (7.78, 250.00)
    evt = {'type': 'checkout.session.completed', 'data': {'object': {
        'id': 'cs_test_1', 'amount_total': 25778, 'currency': 'usd', 'payment_intent': 'pi_1',
        'metadata': {'client_id': str(cid), 'credit_amount': '250.00', 'installment_id': str(insts[1]['id'])}}}}
    for _ in range(2):  # Stripe puede reenviar el mismo evento
        r = c.post('/webhook/stripe', data=json.dumps(evt), content_type='application/json')
        assert r.status_code == 200, r.get_data(as_text=True)

    db = database.get_db()
    paid = db.execute("SELECT amount_paid FROM clients WHERE id=?", (cid,)).fetchone()[0]
    db.close()
    assert abs(paid - (600 + 50 + 250)) < 0.001, paid  # tarjeta abona 250, no 257.78; sin doble conteo

    insts = list_installments(cid, 1)
    assert [i['status'] for i in insts] == ['paid', 'paid', 'pending'], insts

    from models import payments_ledger as ledger
    k = ledger.kpis(1, '2026-11')
    assert k['received_net'] == 900.0 and k['fees'] == 7.78 and k['received_gross'] == 907.78, k
    assert k['pending_total'] == 250.0 and k['contracts_pending'] == 700.0, k

    r = c.get('/pagos?mes=2026-11&metodo=stripe')
    assert r.status_code == 200 and 'Pago 2' in r.get_data(as_text=True)
    r = c.get('/pagos/cotizar?neto=250')
    assert r.get_json()['card'] == 257.78

    # Borrar el pago Zelle de la cuota: vuelve a pendiente y resta del pagado
    rec = [h for h in ledger.history(1) if h['source'] == 'manual' and h['installment_id']][0]
    c.post(f"/pagos/manual/{rec['id']}/delete")
    db = database.get_db()
    paid = db.execute("SELECT amount_paid FROM clients WHERE id=?", (cid,)).fetchone()[0]
    db.close()
    assert abs(paid - 300) < 0.001, paid
    assert list_installments(cid, 1)[0]['status'] == 'pending'

    # Perfil del cliente sigue cargando con el modal nuevo
    r = c.get(f'/clients/{cid}')
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'stripePassFees' in html and 'copyPayLink(this' in html
    assert pub.get(f'/pagar/{token}').get_data(as_text=True).count('ya está pagada') == 1

    # Un usuario no admin no ve Pagos
    with c.session_transaction() as s:
        s['user_role'] = 'user'
    assert c.get('/pagos').status_code == 403


if __name__ == '__main__':
    try:
        test_fee_math()
        test_flow()
        print('OK — todas las pruebas de Pagos pasaron')
    finally:
        os.unlink(_tmp.name)
