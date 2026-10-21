import {api, upload, downloadDrive, message} from './videos-api.js';
import {mediaFile, droppedFiles, isFileDrag} from './videos-import.js';
import {videoDuration, imageForRendering, compatiblePreview, PHOTO_DURATION} from './videos-media.js';
const $ = id => document.getElementById(id);
const source = JSON.parse($('videos-data').textContent).source;
const library = JSON.parse($('drive-items').textContent);
const clips = [];
let selected = null;
let engine = null;
let controller = null;
let busy = false;
let result = null;
let resultUrl = null;
let dragged = null;
let phase = {base: 0, weight: 0};
let timelineScale = 45;
const blobUrls = [];

function progress(value, label) {
  $('video-progress').value = Math.max(0, Math.min(100, value));
  $('video-progress-label').textContent = label;
}

function setBusy(value) {
  busy = value;
  for (const input of document.querySelectorAll('#video-editor input, #video-editor select, #video-editor textarea, #video-editor button')) {
    input.disabled = value;
  }
  $('cancel-export').disabled = !value;
  $('retry-save').disabled = value;
  $('drive-connect').style.pointerEvents = value ? 'none' : '';
  if (!value) renderTimeline();
}

function clientId() {
  const value = $('client-search').value.trim();
  if (!value) return null;
  const option = [...$('video-clients').options].find(item => item.value === value);
  if (!option) throw new Error('Selecciona un cliente de la lista o deja el campo vacío.');
  return Number(option.dataset.id);
}

function warnSize(blob) {
  if (blob.size > 500 * 1024 * 1024) {
    return confirm('Este archivo supera 500 MB. El navegador puede quedarse sin memoria al editarlo. ¿Quieres continuar?');
  }
  return true;
}

async function addClip(blob, name, parent = null) {
  let url = URL.createObjectURL(blob);
  const image = blob.type.startsWith('image/');
  let renderBlob = blob;
  try {
    let duration;
    if (!image) {
      try { duration = await videoDuration(url,controller?.signal); }
      catch (error) { if (error.name === 'AbortError') throw error; }
    }
    if (duration === undefined) {
      message(image ? 'Preparando la foto como clip de 5 segundos…' : 'Preparando una vista previa compatible del video de iPhone. El archivo original se conserva.');
      if (image) renderBlob = await imageForRendering(blob,controller?.signal);
      const ffmpeg = await loadEngine();
      phase = {base:0,weight:100,label:image ? 'Preparando foto…' : 'Adaptando la vista previa del video…'};
      const preview = await compatiblePreview(ffmpeg,renderBlob,image,controller?.signal);
      URL.revokeObjectURL(url); url = URL.createObjectURL(preview);
      duration = image ? PHOTO_DURATION : await videoDuration(url,controller?.signal);
    }
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('La duración del clip no es válida.');
    const clip = {id: crypto.randomUUID(), blob, name, url, duration, start: 0, end: duration,
      speed: 1, parent, image, renderBlob, uploadState: {}, saved: Boolean(parent)};
    clips.push(clip);
    selectClip(clip);
    renderTimeline();
    createThumbnail(clip);
  } catch (error) { URL.revokeObjectURL(url); throw error; }
}

function selectClip(clip) {
  selected = clip;
  $('preview').src = clip.url;
  $('preview').currentTime = clip.start;
  $('preview').playbackRate = clip.speed;
  $('preview-empty').hidden = true;
  $('selected-clip-name').textContent = clip.name;
}

