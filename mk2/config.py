"""EVO MK2 configuration: env-first, typed, zero magic.

Secrets live in .env (git-ignored) or the system credential store later.
Everything else is env-overridable with sane defaults.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UI_DIR = Path(__file__).resolve().parent / "ui"


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()


def _s(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass
class Settings:
    name: str = field(default_factory=lambda: _s("EVO_NAME", "EVO"))
    user_address: str = field(default_factory=lambda: _s("EVO_USER_ADDRESS", "sir"))

    # model providers (ordered failover)
    openai_key: str = field(default_factory=lambda: _s("JARVIS_OPENAI_API_KEY"))
    openai_base: str = field(default_factory=lambda: _s("JARVIS_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _s("JARVIS_OPENAI_MODEL", "gpt-4o-mini"))
    ollama_base: str = field(default_factory=lambda: _s("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1"))
    ollama_model: str = field(default_factory=lambda: _s("JARVIS_OLLAMA_MODEL", ""))
    fast_model: str = field(default_factory=lambda: _s("JARVIS_MODEL_FAST"))
    reasoning_model: str = field(default_factory=lambda: _s("JARVIS_MODEL_REASONING"))

    # realtime voice
    gemini_key: str = field(default_factory=lambda: _s("GEMINI_API_KEY") or _s("JARVIS_GEMINI_KEY"))
    gemini_models: list[str] = field(default_factory=lambda: [
        m.strip() for m in _s(
            "JARVIS_GEMINI_MODELS",
            "gemini-2.5-flash-native-audio-latest,gemini-3.1-flash-live-preview",
        ).split(",") if m.strip()
    ])
    voice_engine: str = field(default_factory=lambda: _s("EVO_VOICE_ENGINE", "auto"))  # auto|live|local

    host: str = field(default_factory=lambda: _s("EVO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_s("EVO_PORT", "8421")))


settings = Settings()


def refresh() -> None:
    """Re-read env into settings (used after tests mutate environ)."""
    global settings
    settings = Settings()
