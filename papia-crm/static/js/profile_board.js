/* Perfil de cliente: cuadros flotantes (arrastrar / redimensionar) + colores tipo sticky note.
   Se guarda por cliente en /clients/<id>/layout. */
(function () {
  'use strict';
  var cfg = window.PROFILE_BOARD;
  if (!cfg || !window.GridStack) return;
  var old = document.querySelector('.detail-grid');
  if (!old) return;

  var COLORS = ['white', 'yellow', 'green', 'pink', 'orange', 'blue', 'purple'];
  var NAMES = { white: 'Blanco', yellow: 'Amarillo', green: 'Verde', pink: 'Rosado',
                orange: 'Naranja', blue: 'Azul', purple: 'Violeta' };
  var CELL = 10, MARGIN = 6;
  var state = cfg.state || {};
  var saved = state.layout || {};
  var colors = state.colors || {};

  // Diseño por defecto para clientes sin diseño propio (tomado del perfil de Henrry Test Cobrar).
  var DEFAULT_LAYOUT = {"mantenimiento":{"x":0,"y":131,"w":4,"h":65},"contacto":{"x":0,"y":0,"w":4,"h":36},"financiero":{"x":0,"y":36,"w":4,"h":27},"cronograma":{"x":0,"y":63,"w":4,"h":20},"cuadro":{"x":0,"y":83,"w":4,"h":8},"vaultcard":{"x":0,"y":91,"w":4,"h":11},"etapa-del-pipeline":{"x":0,"y":102,"w":4,"h":29},"nueva-actividad":{"x":4,"y":0,"w":8,"h":13},"projstages":{"x":4,"y":13,"w":8,"h":7},"waconversation":{"x":4,"y":20,"w":8,"h":12},"historial":{"x":4,"y":32,"w":8,"h":60},"recordatorios":{"x":4,"y":92,"w":8,"h":17},"contratos":{"x":4,"y":109,"w":8,"h":12},"portalcliente":{"x":4,"y":121,"w":8,"h":17},"documentos":{"x":4,"y":138,"w":8,"h":24}};
  // Cuadros que se pueden minimizar; empiezan minimizados.
  var COLLAPSIBLE = { historial: true };
  var skipSave = false;

  function slug(s) {
    return (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'cuadro';
  }

  var panels = Array.prototype.slice.call(old.children);
  var cards = Array.prototype.slice.call(old.querySelectorAll('.section-card'));
  if (!cards.length) return;

  var bar = document.createElement('div');
  bar.className = 'pb-toolbar';
  bar.innerHTML = '<button type="button" class="pb-lock" aria-pressed="false" title="Desbloquear para mover y ajustar los cuadros"><i class="bi bi-lock-fill"></i><span class="pb-lock-label">Bloqueado</span></button>' +
    '<span class="pb-hint pb-hint-locked"><i class="bi bi-lock"></i> Diseño fijo. Toca el candado para mover o ajustar los cuadros.</span>' +
    '<span class="pb-hint pb-hint-unlocked"><i class="bi bi-arrows-move"></i> Arrastra cada cuadro por su título y ajusta el tamaño desde los bordes. Usa <i class="bi bi-palette"></i> para darle color.</span>' +
    '<span class="pb-status" aria-live="polite"></span>' +
    '<button type="button" class="btn btn-ghost btn-sm pb-reset"><i class="bi bi-arrow-counterclockwise"></i> Restablecer diseño</button>';
  var gridEl = document.createElement('div');
  gridEl.className = 'grid-stack profile-board';
  old.parentNode.insertBefore(bar, old);
  old.parentNode.insertBefore(gridEl, old);

  var used = {}, items = [], colY = [0, 0];
  cards.forEach(function (card, i) {
    var head = card.querySelector('.section-head-label, .card-header-left');
    var key = card.id ? slug(card.id) : slug(head ? head.textContent : '');
    if (used[key]) { used[key] += 1; key = key + '-' + used[key]; } else { used[key] = 1; }
    var panel = 0;
    panels.forEach(function (p, idx) { if (p.contains(card)) panel = idx; });
    var col = panel === 0 ? 0 : 1;

    var item = document.createElement('div');
    item.className = 'grid-stack-item';
    item.setAttribute('gs-id', key);
    var s = saved[key] || DEFAULT_LAYOUT[key];
    if (s) {
      item.setAttribute('gs-x', s.x); item.setAttribute('gs-y', s.y);
      item.setAttribute('gs-w', s.w); item.setAttribute('gs-h', s.h);
    } else {
      item.setAttribute('gs-x', col === 0 ? 0 : 4);
      item.setAttribute('gs-w', col === 0 ? 4 : 8);
      item.setAttribute('gs-y', colY[col]);
      item.setAttribute('gs-h', 20);
      colY[col] += 20;
    }
    var content = document.createElement('div');
    content.className = 'grid-stack-item-content';
    item.appendChild(content);
    content.appendChild(card);

    var pick = document.createElement('div');
    pick.className = 'pb-color';
    pick.innerHTML = '<button type="button" class="pb-color-btn" title="Color del cuadro"><i class="bi bi-palette"></i></button>' +
      '<div class="pb-swatches">' + COLORS.map(function (c) {
        return '<button type="button" class="pb-sw pb-sw-' + c + '" data-color="' + c + '" title="' + NAMES[c] + '"></button>';
      }).join('') + '</div>';
    content.appendChild(pick);

    gridEl.appendChild(item);
    items.push({ item: item, card: card, key: key, auto: !s });
    applyColor(item, colors[key] || 'white');
  });

  function applyColor(item, c) {
    COLORS.forEach(function (x) { item.classList.remove('pb-c-' + x); });
    if (c && c !== 'white') item.classList.add('pb-c-' + c);
    item.querySelectorAll('.pb-sw').forEach(function (b) {
      b.classList.toggle('is-active', b.getAttribute('data-color') === (c || 'white'));
    });
  }

  var grid = GridStack.init({
    column: 12,
    cellHeight: CELL,
    margin: MARGIN,
    float: false,
    animate: true,
    staticGrid: true,
    handle: '.section-head, .card-header',
    resizable: { handles: 'e, se, s, sw, w' },
    columnOpts: { breakpointForWindow: true, breakpoints: [{ w: 900, c: 1 }] }
  }, gridEl);

  function fitHeight(it) {
    var prev = it.card.style.height;
    it.card.style.height = 'auto';
    var h = Math.ceil((it.card.scrollHeight + MARGIN * 2 + 2) / CELL);
    it.card.style.height = prev;
    return Math.max(h, 6);
  }

  var ready = false;
  grid.batchUpdate();
  items.forEach(function (it) { if (it.auto) grid.update(it.item, { h: fitHeight(it) }); });
  grid.batchUpdate(false);
  setTimeout(function () { ready = true; }, 400);

  // Cuadros minimizables (Historial): minimizados por defecto, con botón Desplegar / Minimizar.
  (function () {
    var st = document.createElement('style');
    st.textContent =
      '.pb-has-collapse .section-head{display:flex;align-items:center;gap:8px}' +
      '.pb-has-collapse .section-head .section-head-label{margin-right:auto}' +
      '.pb-collapse-btn{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border:1px solid var(--c-border);' +
      'border-radius:999px;background:transparent;color:inherit;font-size:11.5px;font-weight:600;cursor:pointer;line-height:1.4}' +
      '.pb-collapse-btn:hover{background:rgba(127,127,127,.12)}' +
      '.section-card.pb-collapsed > :not(.section-head){display:none !important}' +
      '.section-card.pb-collapsed .section-head{border-bottom:0}';
    document.head.appendChild(st);
  })();
  items.forEach(function (it) {
    if (!COLLAPSIBLE[it.key]) return;
    var head = it.card.querySelector('.section-head');
    if (!head) return;
    var node = it.item.gridstackNode;
    it.expandedH = (node && node.h) || fitHeight(it);
    it.card.classList.add('pb-has-collapse');
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pb-collapse-btn';
    head.appendChild(btn);
    function setCollapsed(c) {
      it.collapsed = c;
      it.card.classList.toggle('pb-collapsed', c);
      btn.innerHTML = c ? '<i class="bi bi-chevron-down"></i> Desplegar' : '<i class="bi bi-chevron-up"></i> Minimizar';
      btn.title = c ? 'Desplegar historial' : 'Minimizar historial';
      btn.setAttribute('aria-expanded', c ? 'false' : 'true');
      skipSave = true;
      grid.update(it.item, { h: c ? fitHeight(it) : it.expandedH });
      setTimeout(function () { skipSave = false; }, 0);
    }
    btn.addEventListener('mousedown', function (e) { e.stopPropagation(); });
    btn.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      setCollapsed(!it.collapsed);
    });
    setCollapsed(true);
  });

  var statusEl = bar.querySelector('.pb-status');
  var timer = null;
  function collect() {
    var layout = {};
    grid.engine.nodes.forEach(function (n) {
      var id = n.el && n.el.getAttribute('gs-id');
      if (!id) return;
      var h = n.h;
      items.forEach(function (it) {
        if (it.key !== id || !COLLAPSIBLE[id]) return;
        if (it.collapsed) h = it.expandedH; else it.expandedH = n.h;
      });
      layout[id] = { x: n.x, y: n.y, w: n.w, h: h };
    });
    return layout;
  }
  function save(force) {
    if (!ready && !force) return;
    var payload = { colors: colors };
    if (grid.getColumn() === 12) payload.layout = collect(); else payload.layout = saved;
    saved = payload.layout;
    clearTimeout(timer);
    timer = setTimeout(function () {
      statusEl.textContent = 'Guardando…';
      fetch(cfg.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                           body: JSON.stringify(payload), credentials: 'same-origin' })
        .then(function (r) { statusEl.textContent = r.ok ? 'Guardado' : 'No se pudo guardar'; })
        .catch(function () { statusEl.textContent = 'No se pudo guardar'; })
        .finally(function () { setTimeout(function () { statusEl.textContent = ''; }, 1500); });
    }, 500);
  }
  grid.on('change', function () { if (!skipSave) save(false); });

  gridEl.addEventListener('click', function (e) {
    var btn = e.target.closest('.pb-color-btn');
    var sw = e.target.closest('.pb-sw');
    document.querySelectorAll('.pb-color.is-open').forEach(function (p) {
      if (!btn || !p.contains(btn)) p.classList.remove('is-open');
    });
    if (btn) {
      var open = btn.parentNode.classList.toggle('is-open');
      document.querySelectorAll('.grid-stack-item.pb-open').forEach(function (x) { x.classList.remove('pb-open'); });
      if (open) btn.closest('.grid-stack-item').classList.add('pb-open');
      return;
    }
    if (sw) {
      var item = sw.closest('.grid-stack-item');
      var key = item.getAttribute('gs-id');
      var c = sw.getAttribute('data-color');
      if (c === 'white') delete colors[key]; else colors[key] = c;
      applyColor(item, c);
      sw.closest('.pb-color').classList.remove('is-open');
      save(true);
    }
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.pb-color')) {
      document.querySelectorAll('.pb-color.is-open').forEach(function (p) { p.classList.remove('is-open'); });
    }
  });

  // Candado: por defecto el diseño queda fijo (sin arrastrar ni redimensionar).
  var lockBtn = bar.querySelector('.pb-lock');
  function setLocked(locked) {
    grid.setStatic(locked);
    bar.classList.toggle('pb-unlocked', !locked);
    gridEl.classList.toggle('pb-unlocked', !locked);
    lockBtn.setAttribute('aria-pressed', locked ? 'false' : 'true');
    lockBtn.title = locked ? 'Desbloquear para mover y ajustar los cuadros' : 'Bloquear el diseño';
    lockBtn.querySelector('i').className = locked ? 'bi bi-lock-fill' : 'bi bi-unlock-fill';
    lockBtn.querySelector('.pb-lock-label').textContent = locked ? 'Bloqueado' : 'Desbloqueado';
    if (locked) {
      document.querySelectorAll('.pb-color.is-open').forEach(function (p) { p.classList.remove('is-open'); });
    }
  }
  lockBtn.addEventListener('click', function () {
    setLocked(lockBtn.getAttribute('aria-pressed') === 'true');
  });
  setLocked(true);

  bar.querySelector('.pb-reset').addEventListener('click', function () {
    if (!confirm('¿Restablecer posición, tamaño y colores de los cuadros de este cliente?')) return;
    fetch(cfg.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                         body: JSON.stringify({ reset: true }), credentials: 'same-origin' })
      .then(function () { location.reload(); });
  });

  // Cronograma de pagos: que "Agregar cuota" siempre se vea y que el cuadro crezca
  // al abrir el formulario (vuelve a su altura al cerrarlo). No se guarda en el diseño.
  document.querySelectorAll('details.sched-add').forEach(function (d) {
    var it = items.find(function (x) { return x.card.contains(d); });
    if (!it) return;
    var baseH = null;
    function currentH() { return it.item.gridstackNode ? it.item.gridstackNode.h : 0; }
    function setH(h) {
      skipSave = true;
      grid.update(it.item, { h: h });
      setTimeout(function () { skipSave = false; }, 0);
    }
    if (!it.card.classList.contains('pb-collapsed')) {
      var need = fitHeight(it);
      if (need > currentH()) setH(need);
    }
    d.addEventListener('toggle', function () {
      if (d.open) {
        baseH = currentH();
        setH(Math.max(fitHeight(it), baseH));
        var f = d.querySelector('input[name="label"]');
        if (f) f.focus();
      } else if (baseH) {
        setH(baseH);
      }
    });
  });

  old.style.display = 'none';
})();
