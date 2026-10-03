/* ══ Papia CRM — Bloqueo de montos ══
   Oculta como $$$$$$ cualquier valor monetario visible del CRM (dashboard,
   perfiles, propuestas, pagos…). Por defecto está BLOQUEADO; el estado se
   recuerda en este navegador. Los chats (WhatsApp, chat interno, emails,
   Messenger/Instagram) no se tocan para no alterar las conversaciones. */
(function () {
  'use strict';
  var KEY = 'papia_money_visible';
  var MASK = '$$$$$$';
  var RE = /(?:US)?-?\$\s?-?\d[\d,]*(?:\.\d+)?(?:\s?(?:USD|k|K|M)\b)?/g;
  var SKIP = 'script,style,textarea,input,select,option,noscript,code,pre,[contenteditable],[data-no-mask],.money-val,' +
             '.wa,.ichat-shell';
  var root = document.documentElement;
  var memVisible = null;

  function pageExcluded() {
    var p = location.pathname;
    if (p.indexOf('/whatsapp/bot') === 0) return false;
    return /^\/(chat|emails|messenger|instagram|meta)(\/|$)/.test(p);
  }
  function isVisible() {
    if (memVisible !== null) return memVisible;
    try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }

  function wrapIn(el) {
    if (!el || pageExcluded()) return;
    if (el.nodeType === 3) el = el.parentElement;
    if (!el || el.nodeType !== 1 || el.closest(SKIP)) return;
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue || n.nodeValue.indexOf('$') < 0) return NodeFilter.FILTER_REJECT;
        var p = n.parentElement;
        if (!p || p.closest(SKIP)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    var vis = isVisible();
    nodes.forEach(function (n) {
      var s = n.nodeValue;
      RE.lastIndex = 0;
      if (!RE.test(s)) return;
      RE.lastIndex = 0;
      var frag = document.createDocumentFragment(), last = 0, m;
      while ((m = RE.exec(s))) {
        if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
        var sp = document.createElement('span');
        sp.className = 'money-val';
        sp.setAttribute('data-real', m[0]);
        sp.textContent = vis ? m[0] : MASK;
        frag.appendChild(sp);
        last = m.index + m[0].length;
      }
      if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      if (n.parentNode) n.parentNode.replaceChild(frag, n);
    });
  }

  function paint() {
    var vis = isVisible();
    root.classList.toggle('money-locked', !vis);
    document.querySelectorAll('.money-val').forEach(function (sp) {
      var t = vis ? sp.getAttribute('data-real') : MASK;
      if (sp.textContent !== t) sp.textContent = t;
    });
    document.querySelectorAll('[data-money-toggle]').forEach(function (b) {
      b.setAttribute('aria-pressed', vis ? 'true' : 'false');
      b.classList.toggle('is-unlocked', vis);
      b.title = vis ? 'Ocultar montos' : 'Mostrar montos';
      var ic = b.querySelector('i');
      if (ic) ic.className = 'bi ' + (vis ? 'bi-unlock' : 'bi-lock-fill');
      var lb = b.querySelector('.money-toggle-lbl');
      if (lb) lb.textContent = vis ? 'Montos visibles' : 'Montos ocultos';
    });
  }

  function setVisible(v) {
    memVisible = v;
    try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) {}
    paint();
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-money-toggle]');
    if (!b) return;
    e.preventDefault();
    setVisible(!isVisible());
  });
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) { memVisible = null; paint(); }
  });

  // Contenido que se pinta después (JS de perfiles, tablas, etc.)
  var pending = [], scheduled = false;
  function flush() {
    scheduled = false;
    var list = pending; pending = [];
    list.forEach(function (n) { if (n.isConnected) wrapIn(n); });
  }
  var obs = new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      var t = m.target.nodeType === 3 ? m.target.parentElement : m.target;
      if (t && t.closest && t.closest('.money-val')) return;
      if (m.type === 'characterData') { pending.push(m.target); }
      else m.addedNodes.forEach(function (n) { pending.push(n); });
    });
    if (pending.length && !scheduled) { scheduled = true; requestAnimationFrame(flush); }
  });

  try {
    wrapIn(document.body);
    paint();
  } finally {
    root.classList.add('money-ready');
  }
  obs.observe(document.body, { childList: true, subtree: true, characterData: true });
  window.PapiaMoney = { show: function () { setVisible(true); }, hide: function () { setVisible(false); }, refresh: function () { wrapIn(document.body); paint(); } };
})();
