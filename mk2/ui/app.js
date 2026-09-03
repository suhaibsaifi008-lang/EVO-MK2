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
    if (li) { out += "<li>"+inline(li[1])+"</li>"; continue; }
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

/* ===== TAB NAVIGATION ===== */
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    // Auto-load data on first visit
    if (btn.dataset.tab === "swarm") refreshSwarms();
    if (btn.dataset.tab === "tools") refreshTools();
    if (btn.dataset.tab === "system") refreshSystem();
  });
});

/* ===== MAGNETIC CUSTOM CURSOR ===== */
const cursorRing = $("cursor-ring");
const cursorDot = $("cursor-dot");
if (cursorRing && cursorDot) {
  let mouseX = innerWidth / 2, mouseY = innerHeight / 2, ringX = mouseX, ringY = mouseY;
  window.addEventListener("mousemove", e => {
    mouseX = e.clientX; mouseY = e.clientY;
    cursorDot.style.left = `${mouseX}px`;
    cursorDot.style.top = `${mouseY}px`;
  });
  function renderCursor() {
    ringX += (mouseX - ringX) * 0.2;
    ringY += (mouseY - ringY) * 0.2;
    cursorRing.style.left = `${ringX}px`;
    cursorRing.style.top = `${ringY}px`;
    requestAnimationFrame(renderCursor);
  }
  requestAnimationFrame(renderCursor);

  document.querySelectorAll("a, button, .prompt-chip, input, textarea, .tab-btn").forEach(el => {
    el.addEventListener("mouseenter", () => {
      cursorRing.style.width = "38px";
      cursorRing.style.height = "38px";
      cursorRing.style.borderColor = "var(--amber-light)";
    });
    el.addEventListener("mouseleave", () => {
      cursorRing.style.width = "22px";
      cursorRing.style.height = "22px";
      cursorRing.style.borderColor = "var(--amber)";
    });
  });
}

window.quickPrompt = function(text) {
  const input = $("chatInput");
  if (!input) return;
  input.value = text;
  input.focus();
  const form = $("chatForm");
  if (form) {
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  }
};

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
    gr.addColorStop(0,`rgba(255,208,128,${.7*g+.1})`);gr.addColorStop(.55,`rgba(255,170,48,${.4*g})`);gr.addColorStop(1,"transparent");
    fx.beginPath();fx.arc(cxp,cyp,R*1.2,0,7);fx.fillStyle=gr;fx.fill();
    for(const[rr,lw,a]of[[1,2,.85],[1.2,1,.5],[1.45,1,.25]]){fx.beginPath();fx.arc(cxp,cyp,R*rr,ft*speed(),ft*speed()+Math.PI*1.5);fx.strokeStyle=`rgba(255,170,48,${a})`;fx.lineWidth=lw;fx.stroke()}
    for(const p of parts){p.a+=p.sp*speed();const rr=R*p.r*1.5;fx.beginPath();fx.arc(cxp+Math.cos(p.a)*rr,cyp+Math.sin(p.a)*rr,p.s,0,7);fx.fillStyle=`rgba(255,170,48,${.2+.35*Math.abs(Math.sin(p.a*2))}`+")";fx.fill()}
    for(const tr of traces){tr.v+=tr.sp*speed();if(tr.v>1)tr.v=0;const x0=tr.x*FW,y0=tr.y*FH,x1=x0+tr.l*FW*tr.d;fx.strokeStyle=`rgba(255,170,48,${.06+.18*tr.v})`;fx.lineWidth=1;fx.strokeRect(Math.min(x0,x1),y0,Math.abs(x1-x0),2)}
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