function totalDuration() { return clips.reduce((sum, c) => sum + (c.end - c.start) / c.speed, 0); }
function renderTimeline() {
  $('timeline').replaceChildren();
  clips.forEach((clip, index) => {
    const li = document.createElement('li');
    li.className = `videos-clip ${selected === clip ? 'is-selected' : ''}`;
    li.draggable = true;
    li.dataset.id = clip.id;
    li.style.width = `${clipWidth(clip)}px`;
    li.setAttribute('aria-label', `Clip ${index + 1}: ${clip.name}`);
    const strip = document.createElement('div');
    strip.className = 'studio-clip-strip';
    if (clip.thumbnail) strip.style.backgroundImage = `url(${clip.thumbnail})`;
    strip.onclick = event => { if (!busy && !event.target.closest('button')) { selectClip(clip); renderTimeline(); } };
    const title = document.createElement('div');
    title.className = 'videos-clip-title';
    const name = document.createElement('strong'); name.textContent = `${index + 1}. ${clip.name}`;
    title.append(name);
    const button = (label, action) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'btn btn-ghost btn-sm';
      b.textContent = label; b.onclick = action; title.append(b); return b;
    };
    button('Ver', () => { selectClip(clip); renderTimeline(); });
    button('↑', () => move(index, index - 1)).ariaLabel = 'Mover clip antes';
    button('↓', () => move(index, index + 1)).ariaLabel = 'Mover clip después';
    button('Quitar', () => {
      clips.splice(index, 1); URL.revokeObjectURL(clip.url);
      if (selected === clip) { selected = null; $('preview').removeAttribute('src'); $('preview').load(); }
      renderTimeline();
    });
    const controls = document.createElement('div'); controls.className = 'videos-inline';
    for (const [key, labelText] of [['start', 'Inicio (s)'], ['end', 'Fin (s)']]) {
      const label = document.createElement('label'); label.textContent = labelText;
      const input = document.createElement('input'); input.type = 'number'; input.step = '0.01';
      input.min = '0'; input.max = String(clip.duration); input.value = clip[key].toFixed(2);
      input.onchange = () => {
        const value = Number(input.value);
        if (!Number.isFinite(value) || value < 0 || value > clip.duration ||
          (key === 'start' ? value >= clip.end : value <= clip.start)) {
          input.value = clip[key].toFixed(2); message('El inicio debe ser anterior al fin y ambos deben estar dentro del clip.', true); return;
        }
        clip[key] = value; if (selected === clip) $('preview').currentTime = clip.start;
        li.style.width = `${clipWidth(clip)}px`;
        $('total-time').textContent = `${totalDuration().toFixed(1)} s`;
        updateStudio();
      };
      label.append(input); controls.append(label);
    }
    const label = document.createElement('label'); label.textContent = 'Velocidad';
    const speed = document.createElement('select');
    [0.5, 1, 1.5, 2].forEach(value => { const option = new Option(`${value}x`, value); option.selected = value === clip.speed; speed.add(option); });
    speed.onchange = () => { clip.speed = Number(speed.value); if (selected === clip) $('preview').playbackRate = clip.speed; li.style.width = `${clipWidth(clip)}px`; $('total-time').textContent = `${totalDuration().toFixed(1)} s`; updateStudio(); };
    label.append(speed); controls.append(label); strip.append(title); li.append(strip, controls);
    li.ondragstart = event => { if (busy) {event.preventDefault(); return;} dragged = index; event.dataTransfer.setData('text/plain', clip.id); };
    li.ondragover = event => event.preventDefault();
    li.ondrop = event => { event.preventDefault(); if (!busy && dragged !== null) move(dragged, index); dragged = null; };
    $('timeline').append(li);
  });
  $('total-time').textContent = `${totalDuration().toFixed(1)} s`;
  updateStudio();
}
function move(from, to) { if (busy || to < 0 || to >= clips.length) return; clips.splice(to, 0, clips.splice(from, 1)[0]); renderTimeline(); }
$('preview').addEventListener('timeupdate', () => { if (selected && $('preview').currentTime >= selected.end) { $('preview').pause(); $('preview').currentTime = selected.start; } });
$('preview').addEventListener('play', () => { if (selected && ($('preview').currentTime < selected.start || $('preview').currentTime >= selected.end)) $('preview').currentTime = selected.start; });

async function addDrive(item) {
  if (!item) throw new Error('Selecciona un video de la biblioteca.');
  if (item.size_bytes && !warnSize({size:item.size_bytes})) return;
  message('Descargando el clip directamente desde Google Drive…');
  const blob = await downloadDrive(item, controller.signal);
  if (!item.size_bytes && !warnSize(blob)) return;
  const media = (!blob.type || blob.type === 'application/octet-stream') && item.mime_type
    ? new Blob([blob], {type:item.mime_type}) : blob;
  await addClip(media, item.titulo, item.id);
  message('Clip añadido a la línea de tiempo.');
}

