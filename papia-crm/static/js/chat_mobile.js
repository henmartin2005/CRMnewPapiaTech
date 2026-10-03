/* ══ Chat interno — comportamiento móvil (lista ↔ conversación, estilo app de mensajería) ══ */
(function () {
  'use strict';
  var shell = document.getElementById('chatShell');
  if (!shell) return;
  var mq = window.matchMedia('(max-width: 700px)');
  var params = new URLSearchParams(location.search);
  var hasConversation = params.has('org_id') || params.has('user_id');
  var sidebar = document.getElementById('chatSidebar');
  var overlay = document.getElementById('chatDrawerOverlay');
  var thread = document.getElementById('chatThread');
  var isDirect = !!document.querySelector('.ichat-header-info .ichat-person-avatar');

  // ── Pantalla: lista o conversación ──
  function apply() {
    if (mq.matches) shell.classList.toggle('m-list', !hasConversation);
    else shell.classList.remove('m-list');
  }
  apply();
  if (mq.addEventListener) mq.addEventListener('change', apply); else mq.addListener(apply);

  // Botón del header: en móvil es "volver" a la lista (no abre el drawer)
  var back = document.getElementById('chatDrawerButton');
  if (back) {
    var icon = back.querySelector('i');
    var syncIcon = function () { if (icon) icon.className = 'bi ' + (mq.matches ? 'bi-chevron-left' : 'bi-list'); };
    syncIcon();
    if (mq.addEventListener) mq.addEventListener('change', syncIcon);
    back.setAttribute('aria-label', 'Volver a los chats');
  }
  document.addEventListener('click', function (e) {
    if (!mq.matches || !e.target.closest('#chatDrawerButton')) return;
    e.preventDefault();
    e.stopPropagation();
    if (sidebar) sidebar.classList.remove('mobile-open');
    if (overlay) overlay.classList.remove('open');
    hasConversation = false;
    shell.classList.add('m-list');
    try { history.replaceState(null, '', location.pathname); } catch (err) {}
  }, true);

  // ── Filtros tipo chips en la lista ──
  var orgList = document.getElementById('orgList');
  if (orgList) {
    document.querySelectorAll('.ichat-org').forEach(function (row) {
      if (row.querySelector('.ichat-unread')) row.classList.add('has-unread');
    });
    var chips = document.createElement('div');
    chips.className = 'ichat-chips';
    chips.innerHTML =
      '<button type="button" class="ichat-chip on" data-chip="all">Todos</button>' +
      '<button type="button" class="ichat-chip" data-chip="unread">No leídos</button>' +
      '<button type="button" class="ichat-chip" data-chip="agencies">Agencias</button>' +
      '<button type="button" class="ichat-chip" data-chip="people">Personas</button>';
    orgList.parentNode.insertBefore(chips, orgList);
    var current = 'all';
    var search = document.getElementById('orgSearch');
    var filterRows = function () {
      var q = search ? search.value.trim().toLowerCase() : '';
      document.querySelectorAll('.ichat-org').forEach(function (row) {
        var sec = row.dataset.searchSection;
        var ok = (!q || (row.dataset.orgName || '').indexOf(q) >= 0) &&
          (current === 'all' ||
           (current === 'unread' && row.classList.contains('has-unread')) ||
           (current === 'agencies' && sec === 'agencies') ||
           (current === 'people' && sec !== 'agencies'));
        row.style.display = ok ? '' : 'none';
      });
    };
    chips.addEventListener('click', function (e) {
      var b = e.target.closest('[data-chip]');
      if (!b) return;
      current = b.dataset.chip;
      chips.querySelectorAll('.ichat-chip').forEach(function (c) { c.classList.toggle('on', c === b); });
      filterRows();
    });
    if (search) search.addEventListener('input', function () { if (current !== 'all') setTimeout(filterRows, 0); });
  }

  // ── Burbujas: hora (y autor en grupos) dentro del mensaje ──
  function enhance(msg) {
    if (!msg || msg.dataset.mEnhanced) return;
    msg.dataset.mEnhanced = '1';
    var bubble = msg.querySelector('.ichat-bubble');
    if (!bubble) return;
    var spans = msg.querySelectorAll('.ichat-msg-author > span');
    var raw = spans.length ? spans[spans.length - 1].textContent.trim() : '';
    var m = raw.match(/\d{1,2}:\d{2}(?:\s?[ap]\.?\s?m\.?)?/i);
    var label = m ? m[0] : raw;
    if (/^\d{4}-\d{2}-\d{2}T/.test(raw)) {
      var d = new Date(raw);
      if (!isNaN(d)) label = d.toLocaleTimeString('es-US', { hour: 'numeric', minute: '2-digit' });
    }
    var own = msg.classList.contains('own');
    if (!own && !isDirect && !msg.classList.contains('grouped')) {
      var nameEl = msg.querySelector('.ichat-msg-author strong');
      if (nameEl) {
        var a = document.createElement('span');
        a.className = 'ichat-wa-author';
        a.textContent = nameEl.textContent.trim();
        var hue = 0; for (var i = 0; i < a.textContent.length; i++) hue = (hue * 31 + a.textContent.charCodeAt(i)) % 360;
        a.style.color = 'hsl(' + hue + ',55%,42%)';
        bubble.insertBefore(a, bubble.firstChild);
      }
    }
    if (label) {
      var t = document.createElement('span');
      t.className = 'ichat-wa-time';
      t.textContent = label;
      if (own) {
        var ck = document.createElement('i');
        ck.className = 'bi ' + (msg.classList.contains('pending') ? 'bi-clock' : 'bi-check2-all');
        t.appendChild(ck);
      }
      bubble.appendChild(t);
    }
  }
  if (thread) {
    thread.querySelectorAll('.ichat-message').forEach(enhance);
    new MutationObserver(function (muts) {
      muts.forEach(function (mu) {
        mu.addedNodes.forEach(function (n) {
          if (n.nodeType === 1 && n.classList.contains('ichat-message')) enhance(n);
        });
      });
    }).observe(thread, { childList: true });

    // Tocar una burbuja muestra las reacciones
    thread.addEventListener('click', function (e) {
      if (!mq.matches || e.target.closest('a,button')) return;
      var msg = e.target.closest('.ichat-message');
      if (!msg) return;
      var open = !msg.classList.contains('m-actions');
      thread.querySelectorAll('.ichat-message.m-actions').forEach(function (x) { x.classList.remove('m-actions'); });
      if (open) msg.classList.add('m-actions');
    });
  }

  // ── Accesos rápidos sobre el composer (Archivos / Imágenes / Emoji) ──
  var compose = document.querySelector('.ichat-compose');
  var fileInput = document.getElementById('chatAttachment');
  var emojiBtn = document.getElementById('emojiButton');
  if (compose && fileInput) {
    var quick = document.createElement('div');
    quick.className = 'ichat-quick';
    quick.innerHTML =
      '<button type="button" data-q="files"><i class="bi bi-file-earmark"></i> Archivos</button>' +
      '<button type="button" data-q="images"><i class="bi bi-image"></i> Imágenes</button>' +
      '<button type="button" data-q="emoji"><i class="bi bi-emoji-smile"></i> Emoji</button>';
    compose.parentNode.insertBefore(quick, compose);
    var baseAccept = fileInput.getAttribute('accept') || '';
    quick.addEventListener('click', function (e) {
      var b = e.target.closest('[data-q]');
      if (!b) return;
      e.stopPropagation();
      if (b.dataset.q === 'emoji') { if (emojiBtn) emojiBtn.click(); return; }
      fileInput.setAttribute('accept', b.dataset.q === 'images' ? '.png,.jpg,.jpeg,.gif,.webp' : baseAccept);
      fileInput.click();
      setTimeout(function () { fileInput.setAttribute('accept', baseAccept); }, 1000);
    });
    var input = document.getElementById('chatMessage');
    if (input && mq.matches) input.setAttribute('placeholder', 'Escribe un mensaje…');
  }

  if (mq.matches && thread && hasConversation) thread.scrollTop = thread.scrollHeight;
})();