/* ============ VOICE PIPELINE v3 ============ */
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
  if (!a) { wl.duckUntil = Date.now() + 700;
            if (!currentController) faceState("idle");
            return; }
  aq.cur = a;
  faceState("speaking");
  const fin = () => {
    if (aq.cur === a) aq.cur = null;
    try { URL.revokeObjectURL(a.src); } catch {}
    if (!aq.cur && !aq.items.length) faceState("idle");
    else aqPump();
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
let turnHandler = null;
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
function speakAll(text) {
  if (!$("voiceOut").checked) { faceState("idle"); return; }
  ttsSplit(String(text || "").slice(0, 1200)).forEach(p => wsSend({ type: "tts", text: p }));
}

async function sendStreaming(text) {
  text = (text || "").trim(); if (!text) return;
  // Switch to chat tab if not already there
  if (!$("tab-chat").classList.contains("active")) {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    document.querySelector('[data-tab="chat"]').classList.add("active");
    $("tab-chat").classList.add("active");
  }
  addMsg(text, "user");
  const bubble = addMsg("", "evo"); bubble.classList.add("streaming");
  const body = bubble.querySelector(".msg-body");
  body.innerHTML = typingHTML();
  const sb = $("stopBtn"); sb.classList.remove("hidden");
  let acc = "", finished = false;
  aqCancel();
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
      case "progress":
      case "tool":
        if (!acc) body.innerHTML = `${typingHTML()} <span class="tool-subtle">working...</span>`;
        break;
      case "delta":
        acc += ev.text; setBody(body, acc, "evo");
        scrollArea.scrollTop = scrollArea.scrollHeight;
        break;
      case "final":
        acc = ev.reply || acc || "(no response)";
        setBody(body, acc, "evo");
        wl.lastReplyHead = (ev.reply || "").toLowerCase().slice(0, 60);
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

/* PTT & Live Mic */
let mediaStream, audioCtx, processor, sourceNode, pttRecording, speechSeen, quietFrames, collected;
const PTT_RATE = 16000;
const SRClass = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, srGotFinal = false;
let preferLocalAudioSTT = localStorage.getItem("evo_prefer_local_stt") === "1";

function pttStart() {
  if (pttRecording) return;
  if (currentController) currentController.abort();
  aqCancel();
  if (SRClass && !preferLocalAudioSTT) return srStart();
  return legacyRecorderStart();
}
function pttFinish() {
  if (!pttRecording) return;
  if (recog) { try { recog.stop(); } catch {} return; }
  legacyRecorderFinish();
}

function srStart() {
  recog = new SRClass();
  recog.lang = "en-US";
  recog.continuous = false;
  recog.interimResults = true;
  recog.maxAlternatives = 1;
  pttRecording = true; srGotFinal = false;
  $("pttBtn").classList.add("on"); faceState("listening");
  $("sttPreview").textContent = "🎙 Listening… speak now";
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
    if (why === "not-allowed" || why === "service-not-allowed") { toast("Microphone permission denied."); return; }
    // Cloud speech recognition unavailable on network/localhost — seamlessly remember and use local Whisper
    preferLocalAudioSTT = true;
    localStorage.setItem("evo_prefer_local_stt", "1");
    legacyRecorderStart();
  };
  recog.onend = () => { if (pttRecording && !srGotFinal) srReset(); };
  try { recog.start(); }
  catch {
    preferLocalAudioSTT = true;
    localStorage.setItem("evo_prefer_local_stt", "1");
    srReset();
    legacyRecorderStart();
  }
}
function srFinish(text) {
  srReset();
  const t = (text || "").trim();
  $("sttPreview").textContent = "";
  if (!t) { toast("Didn't catch that."); faceState("idle"); return; }
  sendStreaming(t);
}
function srReset() {
  pttRecording = false;
  $("pttBtn").classList.remove("on");
  try { recog && recog.abort(); } catch {}
  recog = null;
}

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
    if (peak > .015) { speechSeen = true; quietFrames = 0; } else if (speechSeen) quietFrames++;
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
  if (!speechSeen && samples.length < rate * 0.2) { $("sttPreview").textContent = ""; toast("Didn't hear anything."); faceState("idle"); return; }
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
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
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

/* ================= EVENT BUS LISTENER ================= */
let micOn = false;
let handsFree = null;

function hfStop() {
  micOn = false;
  try { handsFree && handsFree.abort(); } catch {}
  handsFree = null;
  wlStop();
  $("convoBtn").classList.remove("on");
  $("sttPreview").textContent = "";
  faceState("idle");
}

const wl = { on: false, ctx: null, proc: null, src: null, stream: null,
             buf: [], seen: false, quiet: 0, busy: false,
             duckUntil: 0, lastReplyHead: "" };

async function wlStart() {
  if (wl.on) return;
  try {
    wl.stream = await navigator.mediaDevices.getUserMedia(
      { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch { toast("Microphone blocked."); hfStop(); return; }
  wl.on = true; wl.seen = false; wl.quiet = 0; wl.buf = []; wl.busy = false;
  try { wl.ctx = new AudioContext({ sampleRate: 16000 }); }
  catch { wl.ctx = new AudioContext(); }
  wl.src = wl.ctx.createMediaStreamSource(wl.stream);
  wl.proc = wl.ctx.createScriptProcessor(2048, 1, 1);
  const rate = wl.ctx.sampleRate;
  wl.proc.onaudioprocess = ev => {
    if (!wl.on) return;
    if (aq.cur || currentController || Date.now() < wl.duckUntil) {
      wl.buf = []; wl.seen = false; wl.quiet = 0;
      return;
    }
    const input = ev.inputBuffer.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < input.length; i++) { const v = Math.abs(input[i]); if (v > peak) peak = v; }
    const frameMs = input.length / rate * 1000;
    if (peak > .015) { wl.seen = true; wl.quiet = 0; }
    else if (wl.seen) wl.quiet++;
    if (wl.seen) for (let i = 0; i < input.length; i++) wl.buf.push(input[i]);
    const heldMs = wl.buf.length / rate * 1000;
    if ((wl.seen && !wl.busy && wl.quiet * frameMs > 650) || heldMs > 12000) {
      wlUtterance(rate);
    }
  };
  wl.src.connect(wl.proc); wl.proc.connect(wl.ctx.destination);
  faceState("listening");
}

async function wlUtterance(rate) {
  const raw = wl.buf;
  wl.buf = []; wl.seen = false; wl.quiet = 0;
  if (!raw.length || raw.length < rate * 0.35) return;
  wl.busy = true;
  $("sttPreview").textContent = "Transcribing…";
  faceState("thinking");
  try {
    const samples = new Int16Array(raw.length);
    for (let i = 0; i < raw.length; i++) {
      const s = Math.max(-1, Math.min(1, raw[i]));
      samples[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const res = await fetch("/api/transcribe",
                            { method: "POST", body: encodeWav(samples, rate) });
    const data = await res.json();
    let t = (data.text || "").trim();
    if (t && wl.lastReplyHead &&
        wl.lastReplyHead.includes(t.toLowerCase().slice(0, 40))) {
      t = "";
    }
    if (t) {
      if (currentController) $("sttPreview").textContent = "(still answering) " + t;
      else sendStreaming(t);
    }
  } catch { /* keep the loop alive regardless */ }
  $("sttPreview").textContent = micOn ? "Listening…" : "";
  wl.busy = false;
}

function wlStop() {
  wl.on = false;
  try { wl.proc && wl.proc.disconnect(); } catch {}
  try { wl.src && wl.src.disconnect(); } catch {}
  try { wl.stream && wl.stream.getTracks().forEach(t => t.stop()); } catch {}
  try { wl.ctx && wl.ctx.close(); } catch {}
  wl.proc = wl.src = wl.ctx = wl.stream = null;
  wl.buf = []; wl.seen = false; wl.quiet = 0; wl.busy = false;
}

function hfStart() {
  handsFree = new SRClass();
  handsFree.continuous = true;
  handsFree.interimResults = true;
  handsFree.lang = "en-US";
  handsFree.onresult = e => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) {
        const t = r[0].transcript.trim();
        if (!t) continue;
        if (currentController) {
          $("sttPreview").textContent = "(still answering) " + t;
          continue;
        }
        sendStreaming(t);
      } else interim += r[0].transcript;
    }
    if (interim) $("sttPreview").textContent = "… " + interim;
  };
  handsFree.onend = () => {
    if (micOn && handsFree) { try { handsFree.start(); } catch {} }
  };
  handsFree.onerror = ev => {
    if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
      toast("Microphone permission denied.");
      hfStop();
      return;
    }
    if (!micOn || !handsFree) return;
    if (ev.error === "network" || ev.error === "audio-capture" || ev.error === "no-speech") {
      preferLocalAudioSTT = true;
      localStorage.setItem("evo_prefer_local_stt", "1");
      try { handsFree.abort(); } catch {}
      handsFree = null;
      wlStart();
    }
  };
  try { handsFree.start(); } catch {
    preferLocalAudioSTT = true;
    localStorage.setItem("evo_prefer_local_stt", "1");
    wlStart();
  }
}

$("convoBtn").addEventListener("click", async () => {
  if (micOn) {
    hfStop();
    toast("Live mic closed.");
    return;
  }
  aqCancel();
  micOn = true;
  $("convoBtn").classList.add("on");
  $("sttPreview").textContent = "🎙 Listening… just talk.";
  toast("Live mic open — speak naturally.");
  if (SRClass && !preferLocalAudioSTT) hfStart();
  else wlStart();
});

const es = new EventSource("/api/events");
const _lastProg = {};
es.onmessage = (m) => {
  try {
    const ev = JSON.parse(m.data);
    if (ev.type === "notify.out") {
      const text = ev.payload?.text || "";
      const kind = ev.payload?.kind || "";
      if (kind === "reply" || kind === "deep_research" || kind === "docs_create") {
        addMsg(text, "evo", { highlight: true });
        speakAll(text.slice(0, 1200));
      } else if (text) {
        toast(text);
      }
    }
    if (ev.type === "job.progress") {
      const { id, goal, step, max_steps } = ev.payload || {};
      const now = Date.now();
      if (!_lastProg[id] || now - _lastProg[id] > 10000) {
        _lastProg[id] = now;
        toast(`⚙ Mission #${id} · step ${step}/${max_steps}: ${goal}`);
      }
    }
    if (ev.type === "convo.turn") {
      const u = ev.payload?.text || "", r = ev.payload?.reply || "";
      if (u) addMsg(u, "user");
      if (r) addMsg(r, "evo", { highlight: false });
    }
    if (ev.type === "voice.turn") {
      const u = ev.payload?.user || "", r = ev.payload?.reply || "";
      if (u) addMsg(u, "user");
      if (r) addMsg(r, "evo", { highlight: false });
    }
    if (ev.type === "system.voice") {
      faceState(ev.payload?.state === "session" ? "listening" : "idle");
    }
    // Live swarm events
    if (ev.type === "swarm.task.completed" || ev.type === "swarm.completed") {
      if ($("tab-swarm").classList.contains("active")) refreshSwarms();
    }
  } catch {}
};
es.onerror = () => {};

/* ===== SWARM PANEL ===== */
async function refreshSwarms() {
  const list = $("swarmList");
  try {
    const res = await fetch("/api/swarm/status");
    const data = await res.json();
    const swarms = data.swarms || [];
    if (!swarms.length) {
      list.innerHTML = '<div class="empty-state">No active swarms. Dispatch one above.</div>';
      return;
    }
    list.innerHTML = "";
    for (const sw of swarms) {
      const item = document.createElement("div");
      item.className = "swarm-item";
      const statusCls = sw.status === "done" ? "done" : sw.status === "failed" ? "failed" : "running";
      item.innerHTML = `
        <div class="swarm-item-header">
          <span class="swarm-item-id">${esc(sw.id)}</span>
          <span class="swarm-item-status ${statusCls}">${esc(sw.status)}</span>
        </div>
        <div class="swarm-item-obj">${esc(sw.objective)}</div>
      `;
      item.onclick = () => showSwarmDetail(sw.id);
      list.appendChild(item);
    }
  } catch (e) {
    list.innerHTML = `<div class="empty-state">Error loading swarms: ${esc(e.message)}</div>`;
  }
}

async function showSwarmDetail(swarmId) {
  const card = $("swarmDetailCard");
  const body = $("swarmDetailBody");
  $("swarmDetailId").textContent = swarmId;
  card.style.display = "";
  body.innerHTML = '<div class="empty-state">Loading…</div>';
  try {
    const res = await fetch(`/api/swarm/status?swarm_id=${encodeURIComponent(swarmId)}`);
    const data = await res.json();
    const sw = data.swarm || {};
    const tasks = sw.tasks || {};
    let html = `<p><strong>Status:</strong> ${esc(sw.status)} &nbsp; <strong>Duration:</strong> ${(sw.duration_s || 0).toFixed(1)}s</p>`;
    html += '<div style="margin-top:12px">';
    for (const [tid, t] of Object.entries(tasks)) {
      const dotCls = t.status === "done" ? "done" : t.status === "running" ? "running" : t.status === "failed" ? "failed" : "pending";
      html += `<div class="task-row">
        <span class="task-dot ${dotCls}"></span>
        <span class="task-role">${esc(t.role)}</span>
        <span class="task-obj">${esc(t.objective)}</span>
        <span class="task-dur">${(t.duration_s || 0).toFixed(1)}s</span>
      </div>`;
    }
    html += '</div>';
    if (sw.final_synthesis) {
      html += `<div style="margin-top:16px"><strong>Synthesis:</strong></div><pre>${esc(sw.final_synthesis)}</pre>`;
    }
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
  }
}

$("swarmDispatchBtn").addEventListener("click", async () => {
  const obj = $("swarmObjective").value.trim();
  if (!obj) { toast("Enter an objective."); return; }
  const btn = $("swarmDispatchBtn");
  btn.disabled = true; btn.textContent = "Dispatching…";
  try {
    const res = await fetch("/api/swarm/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ objective: obj, background: $("swarmBg").checked }),
    });
    const data = await res.json();
    if (data.ok) {
      toast(data.speech || "Swarm dispatched!");
      $("swarmObjective").value = "";
      setTimeout(refreshSwarms, 1000);
    } else {
      toast("Swarm dispatch failed: " + (data.detail || data.speech || "Unknown error"));
    }
  } catch (e) {
    toast("Dispatch error: " + e.message);
  }
  btn.disabled = false; btn.textContent = "🚀 Dispatch Swarm";
});

