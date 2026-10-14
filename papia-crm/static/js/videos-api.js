export function message(text, error = false) {
  const target = document.getElementById('videos-message');
  target.textContent = text;
  target.className = error ? 'videos-error' : 'videos-success';
}

export async function api(path, data, signal) {
  const csrf = document.querySelector('[data-csrf]').dataset.csrf;
  const response = await fetch(`/videos/api/${path}`, {
    method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal,
    headers: {'Content-Type': 'application/json', 'X-Videos-CSRF': csrf},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (response.redirected) throw new Error('La sesión caducó. Vuelve a iniciar sesión.');
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (body.reconnect) document.getElementById('drive-connect')?.removeAttribute('hidden');
    throw new Error(body.error || `No se pudo completar la operación (${response.status}).`);
  }
  return body;
}

function sendChunk(url, blob, contentRange, signal, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    if (signal?.aborted) return reject(new DOMException('Cancelado', 'AbortError'));
    xhr.open('PUT', url);
    xhr.timeout = 120000;
    xhr.setRequestHeader('Content-Range', contentRange);
    xhr.setRequestHeader('Content-Type', blob.type || 'video/mp4');
    xhr.upload.onprogress = event => onProgress?.(event.loaded);
    xhr.onload = () => {
      signal?.removeEventListener('abort', abort);
      resolve({status: xhr.status, range: xhr.getResponseHeader('Range'),
        body: (() => { try { return JSON.parse(xhr.responseText); } catch { return {}; } })()});
    };
    const failed = () => { signal?.removeEventListener('abort', abort); reject(new Error('Se interrumpió la conexión con Drive.')); };
    xhr.onerror = failed;
    xhr.ontimeout = failed;
    xhr.onabort = () => { signal?.removeEventListener('abort', abort); reject(new DOMException('Cancelado', 'AbortError')); };
    signal?.addEventListener('abort', abort, {once: true});
    xhr.send(blob);
  });
}

function acknowledged(result, size) {
  if (result.status === 200 || result.status === 201) return size;
  if (result.status !== 308) throw new Error(`Drive rechazó la subida (${result.status}).`);
  if (!result.range) return 0;
  const match = /^bytes=0-(\d+)$/.exec(result.range);
  if (!match || Number(match[1]) >= size) throw new Error('Drive devolvió un rango de subida inválido.');
  return Number(match[1]) + 1;
}

export async function upload(blob, metadata, progress, signal, state = {}) {
  if (!state.session) {
    state.session = await api('upload-session', {nombre: metadata.titulo, mime_type: blob.type || 'video/mp4',
      size: blob.size, cliente_id: metadata.cliente_id}, signal);
    state.offset = 0;
  }
  const url = state.session.upload_url;
  const chunkSize = 8 * 1024 * 1024;
  let failures = 0;
  // Always probe an existing session, including an uncertain last chunk.
  if (!state.fileId && state.started) {
    const status = await sendChunk(url, new Blob([]), `bytes */${blob.size}`, signal);
    state.offset = acknowledged(status, blob.size);
    state.fileId = status.body.id;
  }
  state.started = true;
  while (!state.fileId) {
    if (signal?.aborted) throw new DOMException('Cancelado', 'AbortError');
    try {
      const start = state.offset;
      const end = Math.min(start + chunkSize, blob.size);
      const result = await sendChunk(url, blob.slice(start, end, blob.type),
        `bytes ${start}-${end - 1}/${blob.size}`, signal,
        loaded => progress(Math.min(100, (start + loaded) / blob.size * 100)));
      if (result.status === 404 || result.status === 410) {
        state.session = null;
        throw new Error('La sesión de Drive caducó. Pulsa Reintentar para crear otra.');
      }
      const next = acknowledged(result, blob.size);
      if (next <= start && !result.body.id) throw new Error('Drive no confirmó el bloque.');
      state.offset = next;
      state.fileId = result.body.id;
      if (next === blob.size && !state.fileId) throw new Error('Drive no devolvió el identificador del archivo.');
      failures = 0;
    } catch (error) {
      if (error.name === 'AbortError' || !state.session) throw error;
      if (++failures > 4) throw error;
      await new Promise(resolve => setTimeout(resolve, Math.min(1000 * 2 ** failures, 8000)));
      const status = await sendChunk(url, new Blob([]), `bytes */${blob.size}`, signal);
      if (status.status === 404 || status.status === 410) {
        state.session = null;
        throw new Error('La sesión de Drive caducó. Pulsa Reintentar para crear otra.');
      }
      state.offset = acknowledged(status, blob.size);
      state.fileId = status.body.id;
    }
  }
  const registered = await api('registrar', {...metadata, drive_file_id: state.fileId,
    upload_id: state.session.upload_id}, signal);
  progress(100);
  return registered;
}

export async function downloadDrive(item, signal) {
  const {access_token: accessToken} = await api('token', undefined, signal);
  const response = await fetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(item.drive_file_id)}?alt=media`,
    {headers: {Authorization: `Bearer ${accessToken}`}, signal});
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403
    ? 'Reconecta Google Drive para descargar este video.' : 'No se pudo descargar el video de Drive.');
  return response.blob();
}
