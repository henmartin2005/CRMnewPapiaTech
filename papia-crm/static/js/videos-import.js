// Some desktop file managers omit the MIME type when dragging a video.
const videoTypes = {
  mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime',
  webm: 'video/webm', mkv: 'video/x-matroska', avi: 'video/x-msvideo',
  mpg: 'video/mpeg', mpeg: 'video/mpeg', ogv: 'video/ogg',
  mts: 'video/mp2t', m2ts: 'video/mp2t', '3gp': 'video/3gpp',
};

export function videoFile(file) {
  if (file.type.startsWith('video/')) return file;
  const type = videoTypes[file.name.split('.').pop().toLowerCase()];
  if (!type || !['', 'application/octet-stream', 'application/x-matroska'].includes(file.type)) return null;
  return new File([file], file.name, {type, lastModified: file.lastModified});
}

export function droppedFiles(transfer) {
  const files = Array.from(transfer?.files || []);
  if (files.length) return files;
  return Array.from(transfer?.items || []).filter(item => item.kind === 'file')
    .map(item => item.getAsFile()).filter(Boolean);
}

export function isFileDrag(transfer) {
  return Array.from(transfer?.types || []).includes('Files');
}
