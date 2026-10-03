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
    var s = saved[key];
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

  var statusEl = bar.querySelector('.pb-status');
  var timer = null;
  function collect() {
    var layout = {};
    grid.engine.nodes.forEach(function (n) {
      var id = n.el && n.el.getAttribute('gs-id');
      if (id) layout[id] = { x: n.x, y: n.y, w: n.w, h: n.h };
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
  grid.on('change', function () { save(false); });

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

  old.style.display = 'none';
})();
