"""Model router — role-based LLM access with provider failover + streaming.

Roles: primary | fast | reasoning. Providers: any OpenAI-compatible endpoint
first (OpenAI/OpenRouter/FreeLLMAPI/LM Studio), then Ollama. The rest of MK2
never touches a provider directly.
"""
import base64
import json
import os
import time
import re
import threading
import urllib.error
import urllib.request

from .config import settings

ROLES = ("primary", "fast", "reasoning")

# Phase 4.5: token-exhaustion cascade. Ranked by the user's FreeLLMAPI
# table (score / intelligence). When one model's quota dries up we slide
# down this list automatically instead of leaving the provider.
PRIMARY_LADDER = [
    "gpt-oss-120b",           # rank 1 - score 0.927
    "command-a-vision",       # rank 2 - 0.886, vision
    "gpt-5.4-mini",           # rank 3 - intelligence 100
    "inkling",                # rank 4 - intelligence 99
    "nemotron-3-ultra-550b",  # rank 12 - intelligence 99-100, 1M ctx
    "qwen3.6-27b",            # rank 6 - speed 99 workhorse
]
MODEL_LADDERS = {
    "fast": ["qwen3.6-27b", "gpt-oss-20b", "gpt-5.4-nano"],
    "reasoning": ["gpt-5.4-mini", "inkling", "nemotron-3-ultra-550b",
                  "nemotron-3-ultra", "o3"],
}

# cooldown so an exhausted/rate-limited model isn't retried every turn
_cooldowns: dict[str, float] = {}
_cd_lock = threading.Lock()

# measured first-token latency (EWMA) -> the router sticks to fast routes
_ttft: dict[str, float] = {}
_TTFT_ALPHA = 0.35
_DEFAULT_TTFT = 3.0


def _record_ttft(prov_name: str, model: str, seconds: float) -> None:
    key = f"{prov_name}:{model}"
    with _cd_lock:
        prev = _ttft.get(key)
        _ttft[key] = round(seconds if prev is None
                           else (1 - _TTFT_ALPHA) * prev + _TTFT_ALPHA * seconds, 3)


def _speed_rank(p: tuple[dict, str]) -> float:
    return _ttft.get(f"{p[0]['name']}:{p[1]}", _DEFAULT_TTFT)


def _penalize(prov_name: str, model: str, err: str, hard: bool | None = None) -> None:
    low = str(err).lower()
    if hard is None:
        hard = any(s in low for s in ("429", "402", "quota", "rate limit",
                                      "rate-limit", "exceeded", "insufficient",
                                      "token limit", "payment"))
    with _cd_lock:
        _cooldowns[f"{prov_name}:{model}"] = time.time() + (900 if hard else 60)


def penalize_stall(prov_name: str, model: str) -> None:
    """A route that choked MID-GENERATION gets benched long - it will
    likely choke again immediately."""
    _penalize(prov_name, model, "stalled", hard=True)


def _is_cooled(prov_name: str, model: str) -> bool:
    with _cd_lock:
        return _cooldowns.get(f"{prov_name}:{model}", 0) > time.time()


def _ladder(role: str) -> list[str]:
    """Ranked model chain for a role. Env override wins."""
    env = os.environ.get("JARVIS_MODEL_LADDER", "").strip()
    if env:
        lad = [m.strip() for m in env.split(",") if m.strip()]
    else:
        lad = list(MODEL_LADDERS.get(role, PRIMARY_LADDER))
    role_override = _role_model(role)
    if role_override:
        lad = [role_override] + [m for m in lad if m != role_override]
    return lad


class LLMUnavailable(RuntimeError):
    pass


class LLMStreamStalled(LLMUnavailable):
    """Raised after partial deltas were yielded when the winning route
    died mid-generation. Carries the partial so callers can recover."""
    def __init__(self, partial: str):
        super().__init__(f"stream stalled after {len(partial)} chars")
        self.partial = partial


