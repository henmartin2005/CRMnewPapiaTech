/* Contratos — editor de campos (remitente). Requiere pdf.js 3.x (window.pdfjsLib). */
(function () {
  'use strict';
  var P = window.PSIGN_PREPARE;
  if (!P || !window.pdfjsLib) return;
  pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

  var ICONS = {signature: 'bi-pen', initials: 'bi-type', date_signed: 'bi-calendar-event', full_name: 'bi-person',
               email: 'bi-envelope', text: 'bi-input-cursor-text', checkbox: 'bi-check2-square'};
  var LABELS = {};
  P.fieldTypes.forEach(function (t) { LABELS[t[0]] = t[1]; });
  // Tamaño por defecto en puntos PDF (1/72 in)
  var SIZES = {signature: [170, 42], initials: [64, 34], date_signed: [110, 22], full_name: [170, 22],
               email: [190, 22], text: [170, 22], checkbox: [16, 16]};

  var stage = document.getElementById('stage');
  var signerById = {};
  P.signers.forEach(function (s) { signerById[s.id] = s; });
  var fields = P.fields.map(function (f) { return Object.assign({uid: uid()}, f); });
  var activeSigner = P.signers[0].id;
  var selected = null;
  var armedType = null;
  var pages = [];   // [{el, layer, w, h}]

  function uid() { return 'f' + Math.random().toString(36).slice(2, 10); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // ── firmantes ──────────────────────────────────────────────────────────
  function renderSigners() {
    var box = document.getElementById('signerList');
    box.innerHTML = '';
    P.signers.forEach(function (s) {
      var count = fields.filter(function (f) { return f.recipient_id === s.id; }).length;
      var hasSig = fields.some(function (f) { return f.recipient_id === s.id && f.type === 'signature'; });
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'ct-signer-pick' + (s.id === activeSigner ? ' active' : '');
      b.innerHTML = '<span class="ct-ord">' + s.order + '</span><span class="ct-dot" style="background:' + s.color +
        '"></span><span style="flex:1;min-width:0;"><b></b><small></small></span>';
      b.querySelector('b').textContent = s.name;
      b.querySelector('small').textContent = count + ' campo' + (count === 1 ? '' : 's') + (hasSig ? '' : ' · falta firma');
      if (!hasSig) b.querySelector('small').style.color = 'var(--c-danger)';
      b.addEventListener('click', function () { activeSigner = s.id; renderSigners(); });
      box.appendChild(b);
    });
    var s = signerById[activeSigner];
    var lbl = document.getElementById('activeSignerLbl');
    lbl.innerHTML = '<span class="ct-dot" style="background:' + s.color + '"></span><span></span>';
    lbl.lastChild.textContent = s.name;
    document.querySelectorAll('.ct-tool').forEach(function (t) { t.style.borderLeft = '4px solid ' + s.color; });
  }

  // ── render PDF ─────────────────────────────────────────────────────────
  function renderPdf() {
    pdfjsLib.getDocument({url: P.pdfUrl, withCredentials: true}).promise.then(function (pdf) {
      document.getElementById('loading').remove();
      var targetW = Math.min(820, stage.clientWidth - 32);
      var chain = Promise.resolve();
      for (var i = 1; i <= pdf.numPages; i++) {
        (function (n) {
          chain = chain.then(function () { return pdf.getPage(n); }).then(function (page) {
            var base = page.getViewport({scale: 1});
            var scale = targetW / base.width;
            var vp = page.getViewport({scale: scale * (window.devicePixelRatio || 1)});
            var wrap = document.createElement('div');
            wrap.className = 'ct-page';
            wrap.style.width = targetW + 'px';
            var canvas = document.createElement('canvas');
            canvas.width = vp.width; canvas.height = vp.height;
            canvas.setAttribute('aria-label', 'Página ' + n);
            var layer = document.createElement('div');
            layer.className = 'ct-layer';
            layer.dataset.page = n;
            wrap.appendChild(canvas); wrap.appendChild(layer);
            stage.appendChild(wrap);
            var num = document.createElement('div');
            num.className = 'ct-page-num';
            num.textContent = 'Página ' + n + ' de ' + pdf.numPages;
            stage.appendChild(num);
            pages[n] = {el: wrap, layer: layer, ptW: base.width, ptH: base.height};
            bindLayer(layer, n);
            drawFields(n);
            return page.render({canvasContext: canvas.getContext('2d'), viewport: vp}).promise;
          });
        })(i);
      }
    }).catch(function (err) {
      document.getElementById('loading').textContent = 'No se pudo cargar el PDF: ' + err.message;
    });
  }

  // ── fields ─────────────────────────────────────────────────────────────
  function drawFields(page) {
    var pg = pages[page];
    if (!pg) return;
    pg.layer.querySelectorAll('.ct-field').forEach(function (n) { n.remove(); });
    fields.filter(function (f) { return f.page === page; }).forEach(function (f) {
      var s = signerById[f.recipient_id] || P.signers[0];
      var el = document.createElement('div');
      el.className = 'ct-field' + (selected === f.uid ? ' sel' : '');
      el.style.setProperty('--fc', s.color);
      el.style.left = (f.x * 100) + '%'; el.style.top = (f.y * 100) + '%';
      el.style.width = (f.w * 100) + '%'; el.style.height = (f.h * 100) + '%';
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.setAttribute('aria-label', LABELS[f.type] + ' de ' + s.name);
      if (f.type !== 'checkbox') {
        el.innerHTML = '<i class="bi ' + ICONS[f.type] + '"></i><span></span>';
        el.lastChild.textContent = (f.label || LABELS[f.type]) + ' · ' + s.name.split(' ')[0];
      }
      var h = document.createElement('span'); h.className = 'ct-h'; el.appendChild(h);
      bindField(el, f, pg);
      pg.layer.appendChild(el);
    });
  }
  function redrawAll() { for (var p = 1; p < pages.length; p++) drawFields(p); renderSigners(); renderProps(); }

  function addField(type, page, cx, cy) {
    var pg = pages[page];
    var sz = SIZES[type];
    var w = sz[0] / pg.ptW, h = sz[1] / pg.ptH;
    var f = {uid: uid(), id: null, recipient_id: activeSigner, type: type, page: page,
             x: clamp(cx - w / 2, 0, 1 - w), y: clamp(cy - h / 2, 0, 1 - h), w: w, h: h,
             required: type === 'checkbox' ? 0 : 1, label: ''};
    fields.push(f);
    selected = f.uid;
    redrawAll(); queueSave();
  }

  function bindLayer(layer, page) {
    layer.addEventListener('click', function (e) {
      if (e.target !== layer) return;
      var r = layer.getBoundingClientRect();
      if (armedType) {
        addField(armedType, page, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
        disarm();
      } else if (selected) { selected = null; redrawAll(); }
    });
    layer.addEventListener('dragover', function (e) { e.preventDefault(); });
    layer.addEventListener('drop', function (e) {
      e.preventDefault();
      var type = e.dataTransfer.getData('text/x-psign');
      if (!type) return;
      var r = layer.getBoundingClientRect();
      addField(type, page, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
    });
  }

  function bindField(el, f, pg) {
    el.addEventListener('pointerdown', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (selected !== f.uid) {
        selected = f.uid;
        pg.layer.querySelectorAll('.ct-field').forEach(function (n) { n.classList.remove('sel'); });
        el.classList.add('sel'); renderProps();
      }
      var resizing = e.target.classList.contains('ct-h');
      var r = pg.layer.getBoundingClientRect();
      var sx = e.clientX, sy = e.clientY, ox = f.x, oy = f.y, ow = f.w, oh = f.h, moved = false;
      el.setPointerCapture(e.pointerId);
      function move(ev) {
        var dx = (ev.clientX - sx) / r.width, dy = (ev.clientY - sy) / r.height;
        if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 2) moved = true;
        if (resizing) {
          f.w = clamp(ow + dx, 12 / pg.ptW, 1 - f.x); f.h = clamp(oh + dy, 10 / pg.ptH, 1 - f.y);
          if (f.type === 'checkbox') { f.h = f.w * pg.ptW / pg.ptH; }
        } else {
          f.x = clamp(ox + dx, 0, 1 - f.w); f.y = clamp(oy + dy, 0, 1 - f.h);
        }
        el.style.left = (f.x * 100) + '%'; el.style.top = (f.y * 100) + '%';
        el.style.width = (f.w * 100) + '%'; el.style.height = (f.h * 100) + '%';
      }
      function up() {
        el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
        if (moved) queueSave();
      }
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
    });
  }

  // ── panel de propiedades ───────────────────────────────────────────────
  function renderProps() {
    var box = document.getElementById('props');
    var f = fields.find(function (x) { return x.uid === selected; });
    if (!f) { box.innerHTML = '<div class="ct-empty-props">Haz clic en un campo del documento para editarlo.</div>'; return; }
    var opts = P.signers.map(function (s) {
      return '<option value="' + s.id + '"' + (s.id === f.recipient_id ? ' selected' : '') + '></option>';
    }).join('');
    box.innerHTML =
      '<div style="font-weight:650;font-size:13.5px;"><i class="bi ' + ICONS[f.type] + '"></i> ' + LABELS[f.type] + '</div>' +
      '<label class="form-label" style="margin:0;font-size:12px;">Asignado a<select class="form-select form-select-sm" id="pSigner">' + opts + '</select></label>' +
      (['text', 'checkbox'].indexOf(f.type) >= 0 ?
        '<label style="display:flex;justify-content:space-between;align-items:center;font-size:13px;">Obligatorio <input type="checkbox" id="pReq"' + (f.required ? ' checked' : '') + ' style="width:17px;height:17px;"></label>' : '') +
      (f.type === 'text' ? '<label class="form-label" style="margin:0;font-size:12px;">Indicación para el firmante<input class="form-control form-control-sm" id="pLabel" maxlength="80" placeholder="Ej. Dirección de facturación"></label>' : '') +
      '<div style="display:flex;gap:6px;"><button type="button" class="btn btn-ghost btn-sm" id="pDup"><i class="bi bi-copy"></i> Duplicar</button>' +
      '<button type="button" class="btn btn-ghost btn-sm" id="pDel" style="color:var(--c-danger);"><i class="bi bi-trash"></i> Quitar</button></div>';
    var sel = document.getElementById('pSigner');
    Array.prototype.forEach.call(sel.options, function (o) { o.textContent = signerById[o.value].name; });
    sel.addEventListener('change', function () { f.recipient_id = parseInt(sel.value, 10); redrawAll(); queueSave(); });
    var req = document.getElementById('pReq');
    if (req) req.addEventListener('change', function () { f.required = req.checked ? 1 : 0; queueSave(); });
    var lab = document.getElementById('pLabel');
    if (lab) { lab.value = f.label || ''; lab.addEventListener('input', function () { f.label = lab.value; drawFields(f.page); queueSave(); }); }
    document.getElementById('pDel').addEventListener('click', removeSelected);
    document.getElementById('pDup').addEventListener('click', function () {
      var c = Object.assign({}, f, {uid: uid(), id: null, y: clamp(f.y + f.h + 0.01, 0, 1 - f.h)});
      fields.push(c); selected = c.uid; redrawAll(); queueSave();
    });
  }
  function removeSelected() {
    if (!selected) return;
    fields = fields.filter(function (x) { return x.uid !== selected; });
    selected = null; redrawAll(); queueSave();
  }
  document.addEventListener('keydown', function (e) {
    var tag = (e.target.tagName || '').toLowerCase();
    if ((e.key === 'Delete' || e.key === 'Backspace') && selected && tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
      e.preventDefault(); removeSelected();
    }
    if (e.key === 'Escape') { disarm(); }
  });

  // ── paleta ─────────────────────────────────────────────────────────────
  function disarm() {
    armedType = null;
    document.querySelectorAll('.ct-tool').forEach(function (t) { t.classList.remove('armed'); });
    pages.forEach(function (p) { if (p) p.el.classList.remove('placing'); });
  }
  document.querySelectorAll('.ct-tool').forEach(function (t) {
    t.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/x-psign', t.dataset.type); });
    t.addEventListener('click', function () {
      var was = armedType === t.dataset.type;
      disarm();
      if (!was) {
        armedType = t.dataset.type; t.classList.add('armed');
        pages.forEach(function (p) { if (p) p.el.classList.add('placing'); });
      }
    });
  });

  // ── guardar / enviar ───────────────────────────────────────────────────
  var saveTimer = null, saving = null;
  var state = document.getElementById('saveState');
  function payloadFields() {
    return fields.map(function (f) {
      return {recipient_id: f.recipient_id, type: f.type, page: f.page, x: f.x, y: f.y, w: f.w, h: f.h,
              required: f.required, label: f.label};
    });
  }
  function save() {
    state.textContent = 'Guardando…';
    saving = fetch(P.saveUrl, {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin',
                               body: JSON.stringify({fields: payloadFields()})})
      .then(function (r) { return r.json(); })
      .then(function (d) { state.textContent = d.ok ? 'Guardado' : (d.error || 'Error al guardar'); return d.ok; })
      .catch(function () { state.textContent = 'Sin conexión — no guardado'; return false; });
    return saving;
  }
  function queueSave() { clearTimeout(saveTimer); state.textContent = 'Cambios sin guardar'; saveTimer = setTimeout(save, 700); }

  document.getElementById('sendBtn').addEventListener('click', function () {
    var missing = P.signers.filter(function (s) {
      return !fields.some(function (f) { return f.recipient_id === s.id && f.type === 'signature'; });
    });
    if (missing.length) {
      alert('Falta un campo de Firma para: ' + missing.map(function (s) { return s.name; }).join(', '));
      activeSigner = missing[0].id; renderSigners(); return;
    }
    if (!confirm('¿Enviar el sobre? Se notificará al primer firmante y ya no podrás editar los campos.')) return;
    var btn = this; btn.disabled = true;
    clearTimeout(saveTimer);
    save().then(function (ok) {
      if (!ok) { btn.disabled = false; return; }
      return fetch(P.sendUrl, {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin', body: '{}'})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) { window.location = d.redirect; }
          else { alert(d.error || 'No se pudo enviar.'); btn.disabled = false; }
        });
    }).catch(function () { btn.disabled = false; });
  });
  window.addEventListener('beforeunload', function (e) {
    if (state.textContent === 'Cambios sin guardar') { save(); e.preventDefault(); e.returnValue = ''; }
  });

  renderSigners();
  renderPdf();
})();
