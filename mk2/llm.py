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

ROLES = ("primary", "fast", "reasoning", "voice")

# Phase 4.5: token-exhaustion cascade. Ranked by the user's FreeLLMAPI
# table (score / intelligence). When one model's quota dries up we slide
# down this list automatically instead of leaving the provider.
PRIMARY_LADDER = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-6",
]
VOICE_LADDER = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "ollama:qwen2.5:7b",
]
MODEL_LADDERS = {
    "fast": ["claude-haiku-4-5", "claude-sonnet-4-6", "ollama:qwen2.5:7b"],
    "reasoning": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"],
    "voice": VOICE_LADDER,
}

_PROXY_ERR_PATTERNS = (
    "the requested model is not currently available",
    "isn't available. please pick a claude model",
    "is not currently available. please select",
    "model not found",
    "unsupported model",
)

def _is_proxy_error(text: str) -> bool:
    if not text:
        return False
    low = text.lower().strip()
    return any(p in low for p in _PROXY_ERR_PATTERNS)


def estimate_tokens(messages: list[dict]) -> int:
    """Accurate token count estimation: ~1.3 tokens per whitespace word + framing overhead."""
    total_tokens = 0
    for m in (messages or []):
        content = str(m.get("content", ""))
        words = len(content.split())
        chars = len(content)
        # Average word in English is ~1.3 tokens; fallback to chars/3.7 for dense/code text
        est = max(int(words * 1.3), int(chars / 3.7))
        total_tokens += est + 4  # 4 tokens overhead per message role/framing
    return max(1, total_tokens)

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
        _cooldowns[f"{prov_name}:{model}"] = time.time() + (900 if hard else 15)


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


class CriticalEvaluationUnavailable(LLMUnavailable):
    """Raised when an execution-critical evaluation (safety, ethics, financial risk) fails."""
    pass


class LLMStreamStalled(LLMUnavailable):
    """Raised after partial deltas were yielded when the winning route
    died mid-generation. Carries the partial so callers can recover."""
    def __init__(self, partial: str):
        super().__init__(f"stream stalled after {len(partial)} chars")
        self.partial = partial


from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

_LLM_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mk2-llm")


def _hard_bounded(fn, seconds: float):
    """Run fn() with a HARD wall-clock limit using bounded thread pool.

    Socket timeouts don't bound slow/trickling generations; a stuck call can
    hold provider slots forever (this froze all of MK1's chat at one point).
    """
    fut = _LLM_POOL.submit(fn)
    try:
        return fut.result(timeout=max(1.0, float(seconds)))
    except FuturesTimeout:
        raise LLMUnavailable(f"timed out after {seconds:.0f}s")
    except Exception as exc:
        raise exc


def _providers(*args, **kwargs) -> list[dict]:
    """Ordered chain. Anthropic primary; FreeLLMAPI/OpenAI secondary;
    Gemini third; Ollama = offline fallback last (or first if role=='voice')."""
    role = args[0] if args else kwargs.get("role", "primary")
    provs = []
    ollama_prov = None
    if settings.ollama_base:
        ollama_prov = {
            "name": "ollama", "kind": "openai", "base": settings.ollama_base,
            "key": "", "default_model": settings.ollama_model or "qwen3:4b",
            "timeout_bias": 20,
        }

    if settings.anthropic_key:
        provs.append({
            "name": "anthropic", "kind": "anthropic",
            "base": settings.anthropic_base.rstrip("/"),
            "key": settings.anthropic_key,
            "default_model": settings.anthropic_model or "claude-haiku-4-5",
            "timeout_bias": 0,
        })
    if settings.openai_key:
        is_ollama = (settings.ollama_base
                     and settings.openai_base.rstrip("/") == settings.ollama_base.rstrip("/"))
        if not is_ollama:
            provs.append({
                "name": "freellmapi", "kind": "openai",
                "base": settings.openai_base, "key": settings.openai_key,
                "default_model": settings.openai_model or "claude-haiku-4-5",
                "timeout_bias": 0,
            })
    if settings.gemini_key:
        provs.append({
            "name": "gemini", "kind": "gemini", "base": "",
            "key": settings.gemini_key,
            "default_model": settings.gemini_text_model or "gemini-3.6-flash",
            "timeout_bias": 0,
        })
    if role != "voice" and ollama_prov:
        provs.append(ollama_prov)
    return provs


