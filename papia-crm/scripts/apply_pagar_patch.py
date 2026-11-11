"""Aplica (idempotente) el link de pago permanente /pagar/<token> en app.py,
el perfil del cliente y el portal. Ejecutar desde papia-crm/."""
import sys

changed = []


def patch(path, old, new, marker):
    s = open(path, encoding='utf-8').read()
    if marker in s:
        return
    if old not in s:
        sys.exit(f'ERROR: no encontré el punto de inserción en {path}:\n{old[:160]}')
    s = s.replace(old, new, 1)
    open(path, 'w', encoding='utf-8').write(s)
    changed.append(path)


# ── app.py ──
patch('app.py',
      "from routes.pagos import pagos_bp\n",
      "from routes.pagos import pagos_bp\nfrom routes.pagar import pagar_bp\n",
      "from routes.pagar import pagar_bp")
patch('app.py',
      "app.register_blueprint(pagos_bp)\n",
      "app.register_blueprint(pagos_bp)\napp.register_blueprint(pagar_bp)\n",
      "app.register_blueprint(pagar_bp)")
patch('app.py',
      "'payments.stripe_webhook', 'payments.success',",
      "'payments.stripe_webhook', 'payments.success', 'pagar.page', 'pagar.card',",
      "'pagar.page', 'pagar.card'")

# ── Perfil del cliente: botón "copiar link de pago" en cada cuota pendiente ──
D = 'templates/clients/detail.html'
patch(D,
      """          {% if p.status == 'paid' %}
          <form method="POST" style="margin:0;" onsubmit="return confirm('¿Volver a pendiente esta cuota?');\"""",
      """          {% if p.status != 'paid' %}
          <button type="button" class="btn btn-ghost btn-icon-xs" title="Copiar link de pago (Zelle o tarjeta)"
                  onclick="copyPayLink(this, '{{ pay_url(p.id) }}')"><i class="bi bi-link-45deg" style="color:#635BFF;"></i></button>
          {% endif %}
          {% if p.status == 'paid' %}
          <form method="POST" style="margin:0;" onsubmit="return confirm('¿Volver a pendiente esta cuota?');\"""",
      "copyPayLink(this")
patch(D,
      "let stripeQuoteTimer = null, stripeQuoteInit = false;",
      """function copyPayLink(btn, url) {
  navigator.clipboard.writeText(url).then(() => {
    const icon = btn.querySelector('i');
    icon.className = 'bi bi-check2';
    btn.title = 'Link copiado: ' + url;
    setTimeout(() => { icon.className = 'bi bi-link-45deg'; }, 1800);
  });
}

let stripeQuoteTimer = null, stripeQuoteInit = false;""",
      "function copyPayLink(")

# ── Portal del cliente: botón "Pagar" en cuotas pendientes ──
P = 'templates/portal/home.html'
patch(P,
      """<span class="tag {{ p.status }}">{{ 'Pagado' if p.status == 'paid' else 'Pendiente' }}</span></span></div>""",
      """<span class="tag {{ p.status }}">{{ 'Pagado' if p.status == 'paid' else 'Pendiente' }}</span>"""
      """{% if p.status != 'paid' %}<br><a class="paybtn" href="{{ pay_url(p.id) }}"><i class="bi bi-credit-card"></i> Pagar</a>{% endif %}</span></div>""",
      'class="paybtn"')
patch(P,
      ".tag.paid{",
      ".paybtn{display:inline-flex;align-items:center;gap:5px;margin-top:6px;padding:5px 12px;border-radius:99px;"
      "background:#635bff;color:#fff!important;font-size:12px;font-weight:700;text-decoration:none}"
      ".paybtn:hover{background:#4f46e5}\n.tag.paid{",
      ".paybtn{")

print('Archivos modificados:', ', '.join(changed) if changed else 'ninguno (ya estaba aplicado)')
