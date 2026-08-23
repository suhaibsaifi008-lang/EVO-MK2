"""Model router — role-based LLM access with provider failover + streaming.

Roles: primary | fast | reasoning. Providers: any OpenAI-compatible endpoint
first (OpenAI/OpenRouter/FreeLLMAPI/LM Studio), then Ollama. The rest of MK2
never touches a provider directly.
"""
import json
import threading
import urllib.error
import urllib.request

from .config import settings

ROLES = ("primary", "fast", "reasoning")


class LLMUnavailable(RuntimeError):
    pass


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
    provs = []
    if settings.openai_key:
        provs.append({
            "name": "openai-compatible", "base": settings.openai_base,
            "key": settings.openai_key, "default_model": settings.openai_model,
            "timeout_bias": 0,
        })
    if settings.ollama_base and (settings.ollama_model or True):
        provs.append({
            "name": "ollama", "base": settings.ollama_base, "key": "",
            "default_model": settings.ollama_model or "qwen3:4b",
            "timeout_bias": 30,
        })
    return provs


def _role_model(role: str) -> str:
    if role == "fast":
        return settings.fast_model or settings.openai_model
    if role == "reasoning":
        return settings.reasoning_model or settings.openai_model
    return settings.openai_model


def _attempts(role: str, model_override: str = ""):
    primary = model_override or _role_model(role)
    out = []
    for p in _providers():
        out.append((p, primary))
    # final safety net per provider default
    for p in _providers():
        out.append((p, p["default_model"]))
    seen = set()
    uniq = []
    for p, m in out:
        k = (p["name"], m)
        if k in seen or not m:
            continue
        seen.add(k)
        uniq.append((p, m))
    if not uniq:
        raise LLMUnavailable("no language providers configured")
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
                return text
            errors.append(f"{prov['name']}/{m}: empty")
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
    raise LLMUnavailable("; ".join(errors) or "nothing configured")


def chat_stream(messages: list[dict], temperature: float = 0.6, model: str = "",
                role: str = "primary", timeout: int = 75):
    """Yield content deltas; falls back to one-shot when SSE unsupported."""
    errors = []
    for prov, m in _attempts(role, model):
        try:
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if prov["key"]:
                headers["Authorization"] = f"Bearer {prov['key']}"
            req = urllib.request.Request(
                prov["base"].rstrip("/") + "/chat/completions",
                data=json.dumps({"model": m, "messages": messages,
                                 "temperature": temperature, "stream": True}).encode(),
                headers=headers, method="POST",
            )
            got = False
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
                        got = True
                        yield delta
            if got:
                return
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
    try:
        yield chat(messages, temperature=temperature, model=model)
        return
    except Exception as exc:
        errors.append(f"oneshot: {exc}")
    raise LLMUnavailable("; ".join(errors))


def diagnostics() -> dict:
    return {
        "roles": {r: _role_model(r) for r in ROLES},
        "providers": [{"name": p["name"], "base": p["base"]} for p in _providers()],
    }
