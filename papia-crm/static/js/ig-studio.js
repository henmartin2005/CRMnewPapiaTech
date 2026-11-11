/* Instagram Studio: acciones de la pestaña (sin dependencias). */
(function () {
  'use strict';
  var root = document.querySelector('.igs');
  if (!root) return;
  var csrf = root.getAttribute('data-csrf') || '';
  var base = '/instagram-studio/api/';
  var toastEl = document.getElementById('igs-toast');
  var toastTimer;

  function toast(msg, isError) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.toggle('is-error', !!isError);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 4500);
  }

  function api(path, body) {
    return fetch(base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-IGS-CSRF': csrf },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin'
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ('Error ' + r.status));
        return data;
      });
    });
  }

  function busy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || 'Un momento…'; btn.disabled = true; }
    else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
  }

  // Sincronizar
  var syncBtn = root.querySelector('[data-igs-sync]');
  if (syncBtn) syncBtn.addEventListener('click', function () {
    busy(syncBtn, true, 'Sincronizando…');
    api('sync').then(function () {
      toast('Sincronizando publicaciones e insights. Esto tarda un par de minutos.');
      var tries = 0;
      var poll = setInterval(function () {
        fetch(base + 'sync/status', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (s) {
          if (!s.running || ++tries > 60) { clearInterval(poll); location.reload(); }
        });
      }, 5000);
    }).catch(function (e) { busy(syncBtn, false); toast(e.message, true); });
  });

  // Comentarios: abrir formulario de respuesta
  root.addEventListener('click', function (ev) {
    var open = ev.target.closest('[data-igs-open-reply]');
    if (open) {
      var art = open.closest('[data-comment]');
      var form = art.querySelector('[data-igs-reply]');
      form.hidden = false;
      art.querySelector('[data-igs-row-actions]').hidden = true;
      form.querySelector('textarea').focus();
      return;
    }
    var cancel = ev.target.closest('[data-igs-cancel]');
    if (cancel) {
      var a = cancel.closest('[data-comment]');
      a.querySelector('[data-igs-reply]').hidden = true;
      a.querySelector('[data-igs-row-actions]').hidden = false;
      return;
    }
    var act = ev.target.closest('[data-igs-action]');
    if (act) {
      var cid = act.closest('[data-comment]').getAttribute('data-comment');
      busy(act, true);
      api('comments/' + encodeURIComponent(cid) + '/' + act.getAttribute('data-igs-action'))
        .then(function () { location.reload(); })
        .catch(function (e) { busy(act, false); toast(e.message, true); });
    }
  });

  root.addEventListener('submit', function (ev) {
    var form = ev.target.closest('[data-igs-reply]');
    if (!form) return;
    ev.preventDefault();
    var cid = form.closest('[data-comment]').getAttribute('data-comment');
    var btn = form.querySelector('[type="submit"]');
    var text = form.querySelector('textarea').value.trim();
    if (!text) { toast('Escribe la respuesta.', true); return; }
    busy(btn, true, 'Publicando…');
    api('comments/' + encodeURIComponent(cid) + '/' + btn.getAttribute('data-mode'), { text: text })
      .then(function () { toast('Respuesta publicada en Instagram.'); setTimeout(function () { location.reload(); }, 700); })
      .catch(function (e) { busy(btn, false); toast(e.message, true); });
  });

  // Agente por publicación
  root.querySelectorAll('[data-igs-media-agent]').forEach(function (sel) {
    sel.addEventListener('change', function () {
      api('media/' + encodeURIComponent(sel.getAttribute('data-igs-media-agent')) + '/agent', { agent: sel.value })
        .then(function () { toast('Agente de la publicación actualizado.'); })
        .catch(function (e) { toast(e.message, true); });
    });
  });

  // Encender/apagar agente por defecto
  root.querySelectorAll('[data-igs-toggle-agent]').forEach(function (sw) {
    sw.addEventListener('click', function () {
      var on = sw.getAttribute('aria-checked') !== 'true';
      api('agents/' + sw.getAttribute('data-igs-toggle-agent'), { enabled: on }).then(function () {
        sw.setAttribute('aria-checked', on ? 'true' : 'false');
        toast(on ? 'Agente activado.' : 'Agente apagado: los comentarios quedan para ti.');
      }).catch(function (e) { toast(e.message, true); });
    });
  });

  // Guardar agente
  var agentForm = root.querySelector('[data-igs-agent-form]');
  if (agentForm) agentForm.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var data = {};
    agentForm.querySelectorAll('input[name], textarea[name], select[name]').forEach(function (el) {
      data[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    });
    var btn = agentForm.querySelector('[type="submit"]');
    busy(btn, true, 'Guardando…');
    api('agents/' + agentForm.getAttribute('data-igs-agent-form'), data)
      .then(function () { busy(btn, false); toast('Agente guardado.'); })
      .catch(function (e) { busy(btn, false); toast(e.message, true); });
  });

  root.querySelectorAll('[data-igs-new-agent]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var kind = btn.getAttribute('data-igs-new-agent');
      busy(btn, true, 'Creando…');
      api('agents', { name: kind === 'comments' ? 'Nuevo agente de comentarios' : 'Nuevo agente de DMs', kind: kind, enabled: false })
        .then(function (d) { location.href = '/instagram-studio/agente?id=' + d.id; })
        .catch(function (e) { busy(btn, false); toast(e.message, true); });
    });
  });

  // Probar agente
  var testBtn = root.querySelector('[data-igs-test]');
  if (testBtn) testBtn.addEventListener('click', function () {
    var text = root.querySelector('#ag-test').value.trim();
    var out = root.querySelector('[data-igs-test-out]');
    if (!text) { toast('Escribe un comentario de prueba.', true); return; }
    busy(testBtn, true, 'Pensando…');
    api('agents/' + testBtn.getAttribute('data-igs-test') + '/test', { text: text }).then(function (d) {
      busy(testBtn, false);
      var x = d.decision || {};
      var labels = { responder: 'Responder', responder_dm: 'Responder + DM', revisar: 'Borrador para revisar', ignorar: 'Ignorar' };
      out.textContent = '';
      [['Acción', labels[x.accion] || x.accion], ['Categoría', x.categoria], ['Respuesta pública', x.respuesta_publica],
       ['DM', x.mensaje_dm], ['Borrador', x.borrador], ['Crear lead', x.crear_lead ? 'Sí' : 'No']]
        .forEach(function (row) {
          if (!row[1]) return;
          var p = document.createElement('div');
          var b = document.createElement('b');
          b.textContent = row[0] + ': ';
          p.appendChild(b);
          p.appendChild(document.createTextNode(row[1]));
          out.appendChild(p);
        });
      out.hidden = false;
    }).catch(function (e) { busy(testBtn, false); toast(e.message, true); });
  });

  // Estrategia
  root.querySelectorAll('[data-igs-generate]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      busy(btn, true, 'Generando… (hasta 1 min)');
      api('strategy/generate').then(function () { location.reload(); })
        .catch(function (e) { busy(btn, false); toast(e.message, true); });
    });
  });

  var emailBtn = root.querySelector('[data-igs-email]');
  if (emailBtn) emailBtn.addEventListener('click', function () {
    busy(emailBtn, true, 'Enviando…');
    api('strategy/email').then(function (d) { busy(emailBtn, false); toast('Reporte enviado a ' + d.to + '.'); })
      .catch(function (e) { busy(emailBtn, false); toast(e.message, true); });
  });

  var ctxBtn = root.querySelector('[data-igs-save-context]');
  if (ctxBtn) ctxBtn.addEventListener('click', function () {
    api('strategy/context', { text: root.querySelector('#igs-context').value })
      .then(function () { toast('Contexto guardado. Se usa en el próximo reporte.'); })
      .catch(function (e) { toast(e.message, true); });
  });

  var counter = root.querySelector('[data-igs-approved]');
  root.querySelectorAll('[data-igs-plan]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var on = btn.getAttribute('aria-pressed') !== 'true';
      api('plan/' + btn.getAttribute('data-igs-plan'), { approved: on }).then(function () {
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.textContent = on ? 'Aprobado' : 'Aprobar';
        btn.classList.toggle('igs-approved', on);
        btn.classList.toggle('btn-ghost', !on);
        if (counter) {
          var n = root.querySelectorAll('[data-igs-plan][aria-pressed="true"]').length;
          counter.textContent = n + ' de ' + counter.getAttribute('data-total') + ' aprobados';
        }
      }).catch(function (e) { toast(e.message, true); });
    });
  });
})();
