// node --test tests/test_videos_upload.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {upload} from '../static/js/videos-api.js';

globalThis.document = {querySelector:()=>({dataset:{csrf:'csrf'}}),getElementById:()=>null};
const originalTimeout = globalThis.setTimeout;

function mockNetwork(steps, registerFails=false) {
  const requests=[];
  let sessions=0, registrations=0;
  globalThis.fetch=async (url,options) => {
    if (url.endsWith('upload-session')) {
      sessions++;
      return {ok:true,json:async()=>({upload_url:'https://www.googleapis.com/upload/test',upload_id:'nonce'})};
    }
    registrations++;
    if (registerFails && registrations===1) throw new Error('Disconnected before registration');
    return {ok:true,json:async()=>({id:123})};
  };
  globalThis.XMLHttpRequest=class {
    upload={};headers={};
    open(method,url){this.url=url;}
    setRequestHeader(key,value){this.headers[key]=value;}
    getResponseHeader(){return this.range||null;}
    send(blob){
      requests.push({headers:this.headers,size:blob.size});
      const next=steps.shift();
      assert.ok(next,'unexpected upload request');
      queueMicrotask(()=>{
        if(next.error){this.onerror();return;}
        this.status=next.status;this.range=next.range;this.responseText=JSON.stringify(next.body||{});
        this.upload.onprogress?.({loaded:blob.size});this.onload();
      });
    }
    abort(){this.onabort();}
  };
  globalThis.setTimeout=(callback)=>originalTimeout(callback,0);
  return {requests,get sessions(){return sessions;},get registrations(){return registrations;}};
}

test('8 MB chunks use acknowledged ranges and preserve final metadata',async()=>{
  const size=17*1024*1024;
  const net=mockNetwork([{status:308,range:'bytes=0-8388607'},{status:308,range:'bytes=0-16777215'},{status:200,body:{id:'drive-file'}}]);
  const state={};const progress=[];
  const result=await upload(new Blob([new Uint8Array(size)],{type:'video/mp4'}),{titulo:'Test',cliente_id:1},p=>progress.push(p),undefined,state);
  assert.equal(result.id,123);
  assert.deepEqual(net.requests.map(r=>r.size),[8388608,8388608,1048576]);
  assert.equal(net.requests[2].headers['Content-Range'],`bytes 16777216-${size-1}/${size}`);
  assert.equal(progress.at(-1),100);assert.equal(state.fileId,'drive-file');
});

test('lost last response probes status instead of uploading a second file',async()=>{
  const net=mockNetwork([{error:true},{status:200,body:{id:'completed-file'}}]);
  const result=await upload(new Blob(['video']),{titulo:'Test'},()=>{},undefined,{});
  assert.equal(result.id,123);assert.equal(net.sessions,1);
  assert.equal(net.requests[1].headers['Content-Range'],'bytes */5');
});

test('failed registration retries without resending video bytes',async()=>{
  const net=mockNetwork([{status:200,body:{id:'completed-file'}}],true);
  const state={};const blob=new Blob(['video']);
  await assert.rejects(upload(blob,{titulo:'Test'},()=>{},undefined,state),/registration/);
  const result=await upload(blob,{titulo:'Test'},()=>{},undefined,state);
  assert.equal(result.id,123);assert.equal(net.requests.length,1);assert.equal(net.registrations,2);assert.equal(net.sessions,1);
});

test('network interruption resumes at the confirmed byte offset',async()=>{
  const size=9*1024*1024;
  const net=mockNetwork([{error:true},{status:308,range:'bytes=0-4194303'},{status:200,body:{id:'done'}}]);
  await upload(new Blob([new Uint8Array(size)]),{titulo:'Test'},()=>{},undefined,{});
  assert.equal(net.requests[2].headers['Content-Range'],`bytes 4194304-${size-1}/${size}`);
});

test('aborted upload sends no video bytes',async()=>{
  const net=mockNetwork([]);const controller=new AbortController();controller.abort();
  await assert.rejects(upload(new Blob(['video']),{titulo:'Test'},()=>{},controller.signal,{}),{name:'AbortError'});
  assert.equal(net.requests.length,0);
});
