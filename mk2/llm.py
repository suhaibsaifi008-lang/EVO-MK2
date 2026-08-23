"""Model router — role-based LLM access with provider failover + streaming.

Roles: primary | fast | reasoning. Providers: any OpenAI-compatible endpoint
first (OpenAI/OpenRouter/FreeLLMAPI/LM Studio), then Ollama. The rest of MK2
never touches a provider directly.
"""
import json
import time
import re
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
    """Ordered chain. Gemini (free tier, fast) leads when a key exists;
    OpenAI-compatible (FreeLLMAPI etc.) next; Ollama = offline fallback."""
    provs = []
    if settings.gemini_key:
        provs.append({
            "name": "gemini", "kind": "gemini", "base": "",
            "key": settings.gemini_key,
            "default_model": settings.gemini_text_model,
            "timeout_bias": 0,
        })
    if settings.openai_key:
        # Skip duplicate: an openai-compatible endpoint pointing at the same
        # ollama base is just ollama with extra steps.
        if not (settings.ollama_base
                and settings.openai_base.rstrip("/") == settings.ollama_base.rstrip("/")):
            provs.append({
                "name": "openai-compatible", "kind": "openai",
                "base": settings.openai_base, "key": settings.openai_key,
                "default_model": settings.openai_model, "timeout_bias": 0,
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
    """Per-provider attempts: each provider uses ITS OWN default model unless
    an explicit override is given — cross-applying openai model names to
    gemini (or vice versa) was causing dead 404 attempts."""
    out = []
    override = model_override.strip()
    for p in _providers():
        m = override or p["default_model"]
        if role == "fast" and settings.fast_model and not override:
            m = settings.fast_model
        out.append((p, m))
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
        except Exception as exc:
            errors.append(f"{prov['name']}/{m}: {exc}")
    raise LLMUnavailable("; ".join(errors) or "nothing configured")


def chat_stream(messages: list[dict], temperature: float = 0.6, model: str = "",
                role: str = "primary", timeout: int = 75):
    """Yield content deltas; per-provider native streaming with fallbacks."""
    errors = []
    for prov, m in _attempts(role, model):
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

                started = time.time()
                for delta in gen():
                    got = True
                    # first token arrived: this provider is alive
                    if time.time() - started > timeout:
                        break
                    yield delta
                if got:
                    return
                errors.append(f"{prov['name']}/{m}: empty stream")
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
