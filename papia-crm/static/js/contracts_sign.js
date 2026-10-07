/* Contratos — experiencia del firmante. Requiere pdf.js 3.x (window.pdfjsLib). */
(function () {
  'use strict';
  var S = window.PSIGN;
  if (!S || !window.pdfjsLib) return;
  pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

  var STYLES = [
    {font: 'Caveat', size: 1.0}, {font: 'Dancing Script', size: 0.9}, {font: 'Homemade Apple', size: 0.68}];
  var doc = document.getElementById('doc');
  var consent = document.getElementById('consent');
  var consented = S.signer.consented;
  var adopted = null;           // {signature, initials, kind}
  var values = {};              // id -> valor para text/checkbox
  var filled = {};              // id -> true
  var nodes = {};               // id -> elemento
  var pages = [];
  var mine = S.fields.filter(function (f) { return f.mine; });
  var actionable = mine.filter(function (f) { return ['signature', 'initials', 'text', 'checkbox'].indexOf(f.type) >= 0; });
  var mustFill = actionable.filter(function (f) { return f.type === 'signature' || f.type === 'initials' || f.required; });

  function $(id) { return document.getElementById(id); }
  function post(url, body) {
    return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify(body || {})}).then(function (r) { return r.json(); });
  }

  // ── PDF ────────────────────────────────────────────────────────────────
  pdfjsLib.getDocument({url: S.pdfUrl, withCredentials: true}).promise.then(function (pdf) {
    $('loading').remove();
    var chain = Promise.resolve();
    for (var i = 1; i <= pdf.numPages; i++) {
      (function (n) {
        chain = chain.then(function () { return pdf.getPage(n); }).then(function (page) {
          var base = page.getViewport({scale: 1});
          var cssW = Math.min(860, doc.clientWidth - 24);
          var vp = page.getViewport({scale: (cssW / base.width) * Math.min(2, window.devicePixelRatio || 1)});
          var wrap = document.createElement('div'); wrap.className = 'ps-page'; wrap.style.width = cssW + 'px';
          var canvas = document.createElement('canvas'); canvas.width = vp.width; canvas.height = vp.height;
          canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', 'Página ' + n + ' del documento');
          var layer = document.createElement('div'); layer.className = 'ps-layer';
          wrap.appendChild(canvas); wrap.appendChild(layer); doc.appendChild(wrap);
          var num = document.createElement('div'); num.className = 'ps-pnum'; num.textContent = n + ' / ' + pdf.numPages;
          doc.appendChild(num);
          pages[n] = {wrap: wrap, layer: layer};
          S.fields.filter(function (f) { return f.page === n; }).forEach(function (f) { placeField(f, layer); });
          return page.render({canvasContext: canvas.getContext('2d'), viewport: vp}).promise;
        });
      })(i);
    }
    return chain;
  }).then(function () { refresh(); }).catch(function (e) {
    var l = $('loading'); if (l) l.textContent = 'No se pudo cargar el documento. Recarga la página. (' + e.message + ')';
  });

  // ── campos ─────────────────────────────────────────────────────────────
  function placeField(f, layer) {
    var el = document.createElement('div');
    el.className = 'ps-f';
    el.style.left = (f.x * 100) + '%'; el.style.top = (f.y * 100) + '%';
    el.style.width = (f.w * 100) + '%'; el.style.height = (f.h * 100) + '%';
    if (!f.mine) {
      el.classList.add('other');
      if (f.img) { var im = new Image(); im.src = f.img; im.alt = 'Firma'; el.appendChild(im); }
      else if (f.type === 'checkbox') { el.innerHTML = '<span class="ps-check">' + (f.value === '1' ? '✓' : '') + '</span>'; }
      else { el.textContent = f.value; }
      layer.appendChild(el); return;
    }
    el.classList.add('mine');
    if (f.required || f.type === 'signature' || f.type === 'initials') el.classList.add('req');
    nodes[f.id] = el;
    if (f.type === 'signature' || f.type === 'initials') {
      el.setAttribute('role', 'button'); el.tabIndex = 0;
      el.setAttribute('aria-label', f.type === 'signature' ? 'Firmar aquí' : 'Poner iniciales aquí');
      el.innerHTML = '<i class="bi bi-arrow-down"></i><span>' + (f.type === 'signature' ? 'Firmar' : 'Iniciales') + '</span>';
      var act = function () { if (!needConsent()) applySig(f); };
      el.addEventListener('click', act);
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
    } else if (f.type === 'text') {
      var inp = document.createElement('input'); inp.type = 'text'; inp.maxLength = 500;
      inp.placeholder = f.label || 'Escribe aquí'; inp.setAttribute('aria-label', f.label || 'Texto');
      inp.addEventListener('focus', function () { needConsent(); });
      inp.addEventListener('input', function () {
        values[f.id] = inp.value; filled[f.id] = !!inp.value.trim(); el.classList.toggle('filled', filled[f.id]); refresh();
      });
      el.appendChild(inp); el.style.cursor = 'text';
    } else if (f.type === 'checkbox') {
      el.setAttribute('role', 'checkbox'); el.setAttribute('aria-checked', 'false'); el.tabIndex = 0;
      el.setAttribute('aria-label', f.label || 'Casilla');
      el.innerHTML = '<span class="ps-check"></span>';
      var tog = function () {
        if (needConsent()) return;
        var on = values[f.id] !== true; values[f.id] = on; filled[f.id] = on;
        el.firstChild.textContent = on ? '✓' : ''; el.setAttribute('aria-checked', on ? 'true' : 'false');
        el.classList.toggle('filled', on); refresh();
      };
      el.addEventListener('click', tog);
      el.addEventListener('keydown', function (e) { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); tog(); } });
    } else {
      // Automáticos: fecha, nombre, email
      el.classList.add('filled', 'ps-auto'); el.classList.remove('req');
      el.textContent = f.type === 'date_signed' ? S.today : (f.type === 'full_name' ? S.signer.name : S.signer.email);
      el.title = 'Se completa automáticamente al firmar';
    }
    layer.appendChild(el);
  }

  function nextPending() {
    return mustFill.find(function (f) { return !filled[f.id]; }) ||
           actionable.find(function (f) { return !filled[f.id]; });
  }
  var startTag = null;
  function refresh() {
    var done = mustFill.filter(function (f) { return filled[f.id]; }).length;
    var total = mustFill.length;
    $('progressTxt').textContent = done + ' de ' + total + ' campos';
    $('bar').firstElementChild.style.width = (total ? Math.round(done * 100 / total) : 100) + '%';
    $('bar').setAttribute('aria-valuenow', total ? Math.round(done * 100 / total) : 100);
    $('finishBtn').disabled = done < total || !consented;
    Object.keys(nodes).forEach(function (id) { nodes[id].classList.remove('current'); });
    var nx = nextPending();
    if (startTag) { startTag.remove(); startTag = null; }
    if (nx && nodes[nx.id] && consented) {
      nodes[nx.id].classList.add('current');
      startTag = document.createElement('button');
      startTag.type = 'button'; startTag.className = 'ps-start';
      startTag.textContent = done === 0 ? 'Empezar' : (nx.type === 'signature' ? 'Firmar' : 'Siguiente');
      startTag.style.top = 'calc(' + ((nx.y + nx.h / 2) * 100) + '% - 19px)';
      startTag.addEventListener('click', function () { goTo(nx); });
      pages[nx.page].wrap.appendChild(startTag);
    }
    $('nextBtn').disabled = !nx;
  }
  function goTo(f) {
    var el = nodes[f.id]; if (!el) return;
    el.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center'});
    var inp = el.querySelector('input'); if (inp) setTimeout(function () { inp.focus(); }, 250); else el.focus({preventScroll: true});
  }
  $('nextBtn').addEventListener('click', function () { if (!needConsent()) { var n = nextPending(); if (n) goTo(n); } });

  // ── consentimiento ─────────────────────────────────────────────────────
  function needConsent() {
    if (consented) return false;
    $('consentBar').scrollIntoView({behavior: 'smooth', block: 'start'});
    consent.focus();
    consent.parentElement.style.color = 'var(--coral-text)';
    return true;
  }
  function acceptConsent() {
    if (!consent.checked) { needConsent(); return; }
    consented = true;
    consent.parentElement.style.color = '';
    post(S.consentUrl);
    $('startBtn').classList.add('ps-hidden');
    refresh();
    var n = nextPending(); if (n) goTo(n);
  }
  $('startBtn').addEventListener('click', acceptConsent);
  consent.addEventListener('change', function () { if (consent.checked) acceptConsent(); else { consented = false; refresh(); } });
  if (consented) $('startBtn').classList.add('ps-hidden');

  // ── modales ────────────────────────────────────────────────────────────
  var lastFocus = null;
  function openModal(id) { lastFocus = document.activeElement; $(id).classList.add('open'); var f = $(id).querySelector('input,textarea,button'); if (f) f.focus(); }
  function closeModal(id) { $(id).classList.remove('open'); if (lastFocus) lastFocus.focus(); }
  document.querySelectorAll('.ps-modal-bg').forEach(function (bg) {
    bg.addEventListener('click', function (e) { if (e.target === bg) closeModal(bg.id); });
    bg.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { closeModal(bg.id); }); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') document.querySelectorAll('.ps-modal-bg.open').forEach(function (m) { closeModal(m.id); });
  });
  $('disclosureLink').addEventListener('click', function (e) { e.preventDefault(); openModal('discBg'); });

  // ── adoptar firma ──────────────────────────────────────────────────────
  var pendingField = null, tab = 'type';
  var nameIn = $('adoptName'), iniIn = $('adoptInitials');
  nameIn.value = S.signer.name;
  iniIn.value = S.signer.name.split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();

  function renderStyles() {
    var pane = $('stylePane'), cur = pane.querySelector('input:checked');
    var idx = cur ? parseInt(cur.value, 10) : 0;
    pane.innerHTML = '';
    STYLES.forEach(function (st, i) {
      var lab = document.createElement('label'); lab.className = 'ps-style';
      lab.innerHTML = '<input type="radio" name="sigStyle" value="' + i + '"' + (i === idx ? ' checked' : '') +
        '><span class="s"></span><span class="i"></span>';
      lab.querySelector('.s').textContent = nameIn.value || ' ';
      lab.querySelector('.s').style.fontFamily = '"' + st.font + '", cursive';
      lab.querySelector('.s').style.fontSize = (32 * st.size) + 'px';
      lab.querySelector('.i').textContent = iniIn.value;
      lab.querySelector('.i').style.fontFamily = '"' + st.font + '", cursive';
      pane.appendChild(lab);
    });
  }
  nameIn.addEventListener('input', renderStyles); iniIn.addEventListener('input', renderStyles);

  document.querySelectorAll('.ps-tab').forEach(function (t) {
    t.addEventListener('click', function () {
      tab = t.dataset.tab;
      document.querySelectorAll('.ps-tab').forEach(function (x) { x.setAttribute('aria-selected', x === t ? 'true' : 'false'); });
      document.querySelectorAll('[data-pane]').forEach(function (p) { p.classList.toggle('ps-hidden', p.dataset.pane !== tab); });
      if (tab === 'draw') sizePad();
    });
  });

  // pad de dibujo
  var pad = $('pad'), pctx = pad.getContext('2d'), drawing = false, inked = false, last = null;
  function sizePad() {
    var r = pad.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width) return;
    var snapshot = inked ? pad.toDataURL() : null;
    pad.width = r.width * dpr; pad.height = r.height * dpr;
    pctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pctx.lineWidth = 2.6; pctx.lineCap = 'round'; pctx.lineJoin = 'round'; pctx.strokeStyle = '#0A2540';
    if (snapshot) { var im = new Image(); im.onload = function () { pctx.drawImage(im, 0, 0, r.width, r.height); }; im.src = snapshot; }
  }
  function pt(e) { var r = pad.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; }
  pad.addEventListener('pointerdown', function (e) { drawing = true; last = pt(e); pad.setPointerCapture(e.pointerId); });
  pad.addEventListener('pointermove', function (e) {
    if (!drawing) return; var p = pt(e);
    pctx.beginPath(); pctx.moveTo(last.x, last.y);
    pctx.quadraticCurveTo(last.x, last.y, (last.x + p.x) / 2, (last.y + p.y) / 2); pctx.lineTo(p.x, p.y); pctx.stroke();
    last = p; inked = true;
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (n) { pad.addEventListener(n, function () { drawing = false; }); });
  $('padClear').addEventListener('click', function () { pctx.clearRect(0, 0, pad.width, pad.height); inked = false; });

  function trimCanvas(src) {
    var ctx = src.getContext('2d'), w = src.width, h = src.height, data = ctx.getImageData(0, 0, w, h).data;
    var minX = w, minY = h, maxX = -1, maxY = -1;
    for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] > 8) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
    }
    if (maxX < 0) return null;
    var pad2 = 6, tw = maxX - minX + 1 + pad2 * 2, th = maxY - minY + 1 + pad2 * 2;
    var scale = Math.min(1, 900 / tw, 300 / th);
    var out = document.createElement('canvas'); out.width = Math.ceil(tw * scale); out.height = Math.ceil(th * scale);
    out.getContext('2d').drawImage(src, minX - pad2, minY - pad2, tw, th, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  }
  function textToPng(text, font, sizeMul) {
    var c = document.createElement('canvas'), px = Math.round(84 * sizeMul);
    var ctx = c.getContext('2d'); ctx.font = px + 'px "' + font + '"';
    c.width = Math.min(1600, Math.ceil(ctx.measureText(text).width + px)); c.height = Math.ceil(px * 1.9);
    ctx = c.getContext('2d'); ctx.font = px + 'px "' + font + '"'; ctx.fillStyle = '#0A2540'; ctx.textBaseline = 'middle';
    ctx.fillText(text, px * 0.4, c.height / 2);
    return trimCanvas(c);
  }

  function openAdopt(f) {
    pendingField = f;
    openModal('adoptBg');
    var fonts = STYLES.map(function (s) { return document.fonts ? document.fonts.load('32px "' + s.font + '"') : null; });
    Promise.all(fonts).then(renderStyles, renderStyles);
  }
  $('adoptBtn').addEventListener('click', function () {
    var name = nameIn.value.trim(), ini = iniIn.value.trim();
    if (!name) { nameIn.focus(); return; }
    var sig, initials, kind;
    var st = STYLES[parseInt(($('stylePane').querySelector('input:checked') || {value: 0}).value, 10)];
    if (tab === 'draw') {
      if (!inked) { alert('Dibuja tu firma en el recuadro.'); return; }
      sig = trimCanvas(pad); kind = 'draw';
      initials = ini ? textToPng(ini, 'Caveat', 1) : null;
    } else {
      sig = textToPng(name, st.font, st.size); kind = 'type';
      initials = ini ? textToPng(ini, st.font, st.size) : null;
    }
    if (!sig) { alert('No pudimos generar la firma. Intenta de nuevo.'); return; }
    adopted = {signature: sig, initials: initials || sig, kind: kind};
    closeModal('adoptBg');
    if (pendingField) applySig(pendingField);
  });

  function applySig(f) {
    if (!adopted) { openAdopt(f); return; }
    var el = nodes[f.id];
    el.innerHTML = '';
    var im = new Image(); im.alt = f.type === 'signature' ? 'Tu firma' : 'Tus iniciales';
    im.src = f.type === 'signature' ? adopted.signature : adopted.initials;
    el.appendChild(im); el.classList.add('filled'); filled[f.id] = true;
    refresh();
    var n = nextPending(); if (n) setTimeout(function () { goTo(n); }, 250);
  }

  // ── finalizar / rechazar ───────────────────────────────────────────────
  $('finishBtn').addEventListener('click', function () {
    var b = this; b.disabled = true; b.textContent = 'Firmando…';
    var vals = {};
    actionable.forEach(function (f) { if (f.type === 'text' || f.type === 'checkbox') vals[f.id] = values[f.id] || (f.type === 'checkbox' ? false : ''); });
    post(S.completeUrl, {consent: true, values: vals, signature: adopted && adopted.signature,
                         initials: adopted && adopted.initials, kind: adopted && adopted.kind})
      .then(function (d) {
        if (d.ok) { window.location = d.redirect; }
        else { alert(d.error || 'No se pudo completar la firma.'); b.disabled = false; b.textContent = 'Finalizar'; }
      }).catch(function () { alert('Sin conexión. Intenta de nuevo.'); b.disabled = false; b.textContent = 'Finalizar'; });
  });
  $('declineBtn').addEventListener('click', function () { openModal('declineBg'); });
  $('declSend').addEventListener('click', function () {
    var b = this; b.disabled = true; $('declErr').textContent = '';
    post(S.declineUrl, {reason: $('declReason').value}).then(function (d) {
      if (d.ok) window.location = d.redirect; else { $('declErr').textContent = d.error; b.disabled = false; }
    }).catch(function () { b.disabled = false; $('declErr').textContent = 'Sin conexión.'; });
  });

  window.addEventListener('resize', function () { if ($('adoptBg').classList.contains('open') && tab === 'draw') sizePad(); });
})();