$("swarmRefreshBtn").addEventListener("click", refreshSwarms);

/* ===== TOOLS PANEL ===== */
async function refreshTools() {
  const list = $("toolList");
  const count = $("toolCount");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const tools = data.tools || 0;
    count.textContent = tools;

    // Also try to get tool list from chat
    const res2 = await fetch("/api/diag");
    const diag = await res2.json();
    const toolNames = diag.tools || [];
    if (toolNames.length) {
      list.innerHTML = "";
      for (const name of toolNames) {
        const item = document.createElement("div");
        item.className = "tool-item";
        item.innerHTML = `
          <div>
            <div class="tool-item-name">${esc(name)}</div>
          </div>
          <span class="tool-item-badge">registered</span>
        `;
        list.appendChild(item);
      }
    } else {
      list.innerHTML = `<div class="empty-state">${tools} tools loaded (details unavailable)</div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
  }
}

$("toolSynthBtn").addEventListener("click", async () => {
  const name = $("toolName").value.trim();
  const desc = $("toolDesc").value.trim();
  const code = $("toolCode").value.trim();
  if (!name || !desc || !code) { toast("Fill in all fields."); return; }
  const btn = $("toolSynthBtn");
  const result = $("toolSynthResult");
  btn.disabled = true; btn.textContent = "Synthesizing…";
  result.style.display = "none";
  try {
    const res = await fetch("/api/tools/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: desc, python_code: code }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      result.className = "result-box success";
      result.textContent = "✅ " + (data.speech || `Tool "${name}" synthesized successfully!`);
      result.style.display = "";
      $("toolName").value = "";
      $("toolDesc").value = "";
      $("toolCode").value = "";
      setTimeout(refreshTools, 500);
    } else {
      result.className = "result-box error";
      result.textContent = "❌ " + (data.detail || data.speech || "Synthesis failed");
      result.style.display = "";
    }
  } catch (e) {
    result.className = "result-box error";
    result.textContent = "❌ Error: " + e.message;
    result.style.display = "";
  }
  btn.disabled = false; btn.textContent = "⚡ Synthesize";
});

$("toolRefreshBtn").addEventListener("click", refreshTools);

/* ===== SYSTEM PANEL ===== */
async function refreshSystem() {
  // Status & Telemetry
  try {
    const s = await (await fetch("/api/status")).json();
    $("sysStatus").textContent = s.ok ? "Online" : "Unknown";
    $("sysStatus").style.color = s.ok ? "var(--grn)" : "var(--warn)";
    $("sysLLM").textContent = s.brain?.model || "Connected";
    $("sysLLM").style.color = "var(--grn)";
    $("sysTools").textContent = `${s.memory?.facts || 0} facts | ${s.memory?.profile_depth || 0}% depth`;
    
    // Update HUD metrics
    if ($("hud-model")) $("hud-model").textContent = s.brain?.model || "Claude Sonnet";
    if ($("hud-depth")) $("hud-depth").textContent = `${s.memory?.profile_depth || 0}%`;
    if ($("hud-missions")) $("hud-missions").textContent = s.autonomy?.missions || "0";
    if ($("hud-facts")) $("hud-facts").textContent = s.memory?.facts || "0";
    if ($("hud-voice")) $("hud-voice").textContent = s.voice?.voice_v2 ? "Hybrid Live" : "Streaming TTS";
  } catch {
    try {
      const h = await (await fetch("/api/health")).json();
      $("sysStatus").textContent = h.status === "ok" ? "Online" : h.status || "Unknown";
      $("sysStatus").style.color = h.status === "ok" ? "var(--grn)" : "var(--warn)";
      $("sysLLM").textContent = h.llm_online ? "Connected" : "Offline";
      $("sysLLM").style.color = h.llm_online ? "var(--grn)" : "var(--red)";
      $("sysTools").textContent = h.tools || "—";
    } catch {
      $("sysStatus").textContent = "Unreachable";
      $("sysStatus").style.color = "var(--red)";
    }
  }
  // Sync
  try {
    const s = await (await fetch("/api/sync/status")).json();
    $("sysDevice").textContent = s.device_name || "—";
  } catch {}
}

/* ===== JARVIS BRAIN RING HUD ===== */
const hudCanvas = $("brain-ring");
if (hudCanvas) {
  const ctx = hudCanvas.getContext("2d");
  let ringAngle = 0;
  function drawBrainRing() {
    ctx.clearRect(0, 0, 180, 180);
    const cx = 90, cy = 90;
    
    // Outer rotating segmented arc
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(ringAngle);
    ctx.beginPath();
    ctx.arc(0, 0, 78, 0, Math.PI * 1.3);
    ctx.strokeStyle = "rgba(255, 170, 48, 0.7)";
    ctx.lineWidth = 2.5;
    ctx.stroke();
    
    ctx.beginPath();
    ctx.arc(0, 0, 78, Math.PI * 1.5, Math.PI * 1.8);
    ctx.strokeStyle = "rgba(255, 208, 128, 0.4)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();

    // Inner counter-rotating ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-ringAngle * 1.5);
    ctx.beginPath();
    ctx.arc(0, 0, 64, 0, Math.PI * 0.8);
    ctx.strokeStyle = "rgba(255, 170, 48, 0.9)";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(0, 0, 64, Math.PI * 1.1, Math.PI * 1.7);
    ctx.strokeStyle = "rgba(255, 170, 48, 0.35)";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();

    // Subtle pulsing center aura
    const pulse = 0.5 + 0.5 * Math.sin(Date.now() * 0.003);
    ctx.beginPath();
    ctx.arc(cx, cy, 50, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255, 170, 48, ${0.04 + pulse * 0.05})`;
    ctx.fill();

    ringAngle += 0.015;
    requestAnimationFrame(drawBrainRing);
  }
  requestAnimationFrame(drawBrainRing);
}

