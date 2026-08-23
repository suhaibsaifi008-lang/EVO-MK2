from pathlib import Path

p = Path("mk2/ui/app.js")
s = p.read_text(encoding="utf-8")

# 1. listening state on PTT start
a1 = '$("pttBtn").classList.add("on");'
if a1 in s:
    s = s.replace(a1, a1 + '\n  faceState("listening");', 1)

# 2. thinking while transcribing
a2 = '$("sttPreview").textContent = "Transcribing...";'
if a2 in s:
    s = s.replace(a2, a2 + '\n    faceState("thinking");', 1)

# 3. thinking during stream
a3 = 'else if (ev.type === "thinking" && !acc) {'
if a3 in s:
    s = s.replace(a3, a3 + ' faceState("thinking");', 1)

# 4. speaking/idle around TTS playback
a4 = "          if (currentAudio) currentAudio.pause();"
if a4 in s:
    s = s.replace(a4, a4 + "\n          faceState(\"speaking\");", 1)
a5 = "          currentAudio.play().catch(() => {});"
if a5 in s:
    s = s.replace(
        a5,
        a5 + "\n          currentAudio.onended = () => faceState(\"idle\");"
             "\n          currentAudio.onpause = () => faceState(\"idle\");",
        1,
    )

# 5. done without voice → brief speaking then idle handled by timeout below
a6 = '        else if (ev.type === "done") { done = true; acc = ev.text || acc; setBody(body, acc, "evo"); }'
if a6 in s:
    s = s.replace(
        a6,
        a6 + "\n          if (!$('voiceOut')?.checked) setTimeout(() => faceState(\"idle\"), 1200);",
        1,
    )

p.write_text(s, encoding="utf-8")
print("state hooks wired")
