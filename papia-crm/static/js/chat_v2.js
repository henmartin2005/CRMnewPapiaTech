/* Chat interno v2 — panel de detalles (archivos compartidos, actividad). Solo UI. */
(function () {
  'use strict';
  var shell = document.getElementById('chatShell');
  var main = shell && shell.querySelector('.ichat-main');
  var header = shell && shell.querySelector('.ichat-header');
  if (!shell || !main || !header || shell.dataset.v2) return;
  shell.dataset.v2 = '1';

  // Quita espacios/saltos sobrantes del template dentro de cada burbuja (white-space: pre-wrap).
  function tidy() {
    shell.querySelectorAll('.ichat-bubble').forEach(function (b) {
      if (b.dataset.tidy) return;
      b.dataset.tidy = '1';
      var seenText = false;
      Array.prototype.slice.call(b.childNodes).forEach(function (n) {
        if (n.nodeType !== 3) return;
        if (!n.textContent.trim()) { n.parentNode.removeChild(n); return; }
        var t = n.textContent;
        if (!seenText) t = t.replace(/^\s+/, '');
        n.textContent = t.replace(/\s+$/, '');
        seenText = true;
      });
    });
  }
  tidy();

  var KEY = 'ichat.details.open';
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function store(v) { try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) {} }

  var panel = document.createElement('aside');
  panel.className = 'ichat-details';
  panel.setAttribute('aria-label', 'Detalles de la conversación');
  shell.appendChild(panel);

  var actions = header.querySelector('.ichat-header-actions');
  var toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'ichat-details-toggle';
  toggle.title = 'Detalles de la conversación';
  toggle.innerHTML = '<i class="bi bi-layout-sidebar-reverse"></i>';
  if (actions) actions.insertBefore(toggle, actions.firstChild); else header.appendChild(toggle);

  function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function iconFor(name) {
    var ext = (name.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return 'bi-file-earmark-pdf';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'heic'].indexOf(ext) >= 0) return 'bi-file-earmark-image';
    if (['doc', 'docx'].indexOf(ext) >= 0) return 'bi-file-earmark-word';
    if (['xls', 'xlsx', 'csv'].indexOf(ext) >= 0) return 'bi-file-earmark-excel';
    if (ext === 'zip') return 'bi-file-earmark-zip';
    return 'bi-file-earmark';
  }

  function render() {
    var nameEl = header.querySelector('.ichat-header-name');
    var metaEl = header.querySelector('.ichat-header-meta');
    var avatarSrc = header.querySelector('.ichat-header-info img');
    var avatarTxt = header.querySelector('.ichat-header-info .ichat-org-avatar, .ichat-header-info .ichat-person-avatar');
    var name = nameEl ? nameEl.textContent.trim() : 'Conversación';
    var meta = metaEl ? metaEl.textContent.replace(/\s+/g, ' ').trim() : '';

    var msgs = shell.querySelectorAll('.ichat-thread .ichat-message');
    var own = shell.querySelectorAll('.ichat-thread .ichat-message.own').length;
    var files = [];
    shell.querySelectorAll('.ichat-thread a.ichat-attachment, .ichat-thread .ichat-attachment a').forEach(function (a) {
      var n = a.querySelector('.ichat-attachment-name');
      var label = (n ? n.textContent : a.textContent).trim();
      if (a.href && files.every(function (f) { return f.href !== a.href; })) files.push({ href: a.href, name: label });
    });

    var avatar = avatarSrc
      ? '<img src="' + esc(avatarSrc.getAttribute('src')) + '" alt="">'
      : esc((avatarTxt ? avatarTxt.textContent.trim() : name).slice(0, 1).toUpperCase());

    panel.innerHTML =
      '<div class="ichat-details-head">' +
        '<button type="button" class="ichat-details-close" title="Cerrar"><i class="bi bi-x-lg"></i></button>' +
        '<div class="ichat-details-avatar">' + avatar + '</div>' +
        '<div class="ichat-details-name">' + esc(name) + '</div>' +
        '<div class="ichat-details-meta">' + esc(meta) + '</div>' +
      '</div>' +
      '<div class="ichat-details-section"><h4>Actividad</h4>' +
        '<div class="ichat-details-stats">' +
          '<div class="ichat-details-stat"><b>' + msgs.length + '</b><span>Mensajes</span></div>' +
          '<div class="ichat-details-stat"><b>' + own + '</b><span>Enviados</span></div>' +
          '<div class="ichat-details-stat"><b>' + files.length + '</b><span>Archivos</span></div>' +
        '</div></div>' +
      '<div class="ichat-details-section"><h4>Archivos compartidos <span>' + files.length + '</span></h4>' +
        (files.length
          ? '<div class="ichat-details-files">' + files.slice().reverse().map(function (f) {
              return '<a class="ichat-details-file" href="' + esc(f.href) + '" target="_blank" rel="noopener">' +
                '<i class="bi ' + iconFor(f.name) + '"></i><span>' + esc(f.name) + '</span></a>';
            }).join('') + '</div>'
          : '<div class="ichat-details-empty">Aún no hay archivos en esta conversación.</div>') +
      '</div>';
    panel.querySelector('.ichat-details-close').addEventListener('click', function () { setOpen(false); });
  }

  function setOpen(open) {
    shell.classList.toggle('ichat-has-details', open);
    store(open);
    if (open) render();
  }
  toggle.addEventListener('click', function () { setOpen(!shell.classList.contains('ichat-has-details')); });

  var thread = shell.querySelector('.ichat-thread');
  if (thread && window.MutationObserver) {
    var t = null;
    new MutationObserver(function () {
      tidy();
      if (!shell.classList.contains('ichat-has-details')) return;
      clearTimeout(t); t = setTimeout(render, 250);
    }).observe(thread, { childList: true, subtree: true });
  }

  var pref = stored();
  setOpen(pref === null ? window.innerWidth >= 1600 : pref === '1');
})();
