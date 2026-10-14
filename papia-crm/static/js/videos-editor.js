import {api, upload, downloadDrive, message} from './videos-api.js';
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
  const url = URL.createObjectURL(blob);
  const probe = document.createElement('video');
  probe.preload = 'metadata';
  try {
    const duration = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('No se pudo leer la duración del clip.')), 20000);
      probe.onloadedmetadata = () => { clearTimeout(timer); resolve(probe.duration); };
      probe.onerror = () => { clearTimeout(timer); reject(new Error('El navegador no puede leer este formato de video.')); };
      probe.src = url;
    });
    if (!Number.isFinite(duration) || duration <= 0) throw new Error('La duración del clip no es válida.');
    const clip = {id: crypto.randomUUID(), blob, name, url, duration, start: 0, end: duration,
      speed: 1, parent, uploadState: {}, saved: Boolean(parent)};
    clips.push(clip);
    selectClip(clip);
    renderTimeline();
  } catch (error) { URL.revokeObjectURL(url); throw error; }
  finally { probe.removeAttribute('src'); probe.load(); }
}

function selectClip(clip) {
  selected = clip;
  $('preview').src = clip.url;
  $('preview').currentTime = clip.start;
  $('preview').playbackRate = clip.speed;
}

function totalDuration() { return clips.reduce((sum, c) => sum + (c.end - c.start) / c.speed, 0); }
function renderTimeline() {
  $('timeline').replaceChildren();
  clips.forEach((clip, index) => {
    const li = document.createElement('li');
    li.className = `videos-clip ${selected === clip ? 'is-selected' : ''}`;
    li.draggable = true;
    li.dataset.id = clip.id;
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
        $('total-time').textContent = `${totalDuration().toFixed(1)} s`;
      };
      label.append(input); controls.append(label);
    }
    const label = document.createElement('label'); label.textContent = 'Velocidad';
    const speed = document.createElement('select');
    [0.5, 1, 1.5, 2].forEach(value => { const option = new Option(`${value}x`, value); option.selected = value === clip.speed; speed.add(option); });
    speed.onchange = () => { clip.speed = Number(speed.value); if (selected === clip) $('preview').playbackRate = clip.speed; $('total-time').textContent = `${totalDuration().toFixed(1)} s`; };
    label.append(speed); controls.append(label); li.append(title, controls);
    li.ondragstart = event => { if (busy) {event.preventDefault(); return;} dragged = index; event.dataTransfer.setData('text/plain', clip.id); };
    li.ondragover = event => event.preventDefault();
    li.ondrop = event => { event.preventDefault(); if (!busy && dragged !== null) move(dragged, index); dragged = null; };
    $('timeline').append(li);
  });
  $('total-time').textContent = `${totalDuration().toFixed(1)} s`;
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
  await addClip(blob, item.titulo, item.id);
  message('Clip añadido a la línea de tiempo.');
}

$('clip-files').onchange = async event => {
  const files = [...event.target.files]; setBusy(true);
  try { for (const file of files) if (warnSize(file)) await addClip(file, file.name); }
  catch (error) { message(error.message, true); }
  finally { setBusy(false); event.target.value = ''; }
};
$('add-drive').onclick = async () => {
  controller = new AbortController(); setBusy(true);
  try { await addDrive(library.find(v => v.id === Number($('drive-source').value))); }
  catch (error) { message(error.message, true); }
  finally { setBusy(false); }
};

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
    const ext = (clip.name.split('.').pop() || 'mp4').replace(/[^a-zA-Z0-9]/g,'').slice(0,8) || 'mp4';
    const input = `input${i}.${ext}`;
    const duration = (clip.end-clip.start)/clip.speed;
    await engine.writeFile(input, new Uint8Array(await clip.blob.arrayBuffer()));
    // The 0.12.10 core can return -1 even after writing valid ffprobe JSON.
    // Validate the actual output, and never reuse a preceding clip's report.
    try { await engine.deleteFile('probe.json'); } catch {}
    await engine.ffprobe(['-v','error','-show_streams','-of','json',input,'-o','probe.json']);
    const info = JSON.parse(new TextDecoder().decode(await engine.readFile('probe.json')));
    if (!Array.isArray(info.streams) || !info.streams.some(s => s.codec_type === 'video')) throw new Error('No se pudo analizar el clip.');
    const hasAudio = info.streams.some(s => s.codec_type === 'audio');
    phase = {base: elapsed/total*65, weight: duration/total*65, label: `Procesando clip ${i+1} de ${clips.length}…`};
    await execute(['-ss',String(clip.start),'-t',String(clip.end-clip.start),'-i',input,
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
  catch(error){message(error.message,true);}finally{setBusy(false);}
}
initialize();
