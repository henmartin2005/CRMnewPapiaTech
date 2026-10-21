export const PHOTO_DURATION = 5;

function checkAbort(signal) {
  if (signal?.aborted) throw new DOMException('Cancelado', 'AbortError');
}

export function videoDuration(url, signal) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    let timer;
    const finish = (error, value) => {
      clearTimeout(timer); signal?.removeEventListener('abort', abort);
      video.onloadeddata = null; video.onerror = null;
      video.removeAttribute('src'); video.load();
      if (error) reject(error); else resolve(value);
    };
    const abort = () => finish(new DOMException('Cancelado', 'AbortError'));
    if (signal?.aborted) { abort(); return; }
    // Metadata alone can load even when the browser cannot decode HEVC frames.
    video.preload = 'auto';
    video.onloadeddata = () => Number.isFinite(video.duration) && video.duration > 0
      ? finish(null, video.duration) : finish(new Error('No se pudo leer la duración.'));
    video.onerror = () => finish(new Error('El navegador necesita una versión compatible.'));
    timer = setTimeout(() => finish(new Error('No se pudo leer el video a tiempo.')), 20000);
    signal?.addEventListener('abort', abort, {once:true}); video.src = url;
  });
}

export async function imageForRendering(blob, signal) {
  checkAbort(signal);
  let bitmap;
  try { bitmap = await createImageBitmap(blob, {imageOrientation:'from-image'}); }
  catch {
    throw new Error(blob.type === 'image/heic' || blob.type === 'image/heif'
      ? 'Este navegador no puede abrir esta foto HEIC. Expórtala como JPEG desde Fotos. Para un video o Live Photo, importa su archivo MOV original.'
      : 'No se pudo abrir la imagen. Si querías importar un video desde Fotos, exporta e importa su MOV original.');
  }
  try {
    checkAbort(signal);
    const scale = Math.min(1, 3840 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(2, Math.round(bitmap.width * scale));
    canvas.height = Math.max(2, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    context.fillStyle = 'black'; context.fillRect(0,0,canvas.width,canvas.height);
    context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    const output = await new Promise(resolve => canvas.toBlob(resolve,'image/jpeg',0.95));
    if (!output) throw new Error('No se pudo preparar la foto.');
    checkAbort(signal); return output;
  } finally { bitmap.close(); }
}

export function previewArguments(input, output, image) {
  return [...(image ? ['-loop','1','-framerate','12'] : []), '-i',input,
    ...(image ? ['-t',String(PHOTO_DURATION)] : []),
    '-map','0:v:0','-map','0:a:0?',
    '-vf',"scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
    '-c:v','libx264','-preset','ultrafast','-crf','28','-c:a','aac',
    '-movflags','+faststart','-y',output];
}

export async function compatiblePreview(ffmpeg, blob, image, signal) {
  const id = crypto.randomUUID();
  const input = `import-${id}.${image ? 'jpg' : 'mov'}`, output = `preview-${id}.mp4`;
  try {
    checkAbort(signal);
    await ffmpeg.writeFile(input,new Uint8Array(await blob.arrayBuffer()));
    checkAbort(signal);
    const status = await ffmpeg.exec(previewArguments(input,output,image));
    checkAbort(signal);
    if (status !== 0) throw new Error('No se pudo adaptar este archivo en el navegador. Si viene de Fotos, exporta el video original como MOV e impórtalo de nuevo.');
    return new Blob([await ffmpeg.readFile(output)],{type:'video/mp4'});
  } finally {
    for (const file of [input,output]) try { await ffmpeg.deleteFile(file); } catch {}
  }
}
