"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const log = $("log");
let currentController = null;
let currentAudio = null;

function addMsg(text, who) {
  const hero = $("hero"); if (hero) hero.style.display = "none";
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  const body = document.createElement("div");
  body.className = "body";
  setBody(body, text, who);
  const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = ts;
  div.append(body, meta); log.appendChild(div); log.scrollTop = log.scrollHeight;
  return div;
}
function setBody(el, text, who) {
  if (who === "evo") el.innerHTML = mdLite(text);
  else el.textContent = text;
}
function mdLite(src) {
  let out = "", inCode = false, buf = [];
  const lines = String(src || "").split(/\r?\n/);
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  for (const raw of lines) {
    if (/^```/.test(raw)) {
      if (inCode) { out += "<pre><code>" + esc(buf.join("\n")) + "</code></pre>"; buf = []; }
      inCode = !inCode; continue;
    }
    if (inCode) { buf.push(raw); continue; }
    const ul = raw.match(/^\s*[-*]\s+(.*)$/);
    const li = raw.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul) { out += "<li>" + inline(ul[1]) + "</li>"; continue; }
    if (li) { out += "<li>" + inline(li[2]) + "</li>"; continue; }
    if (!raw.trim()) continue;
    out += "<p>" + inline(raw.trim()) + "</p>";
  }
  if (buf.length) out += "<pre><code>" + esc(buf.join("\n")) + "</code></pre>";
  return out || "";
}
function toast(text) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text;
  $("toasts").appendChild(t);
  while ($("toasts").children.length > 4) $("toasts").firstElementChild.remove();
  setTimeout(() => t.remove(), 8000);
}

async function sendStreaming(text) {
  text = (text || "").trim(); if (!text) return;
  addMsg(text, "you");
  const bubble = addMsg("", "evo"); bubble.classList.add("streaming");
  const body = bubble.querySelector(".body");
  body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  let acc = "", done = false;
  currentController = new AbortController();
  const stopBtn = $("stopBtn"); stopBtn.classList.add("on");
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }), signal: currentController.signal,
    });
    const reader = res.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done: d, value } = await reader.read(); if (d) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(line.slice(5)); } catch { continue; }
        if (ev.type === "thinking" && !acc) body.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
        else if (ev.type === "delta") { acc += ev.text; setBody(body, acc, "evo"); log.scrollTop = log.scrollHeight; }
        else if (ev.type === "done") { done = true; acc = ev.text || acc; setBody(body, acc, "evo"); }
          if (!$('voiceOut')?.checked) setTimeout(() => faceState("idle"), 1200);
        else if (ev.type === "error" && ev.text && ev.text !== "cancelled") toast(ev.text);
      }
    }
    if (!done) setBody(body, acc || "(no response)", "evo");
  } catch (e) {
    if (!done && !acc) setBody(body, "Connection lost.", "evo");
  } finally {
    bubble.classList.remove("streaming");
    currentController = null; stopBtn.classList.remove("on");
  }
}

$("chatForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("chatInput").value; $("chatInput").value = ""; $("chatInput").style.height = "auto";
  sendStreaming(v);
});
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && currentController) { currentController.abort(); toast("Stopped."); }
});

async function boot() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("engineTag").textContent = `${h.voice} · ${h.llm_online === true ? "LLM online" : h.llm_online === false ? "LLM offline" : "LLM checking"}`;
    $("conn").className = "dot online";
    const hr = new Date().getHours();
    const part = hr < 12 ? "morning" : hr < 18 ? "afternoon" : "evening";
    $("greet").textContent = `Good ${part}. ${h.name} MK2 online.`;
  } catch { $("conn").className = "dot offline"; }
}
setInterval(boot, 30000); boot();


/* ---------- push-to-talk: click MIC, speak, silence sends it ---------- */
let mediaStream = null, audioCtx = null, processor = null, sourceNode = null;
let pttRecording = false, speechSeen = false, quietFrames = 0, collected = [];

function encodeWav(int16, rate) {
  const buf = new ArrayBuffer(44 + int16.length * 2);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); v.setUint32(4, 36 + int16.length * 2, true); ws(8, "WAVE");
  ws(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, "data"); v.setUint32(40, int16.length * 2, true);
  for (let i = 0; i < int16.length; i++) v.setInt16(44 + i * 2, int16[i], true);
  return new Blob([buf], { type: "audio/wav" });
}

async function pttStart() {
  if (pttRecording) return;
  if (currentController) currentController.abort();   // cut any running reply
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }  // barge-in TTS
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch { toast("Microphone blocked - allow mic access."); return; }
  pttRecording = true; speechSeen = false; quietFrames = 0; collected = [];
  try { audioCtx = new AudioContext({ sampleRate: 16000 }); }
  catch { audioCtx = new AudioContext(); }
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);
  processor = audioCtx.createScriptProcessor(2048, 1, 1);
  const rate = audioCtx.sampleRate;
  processor.onaudioprocess = (ev) => {
    if (!pttRecording) return;
    const input = ev.inputBuffer.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < input.length; i++) { const v = Math.abs(input[i]); if (v > peak) peak = v; }
    if (peak > 0.06) { speechSeen = true; quietFrames = 0; }
    else if (speechSeen) quietFrames++;
    for (let i = 0; i < input.length; i++) collected.push(input[i]);
    const frameMs = (input.length / rate) * 1000;
    const elapsed = collected.length / rate * 1000;
    if ((speechSeen && quietFrames * frameMs > 1000) || elapsed > 9000) pttFinish();
  };
  sourceNode.connect(processor);
  processor.connect(audioCtx.destination);
  $("pttBtn").classList.add("on");
  faceState("listening");
  $("sttPreview").textContent = "Listening... speak now";
}

async function pttFinish() {
  if (!pttRecording) return;
  pttRecording = false;
  $("pttBtn").classList.remove("on");
  try { processor.disconnect(); sourceNode.disconnect(); } catch {}
  try { mediaStream.getTracks().forEach((t) => t.stop()); } catch {}
  const rate = audioCtx ? audioCtx.sampleRate : 16000;
  const samples = new Int16Array(collected.length);
  for (let i = 0; i < collected.length; i++) {
    const s = Math.max(-1, Math.min(1, collected[i]));
    samples[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  collected = [];
  try { await audioCtx.close(); } catch {} audioCtx = null;
  if (!speechSeen || samples.length < rate / 2) {
    $("sttPreview").textContent = ""; toast("Didnt hear anything."); return;
  }
  $("sttPreview").textContent = "Transcribing...";
    faceState("thinking");
  try {
    const wav = encodeWav(samples, rate);
    const res = await fetch("/api/transcribe", { method: "POST", body: wav });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    $("sttPreview").textContent = "";
    const text = (data.text || "").trim();
    if (!text) { toast("Didnt catch that - try again."); return; }
    sendStreaming(text);
  } catch (e) {
    $("sttPreview").textContent = "";
    toast("Transcription failed.");
  }
}

const _sendStreamingBase = sendStreaming;
sendStreaming = async function (text) {
  await _sendStreamingBase(text);
  const last = log.lastElementChild;
  if ($("voiceOut") && $("voiceOut").checked && last && last.classList.contains("evo")) {
    const body = last.querySelector(".body");
    const txt = (body && body.dataset.raw) || "";
    if (txt) {
      fetch(`/api/tts?text=${encodeURIComponent(txt.slice(0, 500))}`)
        .then((r) => { if (r.ok) return r.blob(); throw new Error("no"); })
        .then((b) => {
          if (currentAudio) currentAudio.pause();
          faceState("speaking");
          currentAudio = new Audio(URL.createObjectURL(b));
          currentAudio.play().catch(() => {});
          currentAudio.onended = () => faceState("idle");
          currentAudio.onpause = () => faceState("idle");
        })
        .catch(() => {});
    }
  }
};

/* ================= FACE ENGINE (living orb behind the console) ================= */
const faceCv = document.getElementById("faceCanvas");
if (faceCv) {
  const fx = faceCv.getContext("2d");
  let FW, FH, ft = 0;
  let faceStateVal = "idle";
  window.faceState = function (s) {
    faceStateVal = s;
    const el = document.getElementById("stateTag");
    if (el) el.textContent = s;
  };
  function fResize() { FW = faceCv.width = innerWidth; FH = faceCv.height = innerHeight; }
  addEventListener("resize", fResize); fResize();

  const parts = [];
  for (let i = 0; i < 90; i++) parts.push({ a: Math.random()*6.283, r: .55+Math.random()*.45, sp: .0015+Math.random()*.004, s: 1+Math.random()*2.2 });
  const traces = [];
  for (let i = 0; i < 24; i++) traces.push({ x: Math.random(), y: Math.random(), len: .06+Math.random()*.22, sp: .002+Math.random()*.01, dir: Math.random()<.5?-1:1, v: Math.random() });

  let speakLevel = 0, idleT = 0;
  setInterval(() => {
    if (faceStateVal === "idle" && Date.now() - (window.__lastFaceActivity||0) > 20000) {
      idleT += .016; // gentle breathing phase offset
    }
  }, 500);

  function loopFace() {
    ft += 1/60;
    fx.clearRect(0, 0, FW, FH);
    const cxp = FW/2, cyp = FH*0.34;
    const baseR = Math.min(FW,FH)*0.13;

    let R = baseR, glow = .35, speed = .7;
    if (faceStateVal === "listening") { R = baseR*(1.03+.05*Math.sin(ft*3)); glow=.6; speed=1.2; }
    else if (faceStateVal === "thinking") { R = baseR*(1+.04*Math.sin(ft*9)); glow=.5; speed=2.2; }
    else if (faceStateVal === "speaking") {
      speakLevel += ((.5+.5*Math.sin(ft*11))*(.35+.65*Math.random()) - speakLevel)*.35;
      R = baseR*(1+speakLevel*.22); glow=.75+.25*speakLevel; speed=2.4;
    } else { R = baseR*(1+.02*Math.sin((ft+idleT)*1.2)); glow=.32+.08*Math.sin(ft*.8); }

    const g = fx.createRadialGradient(cxp,cyp,R*.1,cxp,cyp,R*1.15);
    g.addColorStop(0,`rgba(170,215,255,${.85*glow+.12})`);
    g.addColorStop(.55,`rgba(110,168,254,${.5*glow})`);
    g.addColorStop(1,"rgba(10,20,40,0)");
    fx.beginPath(); fx.arc(cxp,cyp,R*1.15,0,7); fx.fillStyle=g; fx.fill();

    for (const [rr,lw,a] of [[1.0,2,.9],[1.18,1.2,.5],[1.42,1,.28]]) {
      fx.beginPath(); fx.arc(cxp,cyp,R*rr,ft*speed,ft*speed+Math.PI*1.6);
      fx.strokeStyle=`rgba(140,190,255,${a})`; fx.lineWidth=lw; fx.stroke();
    }

    for (const p of parts) {
      p.a += p.sp*speed;
      const rr = R*p.r*1.5;
      fx.beginPath();
      fx.arc(cxp+Math.cos(p.a)*rr, cyp+Math.sin(p.a)*rr*.92, p.s*(faceStateVal==="speaking"?1.5:1), 0, 7);
      fx.fillStyle=`rgba(150,200,255,${.22+.42*Math.abs(Math.sin(p.a*3))}`+")";
      fx.fill();
    }

    for (const tr of traces) {
      tr.v += tr.sp*(faceStateVal==="speaking"?3:speed);
      if (tr.v>1) tr.v=0;
      const x0=tr.x*FW, y0=tr.y*FH, x1=x0+tr.len*FW*tr.dir;
      fx.strokeStyle=`rgba(90,140,220,${.08+.22*tr.v})`; fx.lineWidth=tr.w;
      fx.strokeRect(Math.min(x0,x1),y0-1,Math.abs(x1-x0),2);
      fx.beginPath(); fx.arc(x0,y0,2.2,0,7);
      fx.fillStyle=`rgba(140,190,255,${.12+.45*tr.v})`; fx.fill();
    }

    requestAnimationFrame(loopFace);
  }
  window.faceState("idle");
  loopFace();

  /* keep orb alive on any user activity */
  ["pointerdown","keydown","input"].forEach(evtName =>
    addEventListener(evtName, () => { window.__lastFaceActivity = Date.now(); }));
}