def _role_model(role: str) -> str:
    if role == "voice":
        return getattr(settings, "voice_model", "") or "claude-haiku-4-5"
    if role == "fast":
        return settings.fast_model or "claude-haiku-4-5"
    if role == "reasoning":
        return settings.reasoning_model or "claude-sonnet-4-6"
    return ""


def _attempts(role: str, model_override: str = ""):
    """Per-provider attempts. Anthropic and FreeLLMAPI expand into their ranked
    model ladder (top-to-bottom cascade on quota exhaustion); every other
    provider keeps a single model. Cooldown-skips exhausted models."""
    from .config import settings as s

    out: list[tuple[dict, str]] = []
    override = model_override.strip()
    try:
        prov_list = _providers(role)
    except TypeError:
        prov_list = _providers()
    for p in prov_list:
        if p["name"] in ("anthropic", "freellmapi", "openai"):
            models = [override] if override else _ladder(role)
        else:
            m = override or p["default_model"]
            # role overrides are FreeLLMAPI model names - never leak them
            # onto gemini/ollama endpoints
            if (p.get("kind") == "openai" and role in ("fast", "voice")
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
            if p["name"] in ("anthropic", "freellmapi", "openai"):
                for m in ([override] if override else _ladder(role)):
                    uniq.append((p, m))
                break
        if not uniq:
            raise LLMUnavailable("no language providers configured")
        return uniq
    # measured-speed reorder: within primary routes, routes that have
    # historically answered fastest come first (stable vs original rank).
    # An explicit user ladder/override keeps its exact order.
    if not override:
        pri = [u for u in uniq if u[0]["name"] in ("anthropic", "freellmapi", "openai", "gemini")]
        others = [u for u in uniq if u[0]["name"] not in ("anthropic", "freellmapi", "openai", "gemini")]
        pri.sort(key=_speed_rank)
        return pri + others
    return uniq


def _completion(base: str, key: str, payload: dict, timeout: int = 60) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if he.code == 401:
            raise RuntimeError(f"HTTP 401 Unauthorized from {base}: check API key. {body}") from he
        elif he.code == 429:
            raise RuntimeError(f"HTTP 429 Rate limited from {base}: {body}") from he
        elif he.code >= 500:
            raise RuntimeError(f"HTTP {he.code} Provider server error from {base}: {body}") from he
        raise RuntimeError(f"HTTP {he.code} Error from {base}: {body}") from he
    except urllib.error.URLError as ue:
        raise RuntimeError(f"Network error connecting to {base}: {ue.reason}") from ue


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


def _anthropic_chat(messages: list[dict], model: str, temperature: float, timeout: int = 60,
                    base: str = "", key: str = "") -> str:
    """Direct HTTP call to Anthropic Messages API (with thinking block filtering)."""
    key = key or settings.anthropic_key
    base = (base or settings.anthropic_base or "https://api.anthropic.com").rstrip("/")
    sys_prompts = [m["content"] for m in messages if m["role"] == "system"]
    chat_msgs = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": chat_msgs,
    }
    if sys_prompts:
        payload["system"] = "\n".join(sys_prompts)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/v1/messages",
        data=data, headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        content = result.get("content", [])
        raw_text = ""
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            if texts:
                raw_text = "\n".join(texts).strip()
            elif content and isinstance(content[0], dict):
                raw_text = (content[0].get("text", "") or "").strip()
        else:
            raw_text = (result.get("text", "") or "").strip()
        if _is_proxy_error(raw_text):
            raise LLMUnavailable(f"Proxy rejected model {model}: {raw_text[:60]}")
        return raw_text


