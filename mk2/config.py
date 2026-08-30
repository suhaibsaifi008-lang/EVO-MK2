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
    anthropic_key: str = field(default_factory=lambda: _s("ANTHROPIC_API_KEY") or _s("JARVIS_ANTHROPIC_KEY", ""))
    anthropic_base: str = field(default_factory=lambda: _s("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    anthropic_model: str = field(default_factory=lambda: _s("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    api_key: str = field(default_factory=lambda: _s("EVO_API_KEY", ""))
    openai_key: str = field(default_factory=lambda: _s("JARVIS_OPENAI_API_KEY") or _s("OPENAI_API_KEY", ""))
    openai_base: str = field(default_factory=lambda: _s("JARVIS_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _s("JARVIS_OPENAI_MODEL", "claude-sonnet-4-6"))
    ollama_base: str = field(default_factory=lambda: _s("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434/v1"))
    ollama_model: str = field(default_factory=lambda: _s("JARVIS_OLLAMA_MODEL", ""))
    fast_model: str = field(default_factory=lambda: _s("JARVIS_MODEL_FAST", "claude-haiku-4-5"))
    reasoning_model: str = field(default_factory=lambda: _s("JARVIS_MODEL_REASONING", "claude-sonnet-4-6"))

    # realtime voice
    gemini_key: str = field(default_factory=lambda: _s("GEMINI_API_KEY") or _s("JARVIS_GEMINI_KEY"))
    gemini_models: list[str] = field(default_factory=lambda: [
        m.strip() for m in _s(
            "JARVIS_GEMINI_MODELS",
            "gemini-2.5-flash-native-audio-latest,gemini-3.1-flash-live-preview",
        ).split(",") if m.strip()
    ])
    gemini_text_model: str = field(default_factory=lambda: _s(
        "JARVIS_GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite"))
    voice_engine: str = field(default_factory=lambda: _s("EVO_VOICE_ENGINE", "auto"))  # auto|live|local

    # phase 2: communication ------------------------------------------------
    telegram_token: str = field(default_factory=lambda: _s("TELEGRAM_BOT_TOKEN") or _s("JARVIS_TELEGRAM_TOKEN"))
    mail_user: str = field(default_factory=lambda: _s("MAIL_ADDRESS"))
    mail_password: str = field(default_factory=lambda: _s("MAIL_PASSWORD"))  # app password
    mail_imap_host: str = field(default_factory=lambda: _s("MAIL_IMAP_HOST"))
    mail_imap_port: int = field(default_factory=lambda: int(_s("MAIL_IMAP_PORT", "993")))
    mail_smtp_host: str = field(default_factory=lambda: _s("MAIL_SMTP_HOST"))
    mail_smtp_port: int = field(default_factory=lambda: int(_s("MAIL_SMTP_PORT", "465")))
    mail_send_enabled: bool = field(default_factory=lambda: _s("MAIL_SEND_ENABLED", "0") == "1")
    ntfy_topic: str = field(default_factory=lambda: _s("NTFY_TOPIC"))
    ntfy_server: str = field(default_factory=lambda: _s("NTFY_SERVER", "https://ntfy.sh"))

    # search engine used when EVO opens a search page for you
    search_engine: str = field(default_factory=lambda: _s("EVO_SEARCH_ENGINE", "google").lower())

    host: str = field(default_factory=lambda: _s("EVO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_s("EVO_PORT", "8421")))


SEARCH_TEMPLATES = {
    "google": "https://www.google.com/search?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
}


def search_url(query: str) -> str:
    """Build a search page URL from the configured engine (EVO_SEARCH_ENGINE:
    google|brave|duckduckgo|bing, or a full custom template via
    EVO_SEARCH_URL containing {q})."""
    custom = os.environ.get("EVO_SEARCH_URL", "").strip()
    template = custom or SEARCH_TEMPLATES.get(
        os.environ.get("EVO_SEARCH_ENGINE", "google").strip().lower(),
        SEARCH_TEMPLATES["google"])
    import urllib.parse

    return template.replace("{q}", urllib.parse.quote_plus(query.strip()))


settings = Settings()


def refresh() -> None:
    """Re-read env into settings (used after tests mutate environ)."""
    global settings
    settings = Settings()

