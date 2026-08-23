"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const log = $("log");
let currentController = null;

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
    $("engineTag").textContent = `${h.voice} · ${h.llm_online ? "LLM online" : "LLM offline"}`;
    $("conn").className = "dot online";
    const hr = new Date().getHours();
    const part = hr < 12 ? "morning" : hr < 18 ? "afternoon" : "evening";
    $("greet").textContent = `Good ${part}. ${h.name} MK2 online.`;
  } catch { $("conn").className = "dot offline"; }
}
setInterval(boot, 30000); boot();