def _anthropic_chat_stream(messages: list[dict], model: str, temperature: float, timeout: int = 75,
                           base: str = "", key: str = ""):
    """Direct SSE streaming from Anthropic Messages API."""
    key = key or settings.anthropic_key
    base = (base or settings.anthropic_base or "https://api.anthropic.com").rstrip("/")
    sys_prompts = [m["content"] for m in messages if m["role"] == "system"]
    chat_msgs = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "stream": True,
        "messages": chat_msgs,
    }
    if sys_prompts:
        payload["system"] = "\n".join(sys_prompts)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/v1/messages",
        data=data, headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if not d or d == "[DONE]":
                continue
            try:
                event = json.loads(d)
            except Exception:
                continue
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                delta_text = delta.get("text", "")
                if delta_text:
                    if _is_proxy_error(delta_text):
                        raise LLMUnavailable(f"Proxy rejected model {model}: {delta_text[:60]}")
                    yield delta_text


_gemini_client_instance = None
_gemini_lock = threading.Lock()


def _gemini_client():
    global _gemini_client_instance
    from google import genai
    with _gemini_lock:
        if _gemini_client_instance is None:
            _gemini_client_instance = genai.Client(api_key=settings.gemini_key)
        return _gemini_client_instance


def close_gemini_client() -> None:
    """Close cached Gemini client and release connection pools."""
    global _gemini_client_instance
    with _gemini_lock:
        if _gemini_client_instance is not None:
            try:
                if hasattr(_gemini_client_instance, "close"):
                    _gemini_client_instance.close()
            except Exception:
                pass
            _gemini_client_instance = None


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


def _offline_parse(text: str) -> str | None:
	"""Local command parser for when all LLM providers are unreachable."""
	import re
	from datetime import datetime

	t = (text or "").lower().strip(" .!?").replace("'", "")
	t = re.sub(r"\s+", " ", t)

	if t in ("time", "the time", "what time", "whats the time",
	"what is the time", "what time is it", "current time"):
		return datetime.now().strftime("It is %H:%M")
	if t in ("date", "today", "todays date", "what is the date",
	"whats the date", "what day is it", "what is today"):
		return datetime.now().strftime("Today is %A, %d %B %Y.")

	def _safe_eval_math(expr_str: str):
		import ast
		import operator
		clean = expr_str.strip().replace("^", "**")
		if not re.fullmatch(r"[\d\s+\-*/%.()]+", clean):
			return None
		bin_ops = {
			ast.Add: operator.add, ast.Sub: operator.sub,
			ast.Mult: operator.mul, ast.Div: operator.truediv,
			ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
			ast.Pow: operator.pow,
		}
		un_ops = {ast.USub: operator.neg, ast.UAdd: operator.pos}

		def _eval_node(node):
			if isinstance(node, ast.Expression):
				return _eval_node(node.body)
			elif isinstance(node, ast.Constant):
				if isinstance(node.value, (int, float)):
					return node.value
				raise ValueError("invalid constant")
			elif isinstance(node, ast.BinOp):
				op_cls = type(node.op)
				if op_cls not in bin_ops:
					raise ValueError("unsupported operator")
				left = _eval_node(node.left)
				right = _eval_node(node.right)
				if op_cls is ast.Pow and (abs(left) > 10000 or abs(right) > 100):
					raise ValueError("exponent too large")
				return bin_ops[op_cls](left, right)
			elif isinstance(node, ast.UnaryOp):
				op_cls = type(node.op)
				if op_cls not in un_ops:
					raise ValueError("unsupported unary operator")
				return un_ops[op_cls](_eval_node(node.operand))
			raise ValueError("unsupported AST node")

		try:
			tree = ast.parse(clean, mode="eval")
			return _eval_node(tree)
		except Exception:
			return None

	m = re.search(r"what is\s+([\d\s+\-*/^%.()]+)", t)
	if m:
		raw_expr = m.group(1).strip()
		res = _safe_eval_math(raw_expr)
		if res is not None:
			return f"{raw_expr} equals {res}."
	m = re.search(r"^([\d]+\s*[\+\-\*/^%]\s*[\d]+)\s*$", t)
	if m:
		raw_expr = m.group(1).strip()
		res = _safe_eval_math(raw_expr)
		if res is not None:
			return f"That equals {res}."

	if re.search(r"\bwho are you\b|\bwhat are you\b|\byour name\b", t):
		return ("I'm EVO. Language service is offline but I can handle "
		"time, date, math, weather, timers, and simple commands.")
	if re.search(r"\bhelp\b|\bwhat can you do\b", t):
		return ("Offline mode: time, date, basic math, weather, timers, "
		"and local app commands.")

	if re.search(r"weather", t):
		try:
			from . import tools
			m_city = re.search(r"in\s+([a-zA-Z\s]+)$", t)
			city = m_city.group(1).strip() if m_city else ""
			r = tools.call("weather_now", {"city": city})
			return r.get("speech") or "Weather unavailable offline."
		except Exception:
			return "Weather check failed offline."

	m = re.fullmatch(r"open\s+(?:up\s+)?(?:the\s+)?(\w+)", t)
	if m:
		try:
			from . import tools
			r = tools.call("open_app", {"target": m.group(1)})
			return r.get("speech") or f"Tried to open {m.group(1)}."
		except Exception:
			return "Can't open apps offline."

	m = re.search(r"(?:set|start)\s+(?:a\s+)?timer\s+(?:for\s+)?(.+)", t)
	if m:
		try:
			from . import tools
			r = tools.call("timer_set", {"duration": m.group(1).strip(), "label": "Timer"})
			return r.get("speech") or "Timer command sent."
		except Exception:
			return "Can't set timers offline."

	return None


