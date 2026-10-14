// node --test tests/test_videos_import.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {videoFile, droppedFiles, isFileDrag} from '../static/js/videos-import.js';

test('desktop MOV drops with no MIME type preserve bytes and filename', async () => {
  const original = new File(['clip bytes'], 'Mi clip.MOV', {lastModified: 123});
  const video = videoFile(original);
  assert.equal(video.type, 'video/quicktime');
  assert.equal(video.name, original.name);
  assert.equal(video.lastModified, 123);
  assert.equal(await video.text(), await original.text());
});

test('generic binary MP4 files can be imported as videos', () => {
  const video = videoFile(new File(['bytes'], 'clip.mp4', {type:'application/octet-stream'}));
  assert.equal(video.type, 'video/mp4');
});

test('typed video files retain their original object', () => {
  const file = new File(['bytes'], 'video.webm', {type:'video/webm'});
  assert.equal(videoFile(file), file);
});

test('documents, audio files and URLs are not imported as videos', () => {
  assert.equal(videoFile(new File(['text'], 'document.txt')), null);
  assert.equal(videoFile(new File(['sound'], 'sound.mp4', {type:'audio/mp4'})), null);
  assert.equal(isFileDrag({types:['text/plain','text/uri-list']}), false);
});

test('drops retain multiple files and recover files from transfer items', () => {
  const a = new File(['a'], 'a.mp4'), b = new File(['b'], 'b.mov');
  assert.deepEqual(droppedFiles({files:[a,b]}), [a,b]);
  assert.deepEqual(droppedFiles({files:[],items:[
    {kind:'string'}, {kind:'file',getAsFile:()=>a},
    {kind:'file',getAsFile:()=>null}, {kind:'file',getAsFile:()=>b},
  ]}), [a,b]);
  assert.equal(isFileDrag({types:['Files']}), true);
});