async function importClips(files) {
  if (busy) { message('Espera a que termine la operación actual antes de agregar videos.', true); return; }
  if (!files.length) { message('Arrastra archivos de video desde tu equipo.', true); return; }
  controller = new AbortController();
  setBusy(true);
  let added = 0;
  let photos = 0;
  const errors = [];
  try {
    for (const file of files) {
      if (controller.signal.aborted) break;
      const clip = mediaFile(file);
      if (!clip) { errors.push(`${file.name}: importa un video MOV/MP4 o una foto JPEG, PNG o WebP.`); continue; }
      if (!warnSize(clip)) continue;
      try {
        progress(added / files.length * 100, `Importando ${file.name}…`);
        await addClip(clip, clip.name);
        added++;
        if (clip.type.startsWith('image/')) photos++;
      } catch (error) {
        if (controller.signal.aborted || error.name === 'AbortError') break;
        errors.push(`${file.name}: ${error.message}`);
      }
    }
    const success = added ? `${added} ${added === 1 ? 'clip agregado' : 'clips agregados'} a la línea de tiempo.` + (photos ? ' Las fotos duran 5 segundos. Para conservar el movimiento de un video o Live Photo, importa el MOV original.' : '') : '';
    message([success, ...errors, controller.signal.aborted ? 'Importación cancelada.' : ''].filter(Boolean).join(' ') || 'Importación cancelada.', errors.length > 0);
    progress(controller.signal.aborted ? $('video-progress').value : 100,
      controller.signal.aborted ? 'Importación cancelada' : added ? 'Clips listos para editar' : 'No se agregaron clips');
  } finally { releaseImportEngine(); setBusy(false); }
}
$('clip-files').onchange = async event => {
  const files = [...event.target.files];
  event.target.value = '';
  await importClips(files);
};
$('add-drive').onclick = async () => {
  controller = new AbortController(); setBusy(true);
  try { await addDrive(library.find(v => v.id === Number($('drive-source').value))); }
  catch (error) { message(error.message, true); }
  finally { releaseImportEngine(); setBusy(false); }
};

function releaseImportEngine() {
  if (engine) {engine.terminate();engine=null;}
  for (const url of blobUrls.splice(0)) URL.revokeObjectURL(url);
}

async function toBlobURL(url, mime) {
  const response = await fetch(url, {signal: controller.signal});
  if (!response.ok) throw new Error('No se pudo cargar el motor de edición. Revisa la conexión.');
  const blob = new Blob([await response.arrayBuffer()], {type: mime});
  const result = URL.createObjectURL(blob); blobUrls.push(result); return result;
}

async function loadEngine() {
  if (engine?.loaded) return engine;
  engine = new window.FFmpegWASM.FFmpeg();
  engine.on('progress', ({progress: value}) => {
    progress(phase.base + Math.max(0, Math.min(1, value)) * phase.weight, phase.label || 'Procesando…');
  });
  const base = 'https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/umd';
  progress(0, 'Cargando el motor de edición…');
  const coreURL = await toBlobURL(`${base}/ffmpeg-core.js`, 'text/javascript');
  const wasmURL = await toBlobURL(`${base}/ffmpeg-core.wasm`, 'application/wasm');
  await engine.load({coreURL, wasmURL});
  for (const url of blobUrls.splice(0)) URL.revokeObjectURL(url);
  return engine;
}

async function execute(args) {
  if (controller.signal.aborted) throw new DOMException('Cancelado', 'AbortError');
  const status = await engine.exec(args);
  if (status !== 0) throw new Error('El motor no pudo procesar este clip o efecto. Prueba otro formato o un archivo más pequeño.');
}