$("syncRefreshBtn").addEventListener("click", async () => {
  const body = $("syncStatus");
  body.innerHTML = "Loading…";
  try {
    const s = await (await fetch("/api/sync/status")).json();
    body.innerHTML = `
      <p><strong>Device ID:</strong> <code>${esc(s.device_id)}</code></p>
      <p><strong>Device Name:</strong> ${esc(s.device_name)}</p>
      <p><strong>Status:</strong> ${s.ok ? "✅ Ready" : "❌ Error"}</p>
    `;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
  }
});

$("syncPullBtn").addEventListener("click", async () => {
  try {
    const bundle = await (await fetch("/api/sync/pull")).json();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `evo-sync-bundle-${Date.now()}.json`;
    a.click(); URL.revokeObjectURL(url);
    toast("Sync bundle downloaded.");
  } catch (e) {
    toast("Pull failed: " + e.message);
  }
});

$("memRefreshBtn").addEventListener("click", async () => {
  const body = $("memBody");
  body.innerHTML = "Loading…";
  try {
    const m = await (await fetch("/api/memory")).json();
    let html = "";
    if (m.facts && m.facts.length) {
      html += "<h4 style='margin:8px 0 4px;font-size:13px;color:var(--acc)'>Facts</h4>";
      for (const f of m.facts.slice(0, 30)) {
        html += `<div class="mem-item"><span class="mem-key">${esc(f.key || f.k || "—")}:</span> <span class="mem-val">${esc(f.value || f.v || f.text || "")}</span></div>`;
      }
    }
    if (m.opinions && Object.keys(m.opinions).length) {
      html += "<h4 style='margin:12px 0 4px;font-size:13px;color:var(--acc2)'>Opinions</h4>";
      for (const [k, v] of Object.entries(m.opinions)) {
        html += `<div class="mem-item"><span class="mem-key">${esc(k)}:</span> <span class="mem-val">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span></div>`;
      }
    }
    if (!html) html = '<div class="empty-state">No memory data available.</div>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
  }
});