def chat(messages: list[dict], temperature: float = 0.6, model: str = "",
         role: str = "primary", timeout: int = 60, bias: bool = True,
         max_providers: int | None = None, evaluation_mode: str = "advisory") -> str:
    errors = []
    attempts = _attempts(role, model)
    if max_providers:
        attempts = attempts[:max_providers]
    for prov, m in attempts:
        try:
            total = timeout + (prov["timeout_bias"] if bias else 0)
            if prov.get("kind") == "anthropic":
                text = _hard_bounded(
                    lambda: _anthropic_chat(
                        messages, m, temperature, timeout=total,
                        base=prov.get("base", ""), key=prov.get("key", "")
                    ),
                    total,
                )
            elif prov.get("kind") == "gemini":
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

    if evaluation_mode == "execution_critical":
        # Execution-critical safety/risk evaluations MUST FAIL-CLOSED.
        raise CriticalEvaluationUnavailable(
            f"Execution-critical evaluation failed across all providers: {'; '.join(errors) or 'none configured'}"
        )

    # Offline local fallback for basic commands (advisory mode only)
    user_prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_prompt = msg.get("content", "")
            break
    offline = _offline_parse(user_prompt)
    if offline:
        return offline

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

    # Avoid concurrency throttling on single-key proxies by racing only distinct endpoints
    seen_bases = set()
    distinct_pairs = []
    for prov, m in pairs:
        bk = (prov.get("base", ""), prov.get("key", ""))
        if bk in seen_bases:
            continue
        seen_bases.add(bk)
        distinct_pairs.append((prov, m))
    pairs = distinct_pairs or pairs[:1]

    q: _q.Queue = _q.Queue(maxsize=2000)
    stop_events = {i: threading.Event() for i in range(len(pairs))}

    def pump(prov, m, tag):
        started = time.time()
        first = True
        try:
            if prov.get("kind") == "anthropic":
                for delta in _anthropic_chat_stream(
                        messages, m, temperature, timeout=25,
                        base=prov.get("base", ""), key=prov.get("key", "")):
                    if stop_events[tag].is_set():
                        return
                    if delta:
                        q.put((tag, "delta", delta))
                        if first:
                            dt = time.time() - started
                            _record_ttft(prov["name"], m, dt)
                            first = False
            elif prov.get("kind") == "gemini":
                client = _hard_bounded(lambda: _gemini_client(), 10)
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
                    if stop_events[tag].is_set():
                        return
                    t = getattr(chunk, "text", "") or ""
                    if t:
                        q.put((tag, "delta", t))
                        if first:
                            dt = time.time() - started
                            _record_ttft(prov["name"], m, dt)
                            first = False
            else:
                headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
                if prov.get("key"):
                    headers["Authorization"] = f"Bearer {prov['key']}"
                req = urllib.request.Request(
                    prov["base"].rstrip("/") + "/chat/completions",
                    data=json.dumps({"model": m, "messages": messages,
                                     "temperature": temperature, "stream": True}).encode(),
                    headers=headers, method="POST",
                )
                with urllib.request.urlopen(req, timeout=25 + prov.get("timeout_bias", 0)) as resp:
                    for raw in resp:
                        if stop_events[tag].is_set():
                            return
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0].get("delta", {}).get("content", "")
                        except Exception:
                            continue
                        if delta:
                            q.put((tag, "delta", delta))
                            if first:
                                dt = time.time() - started
                                _record_ttft(prov["name"], m, dt)
                                first = False
            q.put((tag, "done", None))
        except Exception as exc:
            q.put((tag, "error", exc))

    threads = []
    tag_route: dict[int, tuple[dict, str]] = {}
    for i, (prov, m) in enumerate(pairs):
        tag_route[i] = (prov, m)
        t = threading.Thread(target=pump, args=(prov, m, i),
                             daemon=True, name=f"mk2-race-{i}")
        t.start()
        threads.append(t)
    deadline_first = time.time() + first_timeout
    total_deadline = time.time() + 90.0
    GAP_TIMEOUT = 18.0           # winner stalled mid-generation -> abort fast
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
                    for other_tag, ev in stop_events.items():
                        if other_tag != winner:
                            ev.set()      # abandon the loser immediately
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
        for ev in stop_events.values():
            ev.set()