async function compile() {
  if (!clips.length) throw new Error('Agrega al menos un clip.');
  clientId();
  const total = totalDuration();
  const overlay = $('overlay-text').value.trim();
  const textStart = Number($('text-start').value), textEnd = Number($('text-end').value);
  const fontSize = Number($('text-size').value);
  if (overlay && (!Number.isFinite(textStart) || !Number.isFinite(textEnd) || textStart < 0 || textEnd <= textStart || textStart >= total || fontSize < 16 || fontSize > 180)) {
    throw new Error('Revisa el tamaño y el intervalo del texto: debe comenzar dentro del video.');
  }
  if (clips.reduce((sum,c) => sum+c.blob.size,0) > 500*1024*1024 && !confirm('Los clips suman más de 500 MB. La edición podría agotar la memoria. ¿Continuar?')) return null;
  await loadEngine();
  const [width, height] = $('output-format').value.split(':').map(Number);
  const fit = $('output-fit').value === 'crop'
    ? `scale=${width}:${height}:force_original_aspect_ratio=increase,crop=${width}:${height}`
    : `scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:black`;
  let elapsed = 0;
  for (let i=0; i<clips.length; i++) {
    const clip = clips[i];
    const ext = clip.image ? 'jpg' : (clip.name.split('.').pop() || 'mp4').replace(/[^a-zA-Z0-9]/g,'').slice(0,8) || 'mp4';
    const input = `input${i}.${ext}`;
    const duration = (clip.end-clip.start)/clip.speed;
    await engine.writeFile(input, new Uint8Array(await (clip.renderBlob || clip.blob).arrayBuffer()));
    // The 0.12.10 core can return -1 even after writing valid ffprobe JSON.
    // Validate the actual output, and never reuse a preceding clip's report.
    try { await engine.deleteFile('probe.json'); } catch {}
    await engine.ffprobe(['-v','error','-show_streams','-of','json',input,'-o','probe.json']);
    const info = JSON.parse(new TextDecoder().decode(await engine.readFile('probe.json')));
    if (!Array.isArray(info.streams) || !info.streams.some(s => s.codec_type === 'video')) throw new Error('No se pudo analizar el clip.');
    const hasAudio = info.streams.some(s => s.codec_type === 'audio');
    phase = {base: elapsed/total*65, weight: duration/total*65, label: `Procesando clip ${i+1} de ${clips.length}…`};
    await execute([...(clip.image ? ['-loop','1','-framerate','30'] : []),'-ss',String(clip.start),'-t',String(clip.end-clip.start),'-i',input,
      '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000',
      '-filter_complex',`[0:v]setpts=(PTS-STARTPTS)/${clip.speed},${fit},setsar=1,fps=30,format=yuv420p[v];`+
        (hasAudio ? `[0:a]asetpts=PTS-STARTPTS,atempo=${clip.speed},aresample=48000,apad[a]` : '[1:a]anull[a]'),
      '-map','[v]','-map','[a]','-t',String(duration),'-c:v','libx264','-preset','ultrafast',
      '-crf','23','-c:a','aac','-ar','48000','-ac','2','-y',`clip${i}.mp4`]);
    await engine.deleteFile(input); elapsed += duration;
  }
  await engine.writeFile('list.txt', new TextEncoder().encode(clips.map((_,i) => `file 'clip${i}.mp4'`).join('\n')));
  phase = {base:65,weight:5,label:'Uniendo clips…'};
  await execute(['-f','concat','-safe','0','-i','list.txt','-c','copy','-y','joined.mp4']);
  for (let i=0; i<clips.length; i++) await engine.deleteFile(`clip${i}.mp4`);
  const args = ['-i','joined.mp4'];
  const music = $('music-file').files[0];
  if (music) { await engine.writeFile('music', new Uint8Array(await music.arrayBuffer())); args.push('-stream_loop','-1','-i','music'); }
  let videoFilter = '[0:v]null[v]';
  if (overlay) {
    const fontResponse = await fetch($('video-editor').dataset.font, {signal:controller.signal});
    if (!fontResponse.ok) throw new Error('No se pudo cargar la fuente del texto.');
    await engine.writeFile('font.ttf',new Uint8Array(await fontResponse.arrayBuffer()));
    await engine.writeFile('overlay.txt',new TextEncoder().encode(overlay));
    const y = {top:'h*0.08',center:'(h-text_h)/2',bottom:'h*0.92-text_h'}[$('text-position').value];
    const color = $('text-color').value.replace('#','0x');
    videoFilter = `[0:v]drawtext=fontfile=font.ttf:textfile=overlay.txt:expansion=none:fontsize=${fontSize}:fontcolor=${color}:x=(w-text_w)/2:y=${y}:shadowcolor=black:shadowx=2:shadowy=2:enable='between(t,${textStart},${Math.min(total,textEnd)})'[v]`;
  }
  const fade = `afade=t=out:st=${Math.max(0,total-2)}:d=${Math.min(2,total)}`;
  let audio = `[0:a]volume=${Number($('original-volume').value)}[original];`;
  if (music) audio += `[1:a]volume=${Number($('music-volume').value)},aresample=48000[music];[original][music]amix=inputs=2:duration=first:normalize=0,${fade}[a]`;
  else audio += `[original]${fade}[a]`;
  args.push('-filter_complex',videoFilter+';'+audio,'-map','[v]','-map','[a]',
    '-t',String(total),'-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p',
    '-c:a','aac','-b:a','192k','-movflags','+faststart','-y','output.mp4');
  phase = {base:70,weight:30,label:'Aplicando texto y audio…'};
  await execute(args);
  const bytes = await engine.readFile('output.mp4');
  const blob = new Blob([bytes], {type:'video/mp4'});
  engine.terminate(); engine = null;
  return {blob, metadata:{titulo:($('video-title').value.trim() || 'Mi video')+'.mp4',
    descripcion:$('video-description').value,cliente_id:clientId(),tipo:'editado',
    video_padre_id:source?.id || clips.find(c=>c.parent)?.parent || null,duracion_seg:total}, state:{}};
}