$("memClearBtn").addEventListener("click", async () => {
  if (!confirm("Clear all chat history? This cannot be undone.")) return;
  try {
    await fetch("/api/memory/clear-chat", { method: "POST" });
    toast("Chat history cleared.");
  } catch (e) {
    toast("Clear failed: " + e.message);
  }
});

$("pinVerifyBtn").addEventListener("click", async () => {
  const pin = $("pinInput").value.trim();
  if (!pin) { toast("Enter a PIN."); return; }
  const result = $("pinResult");
  try {
    const res = await fetch("/api/auth/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin }),
    });
    const data = await res.json();
    if (data.ok) {
      result.className = "result-box success";
      result.textContent = "✅ PIN verified. Security lockout cleared.";
    } else {
      result.className = "result-box error";
      result.textContent = "❌ " + (data.speech || data.detail || "Invalid PIN");
    }
    result.style.display = "";
    $("pinInput").value = "";
  } catch (e) {
    result.className = "result-box error";
    result.textContent = "❌ Error: " + e.message;
    result.style.display = "";
  }
});

$("diagRefreshBtn").addEventListener("click", async () => {
  const body = $("diagBody");
  body.innerHTML = "Running diagnostics…";
  try {
    const d = await (await fetch("/api/diag")).json();
    let html = "<pre>" + esc(JSON.stringify(d, null, 2)) + "</pre>";
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
  }
});