def chat_stream(messages: list[dict], temperature: float = 0.6, model: str = "",
                role: str = "primary", timeout: int = 30):
    """Yield content deltas; per-provider native streaming with fallbacks."""
    errors = []
    attempts = _attempts(role, model)

    # Parallel race of the top-2 routes across all roles (voice, fast, primary).
    if os.environ.get("EVO_RACE", "1") == "1" and not model and len(attempts) >= 2:
        pairs = attempts[:2]
        info: dict = {}
        buf: list[str] = []
        race_timeout = 3.0 if role == "voice" else 4.5
        for delta in _race_stream(pairs, messages, temperature, first_timeout=race_timeout, info=info):
            buf.append(delta)
            yield delta
        if info.get("no_token"):
            pass                       # nobody spoke -> ladder takes over
        elif not info.get("stalled"):
            return
        else:
            # winner stalled mid-generation: caller resets + retries
            _penalize(pairs[0][0]["name"], pairs[0][1], "stalled", hard=True)
            raise LLMStreamStalled(partial="".join(buf))

    for idx, (prov, m) in enumerate(attempts):
        try:
            if prov.get("kind") == "anthropic":
                got = False

                def gen_anthropic():
                    for delta in _anthropic_chat_stream(
                            messages, m, temperature, timeout=timeout,
                            base=prov.get("base", ""), key=prov.get("key", "")):
                        yield delta

                first_t = 20.0 if idx == 0 else 15.0
                started = time.time()
                first = True
                for delta in _deadline_iter(gen_anthropic(), first_t, 25.0):
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

                first_t = 20.0 if idx == 0 else 15.0
                started = time.time()
                first = True
                for delta in _deadline_iter(gen(), first_t, 25.0):
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
            first_t = 20.0 if idx == 0 else 15.0
            started = time.time()
            first = True
            for delta in _deadline_iter(raw_stream(), first_t, 25.0):
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

    # Offline local fallback
    user_prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_prompt = msg.get("content", "")
            break
    offline = _offline_parse(user_prompt)
    if offline:
        yield offline
        return

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
