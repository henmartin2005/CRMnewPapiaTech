// Módulo Demos: copiar mensajes, editar demos del catálogo y autocompletar el lead.
(function () {
  function toast(text) {
    var el = document.createElement('div');
    el.className = 'demo-toast';
    el.textContent = text;
    document.body.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('show'); });
    setTimeout(function () { el.classList.remove('show'); setTimeout(function () { el.remove(); }, 250); }, 1600);
  }

  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
    return Promise.resolve();
  }

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-copy]');
    if (!btn) return;
    ev.preventDefault();
    copy(btn.getAttribute('data-copy')).then(function () { toast('Copiado'); });
  });

  // Modal del catálogo: nuevo vs editar
  var demoModal = document.getElementById('demoModal');
  if (demoModal) {
    demoModal.addEventListener('show.bs.modal', function (ev) {
      var form = demoModal.querySelector('form');
      var trigger = ev.relatedTarget;
      var raw = trigger && trigger.getAttribute('data-demo');
      var d = raw ? JSON.parse(raw) : null;
      form.reset();
      form.demo_id.value = d ? d.id : '';
      form.nombre.value = d ? d.nombre : '';
      form.slug.value = d ? d.slug : '';
      form.url.value = d ? d.url : '';
      form.color.value = d ? d.color : '#2A5BFF';
      form.mensaje.value = d ? (d.mensaje || '') : '';
      form.activo.checked = d ? !!d.activo : true;
      demoModal.querySelector('[data-title]').textContent = d ? 'Editar demo' : 'Nuevo demo';
    });
  }

  // Modal de código: color del demo y datos del lead elegido
  var linkModal = document.getElementById('linkModal');
  if (linkModal) {
    var form = linkModal.querySelector('form');
    var demoSel = form.demo_id;
    var syncColor = function () {
      var opt = demoSel.options[demoSel.selectedIndex];
      if (opt && opt.dataset.color) form.color.value = opt.dataset.color;
    };
    demoSel.addEventListener('change', syncColor);
    linkModal.addEventListener('show.bs.modal', syncColor);

    var lead = document.getElementById('leadSelect');
    var crearWrap = document.getElementById('crearLeadWrap');
    lead.addEventListener('change', function () {
      var opt = lead.options[lead.selectedIndex];
      var picked = !!lead.value;
      crearWrap.style.display = picked ? 'none' : '';
      if (picked) {
        if (!form.negocio.value) form.negocio.value = opt.dataset.company || opt.dataset.name;
        if (!form.nombre_prospecto.value) form.nombre_prospecto.value = (opt.dataset.name || '').trim();
        if (!form.telefono.value) form.telefono.value = opt.dataset.phone || '';
      }
    });
  }
})();
