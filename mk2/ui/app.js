"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const log = $("log"), scrollArea = $("scrollArea");
let currentController = null;

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

/* ============ VOICE PIPELINE v3 ============
   ONE WebSocket (/ws/voice) carries the whole turn: user text up, streamed
   reply text down, and - per completed sentence - a JSON audio header
   followed by raw audio bytes. The server synthesizes sentence N+1 while
   you are still listening to sentence N, so playback is gapless: every
   blob is already in memory before the previous Audio ends. No /api/tts
   round trips during replies; STT is browser-native (Web Speech API). */
const typingHTML = () => '<span class="typing"><i></i><i></i><i></i></span>';

/* gapless player */
const aq = { items: [], cur: null };
function aqPush(blob) {
  const a = new Audio(URL.createObjectURL(blob));
  aq.items.push(a);
  aqPump();
}
function aqPump() {
  if (aq.cur) return;
  const a = aq.items.shift();
  if (!a) { faceState("idle"); return; }
  aq.cur = a;
  faceState("speaking");
  const fin = () => {
    if (aq.cur === a) aq.cur = null;
    try { URL.revokeObjectURL(a.src); } catch {}
    if (!aq.cur && !aq.items.length) faceState("idle");
    else aqPump();                      // next one is already buffered
  };
  a.onended = fin; a.onerror = fin;
  a.play().catch(fin);
}
function aqCancel() {
  aq.items.forEach(a => { a.pause(); try { URL.revokeObjectURL(a.src); } catch {} });
  aq.items = [];
  if (aq.cur) { aq.cur.pause(); aq.cur = null; }
  faceState("idle");
}

/* transport */
let turnHandler = null;                 // routes server events to active bubble
const vws = { sock: null, queue: [] };
function vwsConnect(onReady) {
  if (vws.sock && vws.sock.readyState === WebSocket.OPEN) { if (onReady) onReady(); return; }
  if (onReady) vws.queue.push(onReady);
  if (vws.sock && vws.sock.readyState === WebSocket.CONNECTING) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const s = new WebSocket(`${proto}://${location.host}/ws/voice`);
  s.binaryType = "blob";
  vws.sock = s;
  s.onopen = () => vws.queue.splice(0).forEach(f => f());
  s.onmessage = m => {
    if (m.data instanceof Blob) { aqPush(m.data); return; }
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (turnHandler) turnHandler(ev);
  };
  s.onclose = () => { vws.sock = null; };
  s.onerror = () => {};
}
function wsSend(obj) {
  vwsConnect(() => { try { vws.sock.send(JSON.stringify(obj)); } catch {} });
}
function wsCancelTurn() {
  try { if (vws.sock && vws.sock.readyState === WebSocket.OPEN) vws.sock.send('{"type":"cancel"}'); } catch {}
}

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
function speakAll(text) {                // proactive one-shots (notifications)
  if (!$("voiceOut").checked) { faceState("idle"); return; }
  ttsSplit(String(text || "").slice(0, 1200)).forEach(p => wsSend({ type: "tts", text: p }));
}

async function sendStreaming(text) {
  text = (text || "").trim(); if (!text) return;
  addMsg(text, "user");
  const bubble = addMsg("", "evo"); bubble.classList.add("streaming");
  const body = bubble.querySelector(".msg-body");
  body.innerHTML = typingHTML();
  const sb = $("stopBtn"); sb.classList.remove("hidden");
  let acc = "", finished = false;
  aqCancel();                                    // fresh audio floor
  const finish = () => {
    if (finished) return;
    finished = true; turnHandler = null; currentController = null;
    sb.classList.add("hidden");
    bubble.classList.remove("streaming");
    if (!aq.cur && !aq.items.length) faceState("idle");
  };
  currentController = { abort() { wsCancelTurn(); finish(); toast("Stopped."); } };
  turnHandler = ev => {
    switch (ev.type) {
      case "thinking": if (!acc) body.innerHTML = typingHTML(); break;
      case "reset": acc = ""; body.innerHTML = typingHTML(); break;
      case "progress": {
        const c = document.createElement("div"); c.className = "tool-chip";
        c.innerHTML = `<b>⚙</b><i>${esc(ev.text)}</i>`; bubble.insertBefore(c, body); break;
      }
      case "tool": {
        const c = document.createElement("div"); c.className = "tool-chip";
        c.innerHTML = `<b>⚙ ${esc(ev.name)}</b><i>${esc(ev.brief)}</i>`; bubble.insertBefore(c, body); break;
      }
      case "delta":
        acc += ev.text; setBody(body, acc, "evo");
        scrollArea.scrollTop = scrollArea.scrollHeight;
        break;
      case "final":
        acc = ev.reply || acc || "(no response)";
        setBody(body, acc, "evo");
        finish();
        break;
      case "error":
        if (ev.text === "busy") { toast("EVO is still answering the previous request."); finish(); }
        else if (ev.text !== "cancelled") toast(ev.text);
        break;
    }
  };
  wsSend({ type: "say", text, voice: $("voiceOut").checked });
}

$("chatForm").addEventListener("submit", e => { e.preventDefault(); const v = $("chatInput").value.trim(); $("chatInput").value = ""; $("chatInput").style.height = "auto"; sendStreaming(v); });
$("chatInput").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); } });
$("chatInput").addEventListener("input", function () { this.style.height = "auto"; this.style.height = Math.min(this.scrollHeight, 180) + "px"; });
document.addEventListener("keydown", e => { if (e.key === "Escape" && currentController) { currentController.abort(); toast("Stopped."); } });
$("stopBtn")?.addEventListener("click", () => { if (currentController) { currentController.abort(); toast("Stopped."); } });

