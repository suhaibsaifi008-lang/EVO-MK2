/* ===== EVO MK2 CINEMATIC LANDING PAGE INTERACTION SCRIPT ===== */

document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const cursorRing = document.getElementById("cursor-ring");
  const cursorDot = document.getElementById("cursor-dot");
  const slides = Array.from(document.querySelectorAll(".slide-page"));
  const railNodes = Array.from(document.querySelectorAll(".rail-node"));
  const railCarriage = document.getElementById("railCarriage");
  const navLinks = Array.from(document.querySelectorAll(".nav-link"));
  const toastContainer = document.getElementById("toastContainer");

  let currentSlide = 0;
  let isScrolling = false;
  const totalSlides = slides.length;

  // 1. Magnetic Custom Cursor
  let mouseX = window.innerWidth / 2;
  let mouseY = window.innerHeight / 2;
  let ringX = mouseX;
  let ringY = mouseY;

  window.addEventListener("mousemove", (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
    if (cursorDot) {
      cursorDot.style.left = `${mouseX}px`;
      cursorDot.style.top = `${mouseY}px`;
    }
  });

  function renderCursor() {
    ringX += (mouseX - ringX) * 0.18;
    ringY += (mouseY - ringY) * 0.18;
    if (cursorRing) {
      cursorRing.style.left = `${ringX}px`;
      cursorRing.style.top = `${ringY}px`;
    }
    requestAnimationFrame(renderCursor);
  }
  requestAnimationFrame(renderCursor);

  // Hover expansion on clickable items
  document.querySelectorAll("a, button, .rail-node, .btn-copy").forEach((el) => {
    el.addEventListener("mouseenter", () => {
      if (cursorRing) {
        cursorRing.style.width = "40px";
        cursorRing.style.height = "40px";
        cursorRing.style.borderColor = "var(--amber-light)";
      }
    });
    el.addEventListener("mouseleave", () => {
      if (cursorRing) {
        cursorRing.style.width = "22px";
        cursorRing.style.height = "22px";
        cursorRing.style.borderColor = "var(--amber)";
      }
    });
  });

  // 2. Slide Transition Engine
  function goToSlide(index) {
    if (index < 0 || index >= totalSlides) return;
    currentSlide = index;

    // Update slides
    slides.forEach((slide, i) => {
      slide.classList.toggle("active", i === index);
    });

    // Update Energy Rail nodes
    railNodes.forEach((node, i) => {
      node.classList.toggle("active", i === index);
    });

    // Update Top Navigation
    navLinks.forEach((link) => {
      const idx = parseInt(link.getAttribute("data-index"), 10);
      link.classList.toggle("active", idx === index);
    });

    // Move Energy Rail Carriage
    if (railCarriage && railNodes.length > 0) {
      const targetNode = railNodes[index];
      if (targetNode) {
        railCarriage.style.top = `${targetNode.offsetTop}px`;
      }
    }
  }

  // Energy Rail Node Clicks
  railNodes.forEach((node) => {
    node.addEventListener("click", () => {
      const idx = parseInt(node.getAttribute("data-index"), 10);
      goToSlide(idx);
    });
  });

  // Top Nav Link Clicks
  navLinks.forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const idx = parseInt(link.getAttribute("data-index"), 10);
      goToSlide(idx);
    });
  });

  // Scroll Indicator Click
  const scrollIndicator = document.getElementById("scrollIndicator");
  if (scrollIndicator) {
    scrollIndicator.addEventListener("click", () => {
      goToSlide(1);
    });
  }

  // Mouse Wheel Navigation (with debounce)
  window.addEventListener("wheel", (e) => {
    // If inside scrollable terminal, let terminal scroll
    if (e.target.closest(".terminal-scroll")) return;

    if (isScrolling) return;
    if (Math.abs(e.deltaY) < 30) return;

    isScrolling = true;
    if (e.deltaY > 0) {
      goToSlide(currentSlide + 1);
    } else {
      goToSlide(currentSlide - 1);
    }

    setTimeout(() => {
      isScrolling = false;
    }, 600);
  }, { passive: true });

  // Keyboard Arrow Navigation
  window.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "PageDown") {
      goToSlide(currentSlide + 1);
    } else if (e.key === "ArrowUp" || e.key === "PageUp") {
      goToSlide(currentSlide - 1);
    }
  });

  // 3. Interactive Wake Word ("EVO") Microphone Tester
  const startWakeTestBtn = document.getElementById("startWakeTestBtn");
  const heroTestWakeBtn = document.getElementById("heroTestWakeBtn");
  const testerCanvas = document.getElementById("testerCanvas");
  const testerStatusText = document.getElementById("testerStatusText");
  const testerFeedback = document.getElementById("testerFeedback");

  let audioCtx = null;
  let analyser = null;
  let micStream = null;
  let recognition = null;
  let isTestingMic = false;

  function playWakeChime() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15); // A5
      gain.gain.setValueAtTime(0.3, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.3);
    } catch {}
  }

  function triggerWakeDetection(phrase = "EVO") {
    playWakeChime();
    toast(`🎙 WAKE WORD DETECTED: "${phrase.toUpperCase()}"!`);

    if (testerStatusText) {
      testerStatusText.innerHTML = `
        <span class="tester-icon" style="color:#ffd080;transform:scale(1.3);transition:transform 0.2s">⚡</span>
        <span class="tester-state" style="color:var(--amber);font-weight:700">WAKING UP!</span>
      `;
    }
    if (testerFeedback) {
      testerFeedback.innerHTML = `<b style="color:#ffd080">✨ DETECTED "${phrase.toUpperCase()}"! EVO ACTIVATED.</b>`;
    }

    setTimeout(() => {
      if (isTestingMic && testerStatusText) {
        testerStatusText.innerHTML = `
          <span class="tester-icon">🎙</span>
          <span class="tester-state">LISTENING</span>
        `;
      }
    }, 2000);
  }

  async function startWakeWordTest() {
    if (isTestingMic) {
      stopWakeWordTest();
      return;
    }

    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const source = audioCtx.createMediaStreamSource(micStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);

      isTestingMic = true;
      if (startWakeTestBtn) startWakeTestBtn.querySelector("span").textContent = "STOP MIC TEST";
      if (testerStatusText) {
        testerStatusText.innerHTML = `
          <span class="tester-icon" style="color:var(--amber)">🎙</span>
          <span class="tester-state" style="color:var(--amber)">LISTENING</span>
        `;
      }
      if (testerFeedback) {
        testerFeedback.textContent = "Listening... say 'EVO' or 'Hey EVO' now!";
      }

      // Start Browser Speech Recognition if available
      const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (SpeechRec) {
        recognition = new SpeechRec();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = "en-US";

        recognition.onresult = (event) => {
          for (let i = event.resultIndex; i < event.results.length; ++i) {
            const transcript = event.results[i][0].transcript.toLowerCase();
            if (transcript.includes("evo") || transcript.includes("ever") || transcript.includes("jarvis")) {
              triggerWakeDetection("EVO");
            }
          }
        };

        recognition.onerror = () => {};
        recognition.onend = () => {
          if (isTestingMic) {
            try { recognition.start(); } catch {}
          }
        };

        try { recognition.start(); } catch {}
      }

      drawWaveform();
      toast("Microphone active — say 'EVO'!");
    } catch (err) {
      if (testerFeedback) {
        testerFeedback.textContent = "Mic access denied or unavailable in this browser.";
      }
      toast("Microphone permission required.");
    }
  }

  function stopWakeWordTest() {
    isTestingMic = false;
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
    }
    if (audioCtx) {
      audioCtx.close().catch(() => {});
    }
    if (recognition) {
      try { recognition.stop(); } catch {}
    }

    if (startWakeTestBtn) startWakeTestBtn.querySelector("span").textContent = "START MIC TEST";
    if (testerStatusText) {
      testerStatusText.innerHTML = `
        <span class="tester-icon">🎙</span>
        <span class="tester-state">READY</span>
      `;
    }
    if (testerFeedback) {
      testerFeedback.textContent = "Click to start mic, then say 'EVO'";
    }
  }

  if (startWakeTestBtn) {
    startWakeTestBtn.addEventListener("click", startWakeWordTest);
  }
  if (heroTestWakeBtn) {
    heroTestWakeBtn.addEventListener("click", () => {
      goToSlide(2);
      setTimeout(startWakeWordTest, 600);
    });
  }

  function drawWaveform() {
    if (!testerCanvas || !analyser) return;
    const ctx = testerCanvas.getContext("2d");
    const data = new Uint8Array(analyser.frequencyBinCount);

    function loop() {
      if (!isTestingMic) {
        ctx.clearRect(0, 0, 160, 160);
        return;
      }

      analyser.getByteFrequencyData(data);
      ctx.clearRect(0, 0, 160, 160);

      const cx = 80;
      const cy = 80;
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i];
      const avg = sum / data.length;

      // Base pulsing circle
      ctx.beginPath();
      ctx.arc(cx, cy, 60 + avg * 0.15, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(255, 170, 48, ${0.3 + avg * 0.005})`;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Energy spikes
      const numPoints = 24;
      ctx.beginPath();
      for (let i = 0; i < numPoints; i++) {
        const angle = (i / numPoints) * Math.PI * 2;
        const val = data[i % data.length] / 255;
        const r = 68 + val * 16;
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = "rgba(255, 170, 48, 0.7)";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  // 4. Copy-to-Clipboard Buttons
  document.querySelectorAll(".btn-copy").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-copy");
      if (text) {
        try {
          await navigator.clipboard.writeText(text);
          btn.textContent = "COPIED!";
          btn.style.background = "var(--amber)";
          btn.style.color = "#050505";
          setTimeout(() => {
            btn.textContent = "COPY";
            btn.style.background = "";
            btn.style.color = "";
          }, 1800);
          toast("Command copied to clipboard.");
        } catch {
          toast("Failed to copy.");
        }
      }
    });
  });

  // 5. Toast Notifications
  function toast(msg) {
    if (!toastContainer) return;
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = msg;
    toastContainer.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity 0.3s";
      setTimeout(() => t.remove(), 300);
    }, 2800);
  }

  // 6. Real-Time Kernel Telemetry Bridge
  async function pollKernelStatus() {
    const liveStatusText = document.getElementById("liveStatusText");
    const pingText = document.getElementById("pingText");
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const data = await res.json();
        if (liveStatusText) {
          liveStatusText.textContent = `KERNEL ONLINE · ${data.brain?.model || 'Sonnet 4.6'} · ${data.memory?.facts || 0} FACTS`;
        }
        if (pingText) {
          pingText.textContent = "127.0.0.1:8421 (ONLINE)";
        }
      }
    } catch {
      if (liveStatusText) {
        liveStatusText.textContent = "STANDALONE PREVIEW";
      }
    }
  }

  pollKernelStatus();
  setInterval(pollKernelStatus, 8000);
});
