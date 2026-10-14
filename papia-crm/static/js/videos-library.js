import {api, message} from './videos-api.js';
document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', async () => {
  if (!confirm('¿Mover este video a la papelera de Drive?')) return;
  button.disabled = true;
  try {
    await api(`${button.dataset.delete}/eliminar`, {});
    button.closest('article').remove();
    message('Video movido a la papelera de Drive.');
  } catch (error) { message(error.message, true); button.disabled = false; }
}));