async function saveResult() {
  progress(0,'Subiendo el MP4 directamente a Google Drive…');
  await upload(result.blob,result.metadata,p=>progress(p,`Guardando en Drive: ${Math.round(p)} %`),controller.signal,result.state);
  $('retry-save').hidden = true;
  message('Video editado guardado en Google Drive. Ya está en tu biblioteca.');
}

$('export-video').onclick = async () => {
  controller = new AbortController(); setBusy(true); $('retry-save').hidden = true;
  try {
    const compiled = await compile();
    if (!compiled) return;
    result = compiled;
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    resultUrl = URL.createObjectURL(result.blob);
    $('export-download').href = resultUrl; $('export-download').download = result.metadata.titulo;
    $('export-download').hidden = false;
    if ($('local-download').checked) $('export-download').click();
    $('retry-save').hidden = false;
    await saveResult();
  } catch(error) {
    message(error.name==='AbortError' || controller.signal.aborted ? 'Operación cancelada. Si el MP4 ya se exportó, puedes descargarlo o reintentar guardarlo.' : error.message,true);
    progress($('video-progress').value,'La operación se detuvo');
  } finally {
    if(engine) {engine.terminate();engine=null;}
    for (const url of blobUrls.splice(0)) URL.revokeObjectURL(url);
    setBusy(false);
  }
};
$('retry-save').onclick = async () => {
  if(!result) return;
  controller = new AbortController(); setBusy(true);
  try {await saveResult();} catch(error){message(error.message,true);} finally{setBusy(false);}
};
$('cancel-export').onclick = () => { controller?.abort(); if(engine){engine.terminate();engine=null;} };
$('save-originals').onclick = async () => {
  controller = new AbortController(); setBusy(true);
  try {
    const id = clientId();
    if (!clips.length) throw new Error('Agrega al menos un clip.');
    for (const clip of clips.filter(c=>!c.saved)) {
      clip.originalMetadata ||= {titulo:clip.name,cliente_id:id,tipo:'original',duracion_seg:clip.duration};
      const saved = await upload(clip.blob,clip.originalMetadata,p=>progress(p,`Guardando ${clip.name}: ${Math.round(p)} %`),controller.signal,clip.uploadState);
      clip.saved=true;clip.parent=saved.id;
    }
    message('Clips originales guardados en Drive.');
  } catch(error){message(error.message,true);}finally{setBusy(false);}
};
window.addEventListener('beforeunload', event => {if(busy || clips.length){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide', () => {for(const c of clips)URL.revokeObjectURL(c.url);if(resultUrl)URL.revokeObjectURL(resultUrl);});
async function initialize() {
  controller = new AbortController();setBusy(true);
  try {await api('token');$('drive-connect').hidden=true;if(source)await addDrive(source);}
  catch(error){message(error.message,true);}finally{releaseImportEngine();setBusy(false);}
}
initialize();

// Studio controls share the same clip state and export pipeline.
function timecode(seconds) {
  const value = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${(value % 60).toFixed(1).padStart(4, '0')}`;
}
function clipWidth(clip) { return Math.max(220, (clip.end - clip.start) / clip.speed * timelineScale); }
function projectTime() {
  let elapsed = 0;
  for (const clip of clips) {
    if (clip === selected) return elapsed + Math.max(0, ($('preview').currentTime - clip.start) / clip.speed);
    elapsed += (clip.end - clip.start) / clip.speed;
  }
  return 0;
}
function updatePlayhead() {
  let left = 0;
  for (const clip of clips) {
    if (clip === selected) {
      const fraction = Math.max(0, Math.min(1, ($('preview').currentTime - clip.start) / (clip.end - clip.start)));
      left += fraction * clipWidth(clip); break;
    }
    left += clipWidth(clip) + 4;
  }
  $('timeline-playhead').style.left = `${selected ? left : 0}px`;
  $('preview-time').textContent = timecode(projectTime());
  updateOverlay();
}
function updateOverlay() {
  const overlay = $('preview-overlay');
  overlay.textContent = $('overlay-text').value;
  const position = $('text-position').value;
  overlay.style.top = position === 'top' ? '8%' : position === 'center' ? '50%' : 'auto';
  overlay.style.bottom = position === 'bottom' ? '8%' : 'auto';
  overlay.style.transform = position === 'center' ? 'translateY(-50%)' : '';
  overlay.style.color = $('text-color').value;
  const width = Number($('output-format').value.split(':')[0]);
  overlay.style.fontSize = `${Number($('text-size').value) / width * 100}cqw`;
  const t = projectTime();
  overlay.hidden = !selected || t < Number($('text-start').value) || t > Number($('text-end').value);
}
function fitCanvas() {
  const stage = document.querySelector('.studio-stage');
  const [w, h] = $('output-format').value.split(':').map(Number);
  const style = getComputedStyle(stage);
  const availableWidth = stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const availableHeight = stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  const width = Math.max(1, Math.min(availableWidth, availableHeight * w / h));
  $('preview-canvas').style.width = `${width}px`;
  $('preview-canvas').style.height = `${width * h / w}px`;
  $('preview-canvas').style.aspectRatio = `${w} / ${h}`;
}
function updateStudio() {
  $('media-count').textContent = clips.length;
  $('clip-count').textContent = `${clips.length} ${clips.length === 1 ? 'clip' : 'clips'}`;
  $('media-size').textContent = `${(clips.reduce((s, c) => s + c.blob.size, 0) / 1048576).toFixed(1)} MB`;
  $('player-duration').textContent = timecode(totalDuration());
  $('timeline-empty').hidden = clips.length > 0;
  $('preview-empty').hidden = selected !== null;
  $('preview-play').disabled = !selected || busy;
  $('previous-clip').disabled = !selected || busy;
  $('next-clip').disabled = !selected || busy;
  const bin = $('media-bin');
  if (clips.length) {
    bin.replaceChildren();
    for (const clip of clips) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = `studio-media-item${selected === clip ? ' is-selected' : ''}`;
      button.disabled = busy;
      const thumb = document.createElement('div'); thumb.className = 'studio-media-thumb';
      if (clip.thumbnail) { const img = new Image(); img.src = clip.thumbnail; img.alt = ''; thumb.append(img); }
      else { const icon = document.createElement('i'); icon.className = 'ph ph-film-strip'; thumb.append(icon); }
      const duration = document.createElement('span'); duration.textContent = timecode(clip.duration); thumb.append(duration);
      const name = document.createElement('span'); name.textContent = clip.name;
      button.title = clip.name; button.append(thumb, name);
      button.onclick = () => { selectClip(clip); renderTimeline(); };
      bin.append(button);
    }
  } else {
    bin.innerHTML = '<div class="studio-media-empty"><i class="ph ph-film-slate"></i><p>Un espacio para tus ideas.</p><span>Los clips que importes aparecerán aquí.</span></div>';
  }
  const ruler = $('timeline-ruler'); ruler.replaceChildren();
  let offset = 0, elapsed = 0;
  for (const clip of clips) {
    const duration = (clip.end - clip.start) / clip.speed;
    const count = Math.max(1, Math.floor(clipWidth(clip) / 85));
    for (let i = 0; i < count; i++) {
      const mark = document.createElement('span');
      mark.style.left = `${offset + i / count * clipWidth(clip)}px`;
      mark.textContent = timecode(elapsed + i / count * duration);
      ruler.append(mark);
    }
    offset += clipWidth(clip) + 4; elapsed += duration;
  }
  if (!clips.length) for (let i = 0; i < 10; i++) {
    const mark = document.createElement('span'); mark.style.left = `${i * 90}px`; mark.textContent = timecode(i * 5); ruler.append(mark);
  }
  $('timeline-content').style.width = `${Math.max(offset, $('timeline-scroll').clientWidth)}px`;
  updateTracks(); updatePlayhead();
}
function updateTracks() {
  const text = $('overlay-text').value.trim();
  $('text-track').textContent = text ? `T  ${text}` : '+ Agregar texto';
  $('text-track').classList.toggle('has-content', Boolean(text));
  const total = totalDuration();
  const width = Math.max(0, clips.reduce((n, c) => n + clipWidth(c) + 4, 0) - 4);
  const start = Math.max(0, Math.min(total, Number($('text-start').value)));
  const end = Math.max(start, Math.min(total, Number($('text-end').value)));
  $('text-track').style.marginLeft = text && total ? `${start / total * width}px` : '0';
  $('text-track').style.width = text && total ? `${Math.max(50, (end - start) / total * width)}px` : '160px';
  $('text-track').style.minWidth = text ? '50px' : '160px';
  const music = $('music-file').files[0];
  $('audio-track').textContent = music ? `♫  ${music.name}` : '+ Agregar música';
  $('audio-track').classList.toggle('has-content', Boolean(music));
  $('audio-track').style.width = music ? `${Math.max(160, width)}px` : '160px';
  $('music-name').textContent = music?.name || 'Elige un archivo de audio';
}
function showInspector(section) {
  document.querySelectorAll('[data-section]').forEach(panel => { panel.hidden = panel.dataset.section !== section; });
  document.querySelectorAll('[data-inspector]').forEach(button => {
    const active = button.dataset.inspector === section;
    button.classList.toggle('is-active', active); button.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('[data-tool]').forEach(button => button.classList.toggle('is-active', button.dataset.tool === section));
}
function createThumbnail(clip) {
  const video = document.createElement('video'); video.muted = true; video.preload = 'auto';
  const cleanup = () => { video.removeAttribute('src'); video.load(); };
  video.onloadeddata = () => {
    try {
      const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 90;
      const ctx = canvas.getContext('2d');
      const ratio = Math.max(160 / video.videoWidth, 90 / video.videoHeight);
      const w = video.videoWidth * ratio, h = video.videoHeight * ratio;
      ctx.drawImage(video, (160 - w) / 2, (90 - h) / 2, w, h);
      clip.thumbnail = canvas.toDataURL('image/jpeg', 0.6);
      if (!busy) renderTimeline();
    } catch {} finally { cleanup(); }
  };
  video.onerror = cleanup; video.src = clip.url;
}
$('stage-import').onclick = () => $('clip-files').click();
$('preview-play').onclick = () => {
  if (!selected || busy) return;
  if ($('preview').paused) $('preview').play().catch(() => message('No se pudo reproducir este clip.', true));
  else $('preview').pause();
};
function stepClip(delta) {
  const index = clips.indexOf(selected) + delta;
  if (busy || index < 0 || index >= clips.length) return;
  selectClip(clips[index]); renderTimeline();
}
$('previous-clip').onclick = () => stepClip(-1);
$('next-clip').onclick = () => stepClip(1);
$('preview-fullscreen').onclick = () => {
  $('preview-canvas').requestFullscreen?.().catch(() => message('No se pudo abrir la pantalla completa.', true));
};
$('preview').addEventListener('timeupdate', updatePlayhead);
$('preview').addEventListener('loadedmetadata', updatePlayhead);
for (const event of ['play', 'pause', 'ended']) $('preview').addEventListener(event, () => {
  $('preview-play').innerHTML = `<i class="ph ph-${$('preview').paused ? 'play' : 'pause'}"></i>`;
  $('preview-play').setAttribute('aria-label', $('preview').paused ? 'Reproducir' : 'Pausar');
});
for (const button of document.querySelectorAll('[data-inspector]')) button.onclick = () => showInspector(button.dataset.inspector);
for (const button of document.querySelectorAll('[data-tool]')) button.onclick = () => {
  if (button.dataset.tool === 'media') {
    document.querySelectorAll('[data-tool]').forEach(item => item.classList.toggle('is-active', item === button));
    document.querySelector('.studio-media').scrollIntoView({behavior:'smooth',block:'nearest'});
  } else {
    showInspector(button.dataset.tool);
    if (matchMedia('(max-width:550px)').matches) document.querySelector('.studio-inspector').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
};
function updateFormat() {
  const [w, h] = $('output-format').value.split(':');
  $('preview-format').textContent = {'1080:1920':'9:16','1080:1080':'1:1','1920:1080':'16:9'}[$('output-format').value];
  $('footer-resolution').textContent = `${w} × ${h}`;
  $('preview').style.objectFit = $('output-fit').value === 'crop' ? 'cover' : 'contain';
  document.querySelectorAll('[data-ratio]').forEach(b => b.classList.toggle('is-active', b.dataset.ratio === $('output-format').value));
  fitCanvas(); updateOverlay();
}
for (const button of document.querySelectorAll('[data-ratio]')) button.onclick = () => { $('output-format').value = button.dataset.ratio; updateFormat(); };
$('output-format').addEventListener('change', updateFormat);
$('output-fit').addEventListener('change', updateFormat);
for (const id of ['overlay-text', 'text-position', 'text-size', 'text-color', 'text-start', 'text-end']) {
  $(id).addEventListener('input', () => { updateOverlay(); updateTracks(); });
}
$('music-file').addEventListener('change', updateTracks);
for (const id of ['original-volume', 'music-volume']) $(id).addEventListener('input', () => {
  $(`${id}-label`).textContent = `${Math.round(Number($(id).value) * 100)} %`;
  if (id === 'original-volume') { $('preview').volume = Math.min(1, Number($(id).value)); }
});
$('text-track').onclick = () => { showInspector('text'); $('overlay-text').focus(); };
$('audio-track').onclick = () => { showInspector('audio'); $('music-file').click(); };
$('timeline-zoom').oninput = () => { timelineScale = Number($('timeline-zoom').value); renderTimeline(); };
for (const [id, delta] of [['timeline-zoom-out', -10], ['timeline-zoom-in', 10]]) $(id).onclick = () => {
  $('timeline-zoom').value = Math.max(20, Math.min(100, timelineScale + delta));
  timelineScale = Number($('timeline-zoom').value); renderTimeline();
};
$('timeline-ruler').onclick = event => {
  if (!clips.length || busy) return;
  let x = event.clientX - $('timeline-ruler').getBoundingClientRect().left;
  for (const clip of clips) {
    const width = clipWidth(clip);
    if (x <= width || clip === clips.at(-1)) {
      selectClip(clip); $('preview').currentTime = clip.start + Math.min(1, Math.max(0, x / width)) * (clip.end - clip.start);
      renderTimeline(); return;
    }
    x -= width + 4;
  }
};
const zone = $('import-zone');
zone.addEventListener('dragenter', event => {
  if (!isFileDrag(event.dataTransfer)) return;
  event.preventDefault();
  if (!busy) zone.classList.add('is-dragover');
});
zone.addEventListener('dragover', event => {
  if (!isFileDrag(event.dataTransfer)) return;
  event.preventDefault(); event.stopPropagation();
  event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
  if (!busy) zone.classList.add('is-dragover');
});
zone.addEventListener('dragleave', event => {
  if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-dragover');
});
zone.addEventListener('drop', async event => {
  event.preventDefault(); event.stopPropagation(); zone.classList.remove('is-dragover');
  // Read files synchronously: browsers clear the transfer after this event.
  await importClips(droppedFiles(event.dataTransfer));
});
window.addEventListener('dragover', event => {
  if (isFileDrag(event.dataTransfer)) event.preventDefault();
});
window.addEventListener('drop', event => {
  if (!isFileDrag(event.dataTransfer)) return;
  event.preventDefault(); zone.classList.remove('is-dragover');
  message('Suelta los videos en «Importar archivos» para agregarlos a la línea de tiempo.');
});
new ResizeObserver(fitCanvas).observe(document.querySelector('.studio-stage'));
updateStudio(); updateFormat();