/* PTT — primary: Web Speech API (browser-native streaming STT, no server
   round trip, no whisper wait). Fallback: legacy record->WAV->/api/transcribe
   for browsers without SpeechRecognition or when the service errors. */
let mediaStream, audioCtx, processor, sourceNode, pttRecording, speechSeen, quietFrames, collected;
const PTT_RATE = 16000;
const SRClass = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, srGotFinal = false;

function pttStart() {
  if (pttRecording) return;
  if (currentController) currentController.abort();
  aqCancel();                                    // stop speech, free the floor
  if (SRClass) return srStart();
  return legacyRecorderStart();
}
function pttFinish() {
  if (!pttRecording) return;
  if (recog) { try { recog.stop(); } catch {} return; }   // let final arrive
  legacyRecorderFinish();
}

/* --- browser-native recognition --- */
function srStart() {
  recog = new SRClass();
  recog.lang = "en-US";
  recog.continuous = false;
  recog.interimResults = true;
  recog.maxAlternatives = 1;
  pttRecording = true; srGotFinal = false;
  $("pttBtn").classList.add("on"); faceState("listening");
  $("sttPreview").textContent = "Listening… speak now";
  recog.onresult = e => {
    let interim = "", fin = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) fin += r[0].transcript; else interim += r[0].transcript;
    }
    if (interim) $("sttPreview").textContent = interim;
    if (fin && !srGotFinal) { srGotFinal = true; srFinish(fin); }
  };
  recog.onerror = e => {
    const why = e.error || "";
    if (!pttRecording) return;
    srReset();
    if (why === "not-allowed" || why === "service-not-allowed") { toast("Microphone/speech blocked."); return; }
    toast(`Speech service unavailable (${why}) — falling back to local Whisper.`);
    legacyRecorderStart();
  };
  recog.onend = () => { if (pttRecording && !srGotFinal) srReset(); };
  try { recog.start(); }
  catch { srReset(); legacyRecorderStart(); }
}
function srFinish(text) {
  srReset();
  const t = (text || "").trim();
  $("sttPreview").textContent = "";
  if (!t) { toast("Didn't catch that."); faceState("idle"); return; }
  sendStreaming(t);                              // straight to the brain
}
function srReset() {
  pttRecording = false;
  $("pttBtn").classList.remove("on");
  try { recog && recog.abort(); } catch {}
  recog = null;
}

/* --- legacy fallback: record WAV -> /api/transcribe -> sendStreaming --- */
async function legacyRecorderStart() {
  if (pttRecording) return;
  if (currentController) currentController.abort();
  try { mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } }); }
  catch { toast("Microphone blocked."); return; }
  pttRecording = true; speechSeen = false; quietFrames = 0; collected = [];
  aqCancel();
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
    if ((speechSeen && quietFrames * frameMs > 1000) || elapsed > 9000) legacyRecorderFinish();
  };
  sourceNode.connect(processor); processor.connect(audioCtx.destination);
  $("pttBtn").classList.add("on"); faceState("listening");
  $("sttPreview").textContent = "Listening… speak now";
}

async function legacyRecorderFinish() {
  if (!pttRecording) return;
  pttRecording = false; $("pttBtn").classList.remove("on");
  try { processor.disconnect(); sourceNode.disconnect(); } catch {}
  try { mediaStream.getTracks().forEach(t => t.stop()); } catch {}
  const rate = audioCtx ? audioCtx.sampleRate : 16000;
  const samples = new Int16Array(collected.length);
  for (let i = 0; i < collected.length; i++) { const s = Math.max(-1, Math.min(1, collected[i])); samples[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
  collected = []; try { await audioCtx.close(); } catch {} audioCtx = null;
  if (!speechSeen || samples.length < rate / 2) { $("sttPreview").textContent = ""; toast("Didn't hear anything."); faceState("idle"); return; }
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
    toast(`Transcription failed${msg ? ` (${msg})` : ""}.`);
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
    $("engineTag").textContent = `piper · ${h.llm_online === true ? "online" : h.llm_online === false ? "offline" : "checking"}`;
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
/* Always-on conversation mode (local Vosk loop): mic stays open, replies
   are spoken through the same Piper-first TTS. The Web Speech PTT above is
   the primary voice input; this toggle is for hands-free at the desk. */
let convoOn = false;
$("convoBtn").addEventListener("click", async () => {
  const want = !convoOn;
  try {
    if (want) aqCancel();
    const r = await fetch("/api/voice/convo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: want })
    });
    const j = await r.json();
    convoOn = !!j.running;
    $("convoBtn").classList.toggle("on", convoOn);
    toast(convoOn ? "Conversation mode on — just talk."
                  : "Conversation mode closed.");
  } catch { toast("Could not toggle conversation mode."); }
});
setInterval(async () => {
  try {
    const s = await (await fetch("/api/voice/convo")).json();
    convoOn = !!s.running;
    $("convoBtn").classList.toggle("on", convoOn);
  } catch {}
}, 5000);

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
    if (ev.type === "convo.turn") {
      const u = ev.payload?.text || "", r = ev.payload?.reply || "";
      if (u) addMsg(u, "user");
      if (r) addMsg(r, "evo", { highlight: false });
    }
    if (ev.type === "voice.turn") {              // live WebRTC voice turns
      const u = ev.payload?.user || "", r = ev.payload?.reply || "";
      if (u) addMsg(u, "user");
      if (r) addMsg(r, "evo", { highlight: false });
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
