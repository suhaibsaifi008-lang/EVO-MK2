"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const log = $("log"), scrollArea = $("scrollArea");
let currentController = null, currentAudio = null;

function mdLite(src) {
  let out = "", inCode = false, buf = [];
  const inline = t => esc(t).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  for (const raw of String(src||"").split(/\r?\n/)) {
    if (/^```/.test(raw)) { if (inCode) { out+="<pre><code>"+esc(buf.join("\n"))+"</code></pre>"; buf=[]; } inCode=!inCode; continue; }
    if (inCode) { buf.push(raw); continue; }
    const ul = raw.match(/^\s*[-*]\s+(.*)/), li = raw.match(/^\s*\d+[.)]\s+(.*)/);
    if (ul) { out += "<li>"+inline(ul[1])+"</li>"; continue; }
    if (li) { out += "<li>"+inline(li[2])+"</li>"; continue; }
    if (!raw.trim()) continue;
    out += "<p>"+inline(raw.trim())+"</p>";
  }
  if (buf.length) out += "<pre><code>"+esc(buf.join("\n"))+"</code></pre>";
  return out || "";
}

function toast(text) {
  const box = $("toasts"); const t = document.createElement("div");
  t.className = "toast"; t.textContent = text; box.appendChild(t);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(() => t.remove(), 8000);
}

/* face engine */
const faceCv = document.getElementById("faceCanvas");
if (faceCv) {
  const fx = faceCv.getContext("2d");
  let FW, FH, ft = 0, _state = "idle", speakLevel = 0;
  window.faceState = s => { _state = s; };
  const parts = []; for (let i=0;i<80;i++) parts.push({a:Math.random()*6.28,r:.55+Math.random()*.45,sp:.002+Math.random()*.004,s:1+Math.random()*2});
  const traces = []; for (let i=0;i<20;i++) traces.push({x:Math.random(),y:Math.random(),l:.08+Math.random()*.18,sp:.003+Math.random()*.008,d:Math.random()<.5?-1:1,v:Math.random()});
  function rs(){FW=faceCv.width=innerWidth;FH=faceCv.height=innerHeight} addEventListener("resize",rs);rs();
  function draw(){
    ft+=1/60; fx.clearRect(0,0,FW,FH);
    const cxp=FW/2,cyp=FH*.32,R0=Math.min(FW,FH)*.12;
    let R=R0,g=.3;
    if(_state==="listening"){R=R0*1.06+.03*Math.sin(ft*3);g=.55}
    else if(_state==="thinking"){R=R0*(1+.04*Math.sin(ft*8));g=.45}
    else if(_state==="speaking"){speakLevel+=((.5+.5*Math.sin(ft*10))*(.4+.6*Math.random())-speakLevel)*.35;R=R0*(1+speakLevel*.2);g=.7+.25*speakLevel}
    else{R=R0*(1+.02*Math.sin(ft));g=.28+.07*Math.sin(ft*.7)}
    const gr=fx.createRadialGradient(cxp,cyp,R*.15,cxp,cyp,R*1.2);
    gr.addColorStop(0,`rgba(170,210,255,${.7*g+.1})`);gr.addColorStop(.55,`rgba(110,168,254,${.4*g})`);gr.addColorStop(1,"transparent");
    fx.beginPath();fx.arc(cxp,cyp,R*1.2,0,7);fx.fillStyle=gr;fx.fill();
    for(const[rr,lw,a]of[[1,2,.85],[1.2,1,.5],[1.45,1,.25]]){fx.beginPath();fx.arc(cxp,cyp,R*rr,ft*speed(),ft*speed()+Math.PI*1.5);fx.strokeStyle=`rgba(140,190,255,${a})`;fx.lineWidth=lw;fx.stroke()}
    for(const p of parts){p.a+=p.sp*speed();const rr=R*p.r*1.5;fx.beginPath();fx.arc(cxp+Math.cos(p.a)*rr,cyp+Math.sin(p.a)*rr,p.s,0,7);fx.fillStyle=`rgba(150,200,255,${.2+.35*Math.abs(Math.sin(p.a*2))}`+")";fx.fill()}
    for(const tr of traces){tr.v+=tr.sp*speed();if(tr.v>1)tr.v=0;const x0=tr.x*FW,y0=tr.y*FH,x1=x0+tr.l*FW*tr.d;fx.strokeStyle=`rgba(90,140,220,${.06+.18*tr.v})`;fx.lineWidth=1;fx.strokeRect(Math.min(x0,x1),y0,Math.abs(x1-x0),2)}
    function speed(){return _state==="thinking"?2.5:_state==="speaking"?2:_state==="listening"?1.2:.6}
    requestAnimationFrame(draw);
  }
  draw();
}

/* messages */
function addMsg(text, who, opts) {
  opts = opts || {};
  $("hero").style.display = "none";
  const div = document.createElement("div");
  div.className = `msg ${who}${opts.highlight ? " highlight" : ""}`;
  const body = document.createElement("div");
  body.className = "msg-body"; body.dataset.raw = text || "";
  setBody(body, text, who);
  const meta = document.createElement("div"); meta.className = "meta";
  meta.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  div.append(body);
  if (who === "evo") {
    const act = document.createElement("div"); act.className = "msg-actions";
    const cp = document.createElement("button"); cp.className = "mini-act"; cp.textContent = "⧉ Copy";
    cp.onclick = () => { navigator.clipboard.writeText(div.dataset.raw || text); toast("Copied."); };
    act.appendChild(cp); div.appendChild(act);
  }
  div.appendChild(meta); log.appendChild(div); scrollArea.scrollTop = scrollArea.scrollHeight;
  return div;
}
function setBody(el, text, who) { el.dataset.raw = text; el.innerHTML = who === "evo" ? mdLite(text) : esc(text); }

/* speech engine: sentence-level queue w/ prefetch.
   - speakBegin()   start a fresh speech session (cancels previous)
   - speakFeed(s)   append streamed text; complete sentences get queued
   - speakFlush()   queue whatever tail remains (end of reply)
   - speakAll(text) convenience: begin + enqueue whole text
   Chunks prefetch in parallel so there is zero dead air between them. */
const tts = {
  token: 0,
  q: [],
  buf: "",
  busy: false,
  cache: new Map(),
};
function ttsSplit(text) {
  const sentences = String(text || "").split(/(?<=[.!?])\s+/).filter(s => s.trim());
  const parts = [];
  let buf = "";
  for (const s of sentences) {
    if ((buf + " " + s).trim().length > 280) { if (buf.trim()) parts.push(buf.trim()); buf = s; }
    else buf = (buf ? buf + " " : "") + s;
  }
  if (buf.trim()) parts.push(buf.trim());
  return parts;
}
function ttsFetchBlob(part) {
  const key = part.slice(0, 400);
  if (!tts.cache.has(key)) {
    if (tts.cache.size > 50) tts.cache.clear();
    tts.cache.set(key, fetch(`/api/tts?text=${encodeURIComponent(key)}`)
      .then(r => r.ok ? r.blob() : null).catch(() => null));
  }
  return tts.cache.get(key);
}
function ttsEnqueue(parts) {
  tts.q.push(...parts);
  ttsPump();
}
function ttsPump() {
  if (tts.busy) return;
  const part = tts.q.shift();
  if (!part) { if (!currentAudio) faceState("idle"); return; }
  tts.busy = true;
  faceState("speaking");
  if (tts.q[0]) ttsFetchBlob(tts.q[0]);        // prefetch next chunk
  const myToken = tts.token;
  ttsFetchBlob(part).then(blob => {
    if (myToken !== tts.token) return;
    if (!blob) { tts.busy = false; return ttsPump(); }
    const a = new Audio(URL.createObjectURL(blob));
    currentAudio = a;
    a.onended = () => { tts.busy = false; ttsPump(); };
    a.onerror = () => { tts.busy = false; ttsPump(); };
    a.play().catch(() => { tts.busy = false; ttsPump(); });
  }).catch(() => { tts.busy = false; ttsPump(); });
}
function ttsCancel() {
  tts.token++;
  tts.q = []; tts.buf = ""; tts.busy = false;
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
}
function ttsBegin() {
  tts.token++;                       // invalidate any in-flight chain
  tts.q = []; tts.buf = ""; tts.busy = false;
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
}
function ttsFeed(deltaText) {
  if (!$("voiceOut").checked) return;
  tts.buf += deltaText;
  let m;
  while ((m = tts.buf.match(/^[^.!?]*[.!?](\s+|$)/))) {
    const sent = tts.buf.slice(0, m[0].length).trim();
    tts.buf = tts.buf.slice(m[0].length);
    if (sent) ttsEnqueue([sent]);
  }
}
function ttsFlush() {
  if ($("voiceOut").checked && tts.buf.trim()) {
    ttsEnqueue(ttsSplit(tts.buf));
  }
  tts.buf = "";
}
function speakAll(text) {
  ttsBegin();
  if ($("voiceOut").checked) ttsEnqueue(ttsSplit(text));
  else faceState("idle");
}

async function sendStreaming(text) {
  text = (text || "").trim(); if (!text) return;
  addMsg(text, "user");
  const bubble = addMsg("", "evo"); bubble.classList.add("streaming");
  const body = bubble.querySelector(".msg-body");
  body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  let acc = "", done = false;
  currentController = new AbortController();
  ttsBegin();                                    // cancel any speech chain
  const sb = $("stopBtn"); sb.classList.remove("hidden");
  try {
    const res = await fetch("/api/chat/stream", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      signal: currentController.signal });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done: d, value } = await reader.read(); if (d) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(line.slice(5)); } catch { continue; }
        if (ev.type === "thinking" && !acc) body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
        else if (ev.type === "reset") { acc = ""; body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>'; }
        else if (ev.type === "progress") { const c = document.createElement("div"); c.className = "tool-chip"; c.innerHTML = `<b>⚙</b><i>${esc(ev.text)}</i>`; bubble.insertBefore(c, body); }
        else if (ev.type === "tool") { const c = document.createElement("div"); c.className = "tool-chip"; c.innerHTML = `<b>⚙ ${esc(ev.name)}</b><i>${esc(ev.brief)}</i>`; bubble.insertBefore(c, body); }
        else if (ev.type === "delta") {
          acc += ev.text; setBody(body, acc, "evo");
          if ($("voiceOut").checked) ttsFeed(ev.text);   // speak sentences as they complete
          scrollArea.scrollTop = scrollArea.scrollHeight;
        }
        else if (ev.type === "done") { done = true; acc = ev.text || acc; setBody(body, acc, "evo"); }
        else if (ev.type === "error" && ev.text !== "cancelled") toast(ev.text);
      }
    }
    if (!done) setBody(body, acc || "(no response)", "evo");
    // TTS — flush the unspoken tail (sentences already queued live)
    if ($("voiceOut").checked && acc) {
      ttsFlush();
      if (!tts.q.length && !tts.busy && !currentAudio) faceState("idle");
    } else faceState("idle");
  } catch (e) {
    if (!done && !acc) setBody(body, "Connection lost.", "evo");
    faceState("idle");
  } finally {
    bubble.classList.remove("streaming"); currentController = null; sb.classList.add("hidden");
  }
}

$("chatForm").addEventListener("submit", e => { e.preventDefault(); const v = $("chatInput").value.trim(); $("chatInput").value = ""; $("chatInput").style.height = "auto"; sendStreaming(v); });
$("chatInput").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); } });
$("chatInput").addEventListener("input", function () { this.style.height = "auto"; this.style.height = Math.min(this.scrollHeight, 180) + "px"; });
document.addEventListener("keydown", e => { if (e.key === "Escape" && currentController) { currentController.abort(); toast("Stopped."); } });
$("stopBtn")?.addEventListener("click", () => { if (currentController) { currentController.abort(); toast("Stopped."); } });

/* PTT */
let mediaStream, audioCtx, processor, sourceNode, pttRecording, speechSeen, quietFrames, collected;
const PTT_RATE = 16000;

async function pttStart() {
  if (pttRecording) return;
  if (currentController) currentController.abort();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  try { mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } }); }
  catch { toast("Microphone blocked."); return; }
  pttRecording = true; speechSeen = false; quietFrames = 0; collected = [];
  ttsCancel();                                   // stop speech, free the floor
  try { audioCtx = new AudioContext({ sampleRate: PTT_RATE }); } catch { audioCtx = new AudioContext(); }
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);
  processor = audioCtx.createScriptProcessor(2048, 1, 1);
  const rate = audioCtx.sampleRate;
  processor.onaudioprocess = ev => {
    if (!pttRecording) return;
    const input = ev.inputBuffer.getChannelData(0);
    let peak = 0; for (let i = 0; i < input.length; i++) { const v = Math.abs(input[i]); if (v > peak) peak = v; }
    if (peak > .06) { speechSeen = true; quietFrames = 0; } else if (speechSeen) quietFrames++;
    for (let i = 0; i < input.length; i++) collected.push(input[i]);
    const frameMs = input.length / rate * 1000, elapsed = collected.length / rate * 1000;
    if ((speechSeen && quietFrames * frameMs > 1000) || elapsed > 9000) pttFinish();
  };
  sourceNode.connect(processor); processor.connect(audioCtx.destination);
  $("pttBtn").classList.add("on"); faceState("listening");
  $("sttPreview").textContent = "Listening… speak now";
}

async function pttFinish() {
  if (!pttRecording) return;
  pttRecording = false; $("pttBtn").classList.remove("on");
  try { processor.disconnect(); sourceNode.disconnect(); } catch {}
  try { mediaStream.getTracks().forEach(t => t.stop()); } catch {}
  const rate = audioCtx ? audioCtx.sampleRate : 16000;
  const samples = new Int16Array(collected.length);
  for (let i = 0; i < collected.length; i++) { const s = Math.max(-1, Math.min(1, collected[i])); samples[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
  collected = []; try { await audioCtx.close(); } catch {} audioCtx = null;
  if (!speechSeen || samples.length < rate / 2) { $("sttPreview").textContent = ""; toast("Didn't hear anything."); return; }
  $("sttPreview").textContent = "Transcribing…"; faceState("thinking");
  try {
    const wav = encodeWav(samples, rate);
    const res = await fetch("/api/transcribe", { method: "POST", body: wav });
    if (!res.ok) {
      let why = "";
      try {
        const j = await res.json();
        const d = j.detail ?? j.message ?? "";
        if (typeof d === "string") why = d;
        else if (Array.isArray(d)) why = d.map(e => e.msg || JSON.stringify(e)).join("; ");
        else if (d) why = JSON.stringify(d);
      } catch {}
      throw new Error(`${res.status} ${why}`.trim());
    }
    const data = await res.json(); $("sttPreview").textContent = "";
    const text = (data.text || "").trim();
    if (!text) { toast("Didn't catch that."); faceState("idle"); return; }
    sendStreaming(text);
  } catch (e) {
    $("sttPreview").textContent = ""; faceState("idle");
    const msg = e && e.message ? e.message.replace(/\[object Object\]/g, "").trim() : "";
    toast(`Transcription failed${msg ? ` (${msg})` : ""}. If it says "query/request", restart EVO - the server is running old code.`);
  }
}

function encodeWav(int16, rate) {
  const buf = new ArrayBuffer(44 + int16.length * 2); const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); v.setUint32(4, 36 + int16.length * 2, true); ws(8, "WAVE");
  ws(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, "data"); v.setUint32(40, int16.length * 2, true);
  for (let i = 0; i < int16.length; i++) v.setInt16(44 + i * 2, int16[i], true);
  return new Blob([buf], { type: "audio/wav" });
}

$("pttBtn").addEventListener("click", () => { if (pttRecording) pttFinish(); else pttStart(); });

async function boot() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("engineTag").textContent = `${h.voice.split(":")[0]} · ${h.llm_online === true ? "online" : h.llm_online === false ? "offline" : "checking"}`;
    $("conn").className = "dot online";
    const hr = new Date().getHours(), part = hr < 12 ? "morning" : hr < 18 ? "afternoon" : "evening";
    $("greet").textContent = `Good ${part}. EVO MK2 online.`;
  } catch { $("conn").className = "dot offline"; }
}
boot(); setInterval(boot, 30000);

// auto-grow textarea
$("chatInput").addEventListener("input", function () { this.style.height = "auto"; this.style.height = Math.min(this.scrollHeight, 180) + "px"; });


/* ================= EVENT BUS LISTENER =================
   Receives proactive events from the server: reminders firing,
   research completing, watcher alerts, etc. Without this, all
   those things happen server-side and are INVISIBLE to you. */
const es = new EventSource("/api/events");
const _lastProg = {};
es.onmessage = (m) => {
  try {
    const ev = JSON.parse(m.data);
    if (ev.type === "notify.out") {
      const kind = ev.payload?.kind || "info";
      const text = ev.payload?.text || "";
      // Show as a prominent chat message so it can't be missed
      addMsg(text, "evo", { highlight: true });
      toast(text);
      // Speak it aloud too — sentence-chained
      speakAll(text.slice(0, 1200));
    }
    if (ev.type === "job.progress") {
      const { id, goal, step, max_steps } = ev.payload || {};
      const now = Date.now();
      if (!_lastProg[id] || now - _lastProg[id] > 10000) {   // throttle
        _lastProg[id] = now;
        toast(`⚙ Mission #${id} · step ${step}/${max_steps}: ${goal}`);
      }
    }
    if (ev.type === "system.voice") {
      faceState(ev.payload?.state === "session" ? "listening" : "idle");
    }
  } catch {}
};
es.onerror = () => { /* auto-reconnects */ };

/* New Chat button */
const ncBtn = document.createElement("button");
ncBtn.className = "new-chat-btn";
ncBtn.textContent = "+ New chat";
ncBtn.onclick = () => {
  log.innerHTML = "";
  const h = document.getElementById("hero");
  if (h) h.style.display = "";
  faceState("idle");
  toast("New conversation started.");
};
document.querySelector(".topbar").appendChild(ncBtn);