def _hard_bounded(fn, seconds: float):
    """Run fn() with a HARD wall-clock limit.

    Socket timeouts don't bound slow/trickling generations; a stuck call can
    hold provider slots forever (this froze all of MK1's chat at one point).
    """
    box: dict = {}

    def runner() -> None:
        try:
            box["r"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["e"] = exc

    th = threading.Thread(target=runner, daemon=True, name="mk2-llm-call")
    th.start()
    th.join(max(1.0, float(seconds)))
    if "r" not in box:
        if "e" in box:
            raise box["e"]
        raise LLMUnavailable(f"timed out after {seconds:.0f}s")
    return box["r"]


def _providers() -> list[dict]:
    """Ordered chain. FreeLLMAPI (user's primary) first; Gemini second;
    Ollama = offline fallback last."""
    provs = []
    if settings.openai_key:
        is_ollama = (settings.ollama_base
                     and settings.openai_base.rstrip("/") == settings.ollama_base.rstrip("/"))
        if not is_ollama:
            provs.append({
                "name": "freellmapi", "kind": "openai",
                "base": settings.openai_base, "key": settings.openai_key,
                "default_model": settings.openai_model, "timeout_bias": 0,
            })
    if settings.gemini_key:
        provs.append({
            "name": "gemini", "kind": "gemini", "base": "",
            "key": settings.gemini_key,
            "default_model": settings.gemini_text_model,
            "timeout_bias": 0,
        })
    if settings.ollama_base:
        provs.append({
            "name": "ollama", "kind": "openai", "base": settings.ollama_base,
            "key": "", "default_model": settings.ollama_model or "qwen3:4b",
            "timeout_bias": 20,
        })
    return provs


def _role_model(role: str) -> str:
    if role == "fast":
        return settings.fast_model or ""
    if role == "reasoning":
        return settings.reasoning_model or ""
    return ""


def _attempts(role: str, model_override: str = ""):
    """Per-provider attempts. FreeLLMMAPI expands into its ranked model
    ladder (top-to-bottom cascade on quota exhaustion); every other
    provider keeps a single model. Cooldown-skips exhausted models."""
    from .config import settings as s

    out: list[tuple[dict, str]] = []
    override = model_override.strip()
    for p in _providers():
        if p["name"] == "freellmapi":
            models = [override] if override else _ladder(role)
        else:
            m = override or p["default_model"]
            # role overrides are FreeLLMAPI model names - never leak them
            # onto gemini/ollama endpoints
            if (p.get("kind") == "openai" and role == "fast"
                    and s.fast_model and not override):
                m = s.fast_model
            models = [m] if m else []
        for m in models:
            if m and not _is_cooled(p["name"], m):
                out.append((p, m))
    seen = set()
    uniq = []
    for p, m in out:
        k = (p["name"], m)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((p, m))
    if not uniq:
        # everything is cooling down - better to retry the ladder than die
        for p in _providers():
            if p["name"] == "freellmapi":
                for m in ([override] if override else _ladder(role)):
                    uniq.append((p, m))
                break
        if not uniq:
            raise LLMUnavailable("no language providers configured")
        return uniq
    # measured-speed reorder: within the freellmapi block, routes that have
    # historically answered fastest come first (stable vs original rank).
    # An explicit user ladder/override keeps its exact order.
    if not override:
        freellm = [u for u in uniq if u[0]["name"] == "freellmapi"]
        others = [u for u in uniq if u[0]["name"] != "freellmapi"]
        freellm.sort(key=_speed_rank)
        return freellm + others
    return uniq


def _completion(base: str, key: str, payload: dict, timeout: int = 60) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _deadline_iter(iterator, first_timeout: float = 20.0, gap_timeout: float = 25.0):
    """Wrap a delta iterator so stalls become fast failures."""
    import queue as _q

    q_out: _q.Queue = _q.Queue()
    _DONE = object()

    def pump():
        try:
            for item in iterator:
                q_out.put(item)
        except BaseException as exc:  # noqa: BLE001
            q_out.put(exc)
        q_out.put(_DONE)

    threading.Thread(target=pump, daemon=True).start()
    started = time.time()
    first = True
    while True:
        wait = first_timeout if first else gap_timeout
        try:
            item = q_out.get(timeout=wait)
        except _q.Empty:
            stage = "first token" if first else "gap"
            raise LLMUnavailable(f"stream stalled ({stage}, {wait:.0f}s)")
        first = False
        if item is _DONE:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


def _gemini_client():
    from google import genai

    return genai.Client(api_key=settings.gemini_key)


def _gemini_chat(client, messages: list[dict], model: str, temperature: float) -> str:
    from google.genai import types

    sys = [m["content"] for m in messages if m["role"] == "system"]
    rest = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    cfg = types.GenerateContentConfig(
        system_instruction="\n".join(sys) if sys else None,
        temperature=temperature,
    )
    resp = client.models.generate_content(
        model=model, contents=rest, config=cfg)
    return (resp.text or "").strip()


def chat(messages: list[dict], temperature: float = 0.6, model: str = "",
         role: str = "primary", timeout: int = 60, bias: bool = True,
         max_providers: int | None = None) -> str:
    errors = []
    attempts = _attempts(role, model)
    if max_providers:
        attempts = attempts[:max_providers]
    for prov, m in attempts:
        try:
            total = timeout + (prov["timeout_bias"] if bias else 0)
            if prov.get("kind") == "gemini":
                client = _hard_bounded(lambda: _gemini_client(), 10)
                text = _hard_bounded(
                    lambda: _gemini_chat(client, messages, m, temperature), total)
            else:
                data = _hard_bounded(
                    lambda: _completion(
                        prov["base"], prov["key"],
                        {"model": m, "messages": messages, "temperature": temperature},
                        timeout=total,
                    ),
                    total,
                )
                text = data["choices"][0]["message"]["content"].strip()
            if text:
                # strip reasoning-model <think> blocks
                text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
            if text:
                return text
            errors.append(f"{prov['name']}/{m}: empty")
            _penalize(prov["name"], m, "empty response")
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
            _penalize(prov["name"], m, str(exc))
    raise LLMUnavailable("; ".join(errors) or "nothing configured")


def chat_vision(prompt: str, image_bytes: bytes, timeout: int = 45,
                role: str = "vision") -> str:
    """Ask a vision-capable model about an image (PNG/JPEG bytes)."""
    errors = []
    attempts = [a for a in _attempts(role) if a[0].get("name") == "gemini"] or _attempts(role)
    for prov, m in attempts[:2]:
        try:
            if prov.get("kind") == "gemini":
                client = _hard_bounded(lambda: _gemini_client(), 10)

                def run_gemini():
                    from google.genai import types

                    part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                    resp = client.models.generate_content(
                        model=m,
                        contents=[prompt, part],
                        config=types.GenerateContentConfig(temperature=0.2),
                    )
                    return (resp.text or "").strip()

                text = _hard_bounded(run_gemini, timeout)
                if text:
                    return text
                errors.append(f"gemini/{m}: empty")
            else:
                b64 = base64.b64encode(image_bytes).decode("ascii")
                payload = {
                    "model": m,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                    "temperature": 0.2,
                    "max_tokens": 500,
                }
                data = _hard_bounded(
                    lambda: _completion(prov["base"], prov["key"], payload, timeout),
                    timeout,
                )
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    return text
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
    raise LLMUnavailable("; ".join(errors) or "no vision provider")


def _race_stream(pairs, messages: list[dict], temperature: float,
                 first_timeout: float = 4.0, info: dict | None = None):
    """Start the top routes simultaneously; whoever produces the first
    token wins, the loser is abandoned. Kills bad-routing latency.

    info (optional) reports back how the race ended:
      stalled=True  -> winner died mid-generation after partial output
                       (caller should retry on remaining routes)."""
    import queue as _q

    q: _q.Queue = _q.Queue()
    stop = threading.Event()

    def build_req(prov: dict, m: str):
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        if prov["key"]:
            headers["Authorization"] = f"Bearer {prov['key']}"
        return urllib.request.Request(
            prov["base"].rstrip("/") + "/chat/completions",
            data=json.dumps({"model": m, "messages": messages,
                             "temperature": temperature,
                             "stream": True}).encode(),
            headers=headers, method="POST")

    def pump(req, tag, prov, m):
        started = time.time()
        first = True
        try:
            with urllib.request.urlopen(
                    req, timeout=40 + prov.get("timeout_bias", 0)) as resp:
                for raw in resp:
                    if stop.is_set():
                        return
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get(
                            "delta", {}).get("content", "")
                    except Exception:
                        continue
                    if delta:
                        q.put((tag, "delta", delta))
                        if first:
                            dt = time.time() - started
                            _record_ttft(prov["name"], m, dt)
                            if dt > 6.0:
                                _penalize(prov["name"], m, "slow first token")
                            first = False
                q.put((tag, "done", None))
        except Exception as exc:
            q.put((tag, "error", exc))

    threads = []
    tag_route: dict[int, tuple[dict, str]] = {}
    for i, (prov, m) in enumerate(pairs):
        tag_route[i] = (prov, m)
        t = threading.Thread(target=pump,
                             args=(build_req(prov, m), i, prov, m),
                             daemon=True, name=f"mk2-race-{i}")
        t.start()
        threads.append(t)
    deadline_first = time.time() + first_timeout
    total_deadline = time.time() + 90.0
    GAP_TIMEOUT = 7.0            # winner stalled mid-generation -> abort fast
    winner = None
    finished: set[int] = set()
    last_activity = time.time()
    yielded = False
    try:
        while True:
            now = time.time()
            if now >= total_deadline:
                if info is not None and yielded:
                    info["stalled"] = True
                return
            if winner is not None and now - last_activity > GAP_TIMEOUT:
                if info is not None and yielded:
                    info["stalled"] = True
                    pm = tag_route.get(winner)
                    if pm:
                        penalize_stall(pm[0]["name"], pm[1])
                return
            if winner is None and now >= deadline_first:
                if info is not None:
                    info["no_token"] = True   # caller must NOT treat as done
                return            # nobody spoke in time -> caller falls back
            if winner is None:
                wait = max(0.05, min(1.0, deadline_first - now))
            else:
                wait = min(GAP_TIMEOUT, max(0.05, GAP_TIMEOUT - (now - last_activity)))
            try:
                item = q.get(timeout=wait)
            except _q.Empty:
                continue
            last_activity = time.time()
            tag, kind, payload = item
            if kind == "delta":
                if winner is None:
                    winner = tag
                    stop.set()      # abandon the loser immediately
                if tag == winner:
                    yielded = True
                    yield payload
            elif kind == "done":
                finished.add(tag)
                if winner is None:
                    winner = tag    # completed without visible deltas
                if tag == winner:
                    return
            elif kind == "error":
                finished.add(tag)
                prov_m = tag_route.get(tag)
                if prov_m:
                    _penalize(prov_m[0]["name"], prov_m[1], str(payload))
                if tag == winner:
                    if info is not None and yielded:
                        info["stalled"] = True
                    return          # partial already delivered; don't hang
                if winner is None and len(finished) >= len(pairs):
                    if info is not None:
                        info["no_token"] = True   # ladder must take over
                    return          # every racer died pre-token
    finally:
        stop.set()


def chat_stream(messages: list[dict], temperature: float = 0.6, model: str = "",
                role: str = "primary", timeout: int = 75):
    """Yield content deltas; per-provider native streaming with fallbacks."""
    errors = []
    attempts = _attempts(role, model)

    # Phase 7.5: parallel race of the top-2 openai routes (primary only).
    if os.environ.get("EVO_RACE", "1") == "1" and role == "primary" and not model:
        pairs = [(p, m) for p, m in attempts if p.get("kind") == "openai"][:2]
        if len(pairs) == 2:
            info: dict = {}
            buf: list[str] = []
            for delta in _race_stream(pairs, messages, temperature, info=info):
                buf.append(delta)
                yield delta
            if info.get("no_token"):
                pass                       # nobody spoke -> ladder takes over
            elif not info.get("stalled"):
                return
            else:
                # winner stalled mid-generation: caller resets + retries
                _penalize(pairs[0][0]["name"], pairs[0][1], "stalled",
                          hard=True)
                raise LLMStreamStalled(partial="".join(buf))

    for idx, (prov, m) in enumerate(attempts):
        try:
            if prov.get("kind") == "gemini":
                client = _hard_bounded(lambda: _gemini_client(), 10)
                got = False

                def gen():
                    from google.genai import types

                    sys = [mm["content"] for mm in messages if mm["role"] == "system"]
                    rest = [
                        {"role": "user" if mm["role"] == "user" else "model",
                         "parts": [{"text": mm["content"]}]}
                        for mm in messages if mm["role"] != "system"
                    ]
                    cfg = types.GenerateContentConfig(
                        system_instruction="\n".join(sys) if sys else None,
                        temperature=temperature,
                    )
                    for chunk in client.models.generate_content_stream(
                            model=m, contents=rest, config=cfg):
                        t = getattr(chunk, "text", "") or ""
                        if t:
                            yield t

                first_t = 5.0 if idx == 0 else 12.0
                started = time.time()
                first = True
                for delta in _deadline_iter(gen(), first_t, 15.0):
                    if first:
                        _record_ttft(prov["name"], m, time.time() - started)
                        first = False
                    got = True
                    yield delta
                if got:
                    return
                errors.append(f"{prov['name']}/{m}: empty stream")
                _penalize(prov["name"], m, "empty stream")
                continue

            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if prov["key"]:
                headers["Authorization"] = f"Bearer {prov['key']}"
            req = urllib.request.Request(
                prov["base"].rstrip("/") + "/chat/completions",
                data=json.dumps({"model": m, "messages": messages,
                                 "temperature": temperature, "stream": True}).encode(),
                headers=headers, method="POST",
            )
            def raw_stream():
                with urllib.request.urlopen(req, timeout=timeout + prov["timeout_bias"]) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            delta = json.loads(data)["choices"][0].get("delta", {}).get("content", "")
                        except Exception:
                            continue
                        if delta:
                            yield delta

            got = False
            first_t = 5.0 if idx == 0 else 12.0
            started = time.time()
            first = True
            for delta in _deadline_iter(raw_stream(), first_t, 15.0):
                if first:
                    _record_ttft(prov["name"], m, time.time() - started)
                    first = False
                got = True
                yield delta
            if got:
                return
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
            _penalize(prov["name"], m, str(exc))
    try:
        yield chat(messages, temperature=temperature, model=model)
        return
    except Exception as exc:
        errors.append(f"oneshot: {exc}")
    raise LLMUnavailable("; ".join(errors))


def diagnostics() -> dict:
    with _cd_lock:
        cooling = sorted(k for k, v in _cooldowns.items() if v > time.time())
    return {
        "roles": {r: _role_model(r) for r in ROLES},
        "providers": [{"name": p["name"], "base": p["base"]} for p in _providers()],
        "ladders": {"primary": PRIMARY_LADDER, **MODEL_LADDERS},
        "cooling_down": cooling,
        "measured_ttft_s": dict(sorted(_ttft.items(), key=lambda kv: kv[1])),
    }
