"""Aplica (idempotente) la integración de la pestaña Pagos en app.py,
templates/base.html y templates/clients/detail.html. Ejecutar desde papia-crm/."""
import sys

changed = []


def patch(path, old, new, marker):
    s = open(path, encoding='utf-8').read()
    if marker in s:
        return
    if old not in s:
        sys.exit(f'ERROR: no encontré el punto de inserción en {path}:\n{old[:120]}')
    s = s.replace(old, new, 1)
    open(path, 'w', encoding='utf-8').write(s)
    changed.append(path)


# ── app.py ──
patch('app.py',
      "from routes.payments import payments_bp\n",
      "from routes.payments import payments_bp\nfrom routes.pagos import pagos_bp\n",
      "from routes.pagos import pagos_bp")
patch('app.py',
      "app.register_blueprint(payments_bp)\n",
      "app.register_blueprint(payments_bp)\napp.register_blueprint(pagos_bp)\n",
      "app.register_blueprint(pagos_bp)")

# ── base.html: enlace en el menú (solo admin), después de Pipeline ──
NAV = """
    {% if is_admin %}
    <li>
      <a href="{{ url_for('pagos.index') }}"
         class="sb-link {% if request.blueprint == 'pagos' %}active{% endif %}">
        <i class="bi bi-cash-coin" style="color:#16A34A;"></i>
        <span class="sb-label">Pagos</span>
      </a>
    </li>
    {% endif %}
"""
patch('templates/base.html',
      """        <span class="sb-label">Pipeline</span>
      </a>
    </li>
""",
      """        <span class="sb-label">Pipeline</span>
      </a>
    </li>
""" + NAV,
      "url_for('pagos.index')")

# ── detail.html: modal "Cobrar con Stripe" con precio Zelle vs tarjeta ──
D = 'templates/clients/detail.html'
patch(D,
      """          <label class="form-label">Monto a cobrar (USD)</label>""",
      """          <label class="form-label">Monto que quieres recibir (USD)</label>""",
      "Monto que quieres recibir (USD)")
patch(D,
      """                   value="{{ '%.2f' | format(client.pending) }}"
                   style="padding-left:22px;" />
          </div>
        </div>
""",
      """                   value="{{ '%.2f' | format(client.pending) }}"
                   style="padding-left:22px;" oninput="stripeQuote()" />
          </div>
          <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-top:8px;">
            <input type="checkbox" id="stripePassFees" checked onchange="stripeQuote()">
            El cliente paga la comisión de Stripe (precio con tarjeta)
          </label>
          <div id="stripeBreak" data-no-mask style="display:none;margin-top:8px;padding:10px 12px;border:1px solid var(--c-border);border-radius:var(--radius-sm);background:var(--c-surface-2);font-size:12.5px;line-height:1.7;"></div>
        </div>
""",
      'id="stripePassFees"')
patch(D,
      """  new bootstrap.Modal(document.getElementById('stripeModal')).show();
}
""",
      """  new bootstrap.Modal(document.getElementById('stripeModal')).show();
  stripeQuote(true);
}

let stripeQuoteTimer = null, stripeQuoteInit = false;
function stripeQuote(initial) {
  clearTimeout(stripeQuoteTimer);
  stripeQuoteTimer = setTimeout(async () => {
    const box = document.getElementById('stripeBreak');
    const net = parseFloat(document.getElementById('stripeAmount').value) || 0;
    if (net <= 0) { box.style.display = 'none'; return; }
    try {
      const r = await fetch('/pagos/cotizar?neto=' + net);
      if (!r.ok) { box.style.display = 'none'; return; }
      const q = await r.json();
      const cb = document.getElementById('stripePassFees');
      if (initial && !stripeQuoteInit) { cb.checked = !!q.pass_fees; stripeQuoteInit = true; }
      const f = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      if (cb.checked) {
        box.innerHTML = `Por Zelle: <b>${f(q.zelle)}</b><br>Con tarjeta (link de Stripe): <b style="color:#635BFF;">${f(q.card)}</b>`
          + ` <span style="color:var(--c-text-3);">(incluye comisión ${f(q.fee)})</span><br>Recibes: <b style="color:var(--c-success);">${f(q.receive)}</b>`;
      } else {
        const fee = Math.round((Math.round(net * q.fee_pct) / 100 + q.fee_fixed) * 100) / 100;
        box.innerHTML = `Cobro con tarjeta: <b>${f(net)}</b><br>Comisión de Stripe: <b style="color:var(--c-danger);">${f(fee)}</b><br>`
          + `Recibes: <b style="color:var(--c-success);">${f(net - fee)}</b>`;
      }
      box.style.display = '';
    } catch (e) { box.style.display = 'none'; }
  }, initial ? 0 : 250);
}
""",
      "function stripeQuote(")
patch(D,
      """      body:    JSON.stringify({ client_id: CLIENT_ID, amount, description: desc }),""",
      """      body:    JSON.stringify({ client_id: CLIENT_ID, amount, description: desc,
                               pass_fees: document.getElementById('stripePassFees').checked }),""",
      "pass_fees: document.getElementById('stripePassFees')")

print('Archivos modificados:', ', '.join(changed) if changed else 'ninguno (ya estaba aplicado)')