// ===== JARVIS AUTONOMY & APPROVAL QUEUE =====
async function loadAutonomyStats() {
  try {
    const res = await fetch("/api/autonomy/stats");
    if (!res.ok) return;
    const data = await res.json();
    if ($("autoConsent")) $("autoConsent").textContent = data.consent_level || "assist";
    if ($("autoTrust")) $("autoTrust").textContent = Math.round((data.trust_score || 1.0) * 100) + "%";
    if ($("autoRevenue")) $("autoRevenue").textContent = "$" + (data.revenue_stats?.total_revenue || 0).toFixed(2);
    if ($("autoPlatforms")) $("autoPlatforms").textContent = (data.configured_services || ["upwork", "email"]).join(", ") || "Upwork, Email";
  } catch (e) {
    console.debug("Autonomy stats error:", e);
  }
}

async function loadApprovals() {
  const container = $("approvalsList");
  if (!container) return;
  try {
    const res = await fetch("/api/autonomy/approvals");
    const data = await res.json();
    const items = data.approvals || [];
    if (items.length === 0) {
      container.innerHTML = `<div class="empty-state">No pending actions. EVO is monitoring opportunities in the background.</div>`;
      return;
    }
    let html = "";
    items.forEach(item => {
      const act = item.action || {};
      const verdict = item.verdict || {};
      const title = act.title || act.type || "Opportunity Action";
      const details = act.cover_note || act.body || act.description || JSON.stringify(act);
      html += `
        <div class="card" style="margin-bottom:12px; border-left: 3px solid #ffaa30;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <b>#${item.id} · ${esc(title)}</b>
            <span class="tag" style="background:#443311; color:#ffaa30;">${esc(verdict.verdict || "caution").toUpperCase()}</span>
          </div>
          <div style="font-size:12px; color:#aaa; margin:6px 0;">Reason: ${esc(verdict.reasoning || "Requires user authorization")}</div>
          <div style="background:#111; padding:8px; border-radius:4px; font-size:13px; margin:8px 0; white-space:pre-wrap;">${esc(details.slice(0, 400))}</div>
          <div class="toolbar" style="margin-top:8px;">
            <button class="action-btn small" onclick="window.approveAutonomy('${item.id}')" style="background:#28a745;">✓ Approve & Execute</button>
            <button class="mini-action danger" onclick="window.rejectAutonomy('${item.id}')" style="margin-left:8px;">✗ Reject</button>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="empty-state">Error loading queue: ${esc(e.message)}</div>`;
  }
}

window.approveAutonomy = async (id) => {
  try {
    const res = await fetch("/api/autonomy/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const d = await res.json();
    if (d.ok) {
      toast(`✅ Action #${id} approved!`);
      loadApprovals();
      loadAutonomyStats();
    }
  } catch (e) {
    toast(`❌ Failed: ${e.message}`);
  }
};

window.rejectAutonomy = async (id) => {
  try {
    const res = await fetch("/api/autonomy/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, reason: "User declined via dashboard" }),
    });
    const d = await res.json();
    if (d.ok) {
      toast(`Action #${id} rejected.`);
      loadApprovals();
      loadAutonomyStats();
    }
  } catch (e) {
    toast(`❌ Failed: ${e.message}`);
  }
};

if ($("killSwitchBtn")) {
  $("killSwitchBtn").addEventListener("click", async () => {
    if (!confirm("EMERGENCY KILL SWITCH: Immediately halt all autonomous loops and browser actions?")) return;
    try {
      const res = await fetch("/api/autonomy/kill", { method: "POST" });
      const d = await res.json();
      toast("🛑 EMERGENCY STOP: All autonomous activity halted.");
      loadAutonomyStats();
      loadApprovals();
    } catch (e) {
      toast(`❌ Stop failed: ${e.message}`);
    }
  });
}

if ($("refreshApprovalsBtn")) $("refreshApprovalsBtn").addEventListener("click", loadApprovals);
if ($("scanOppsBtn")) {
  $("scanOppsBtn").addEventListener("click", async () => {
    toast("🔍 Scanning platforms for opportunities...");
    await loadAutonomyStats();
    await loadApprovals();
    toast("✅ Opportunity scan complete.");
  });
}

loadAutonomyStats();
loadApprovals();
setInterval(loadApprovals, 15000);

