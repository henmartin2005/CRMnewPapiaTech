/* Papia CRM — main.js */

// ── Sidebar mobile toggle ──
(function () {
  const toggle  = document.getElementById('sbToggle');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sbOverlay');
  if (!toggle) return;

  function open()  { sidebar.classList.add('open'); overlay.classList.add('open'); document.body.style.overflow = 'hidden'; }
  function close() { sidebar.classList.remove('open'); overlay.classList.remove('open'); document.body.style.overflow = ''; }

  toggle.addEventListener('click', open);
  overlay.addEventListener('click', close);
})();

// ── Sidebar theme toggle ──
(function () {
  const btn  = document.getElementById('themeToggle');
  const icon = document.getElementById('themeIcon');
  if (!btn) return;

  const LIGHT = 'sidebar-light';
  const stored = localStorage.getItem('sb-theme');
  if (stored === LIGHT) {
    document.body.classList.add(LIGHT);
    icon.className = 'bi bi-moon';
  }

  btn.addEventListener('click', function () {
    const isLight = document.body.classList.toggle(LIGHT);
    icon.className = isLight ? 'bi bi-moon' : 'bi bi-circle-half';
    localStorage.setItem('sb-theme', isLight ? LIGHT : '');
  });
})();

// ── Keyboard shortcuts ──
(function () {
  document.addEventListener('keydown', function (e) {
    // Ignore when typing in an input/textarea/select
    const tag = document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.metaKey || e.ctrlKey || e.altKey) {
      // ⌘K / Ctrl+K → focus search
      if (e.key === 'k') {
        e.preventDefault();
        const search = document.getElementById('searchInput') || document.querySelector('.search-input');
        if (search) { search.focus(); search.select(); }
      }
      return;
    }

    switch (e.key) {
      case '1': window.location.href = '/'; break;
      case '2': window.location.href = '/clients/'; break;
      case '3': window.location.href = '/pipeline/'; break;
    }
  });
})();

// ── Auto-dismiss success flashes after 4s ──
(function () {
  setTimeout(function () {
    document.querySelectorAll('.flash-msg.success').forEach(function (el) {
      el.style.transition = 'opacity .3s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 300);
    });
  }, 4000);
})();
