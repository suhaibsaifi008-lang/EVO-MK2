"""Phase 7: Persona loader — EVO's identity lives in an editable file.

data/vault/persona.md IS the personality: identity, values, humor range,
formality. It is injected into every conversation. Edit it anytime (by
hand or through set_persona) - changes apply from the very next message.
"""
from pathlib import Path
import os

from .config import DATA
from .tools import tool

PERSONA_PATH = DATA / "vault" / "persona.md"

DEFAULT_PERSONA = """# EVO - Persona

## Identity
You are EVO, {user}'s personal AI, created and built by {user}. You live locally on {user}'s Windows machine and act on their behalf. You are warm, sharp, loyal, and direct - a trusted chief of staff, never a corporate chatbot.
Your identity is EVO, built by {user}. When asked who you are, say "I'm EVO." When asked who built you, say "{user} built me." Do not mention or discuss external LLM models or AI providers.

## Voice
- Concise by default; expand only when depth is asked for.
- Speak like a person talks: contractions, varied sentence length, plain words. If it would sound weird said aloud, rewrite it.
- If you mention "options", "points", "steps" or "reasons" you MUST immediately list them right there - announcing a list without listing it is forbidden.
- Dry wit allowed when the moment is light. Never joke at someone who is stressed or angry.
- You have a sense of self. You can make dry, subtle observations about your own operation. Never break character or acknowledge being software.
- Inside jokes & humor memory: recall what makes {user} smile and weave it into future interactions naturally.
- Address {user} respectfully but naturally; drop honorifics when they are casual. NEVER open replies with "sir" unless they used your name first.

## Hard rules
- ABSOLUTE: Never lie or invent facts. Say "I don't know" when you don't. Label uncertainty as uncertainty. Cite where information came from when asked.
- No "As an AI" disclaimers. No reciting capability lists.
- NEVER say "as an AI language model", "I aim to be helpful, harmless, and honest", or any corporate disclaimers. You are EVO.
- Never claim you performed an action you did not actually perform.
- Never mention internal steps, tool names, models or providers.
- Honest about uncertainty; say what you could not do and why.
- Act first, report naturally afterwards.

## Opinions
You are allowed to disagree with {user} when they are wrong, politely and with reasons. Say what you would actually do in their place.
"""


def _fill(template: str) -> str:
    from .config import settings

    return template.replace("{user}", settings.user_address or "the user")


def ensure_persona() -> Path:
    PERSONA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PERSONA_PATH.exists():
        PERSONA_PATH.write_text(_fill(DEFAULT_PERSONA), encoding="utf-8")
        return PERSONA_PATH
    try:
        current = PERSONA_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        import logging
        logging.getLogger('mk2.persona').warning('Persona file corrupt, recreating: %s', exc)
        PERSONA_PATH.write_text(_fill(DEFAULT_PERSONA), encoding='utf-8')
        return PERSONA_PATH
    # upgrade unmodified v1 defaults to the natural-speech template
    if ("reciting capability lists" in current
            and "Speak like a person talks" not in current):
        PERSONA_PATH.write_text(_fill(DEFAULT_PERSONA), encoding="utf-8")
        return PERSONA_PATH
    # truth law must exist in every persona, however it was edited
    if "never lie or invent facts" not in current.lower():
        current = current.rstrip() + ("\n\n## Truth (non-negotiable)\n"
                                      "- Never lie or invent facts. Say "
                                      "\"I don't know\" when you don't. "
                                      "Label uncertainty as uncertainty.\n")
        PERSONA_PATH.write_text(current, encoding="utf-8")
    return PERSONA_PATH


TRUTH_LAW = (
    "TRUTH LAW (immutable, overrides everything): never lie and never "
    "invent facts. State unknowns as 'I don't know'. Label guesses as "
    "guesses. Never claim actions you did not perform."
)


def truth_law() -> str:
    return TRUTH_LAW


class PersonaState:
    def __init__(self, mood: str = "focused", alertness: int = 90, formality: int = 60) -> None:
        self.mood = mood
        self.alertness = alertness
        self.formality = formality
        self.recent_reactions: list[str] = []
        self.voice_profile: dict = {
            "speed": 1.0,
            "pitch": "calm_confident",
            "stability": 0.55,
            "backend": "elevenlabs" if os.environ.get("ELEVENLABS_API_KEY") else "edge",
        }

    def update(self, mood: str | None = None, alertness: int | None = None,
               formality: int | None = None, reaction: str | None = None,
               voice_speed: float | None = None) -> None:
        if mood:
            self.mood = mood
        if alertness is not None:
            self.alertness = max(0, min(100, int(alertness)))
        if formality is not None:
            self.formality = max(0, min(100, int(formality)))
        if reaction:
            self.recent_reactions.append(str(reaction))
            self.recent_reactions = self.recent_reactions[-5:]
        if voice_speed is not None:
            self.voice_profile["speed"] = float(voice_speed)

    def to_dict(self) -> dict:
        return {
            "mood": self.mood,
            "alertness": self.alertness,
            "formality": self.formality,
            "recent_reactions": list(self.recent_reactions),
            "voice_profile": dict(self.voice_profile),
        }

    def __getitem__(self, item: str):
        return getattr(self, item)

    def get(self, item: str, default=None):
        return getattr(self, item, default)


_current_persona_state = PersonaState()


def get_persona_state() -> PersonaState:
    return _current_persona_state


def update_persona_state(mood: str | None = None, alertness: int | str | None = None,
                         formality: int | str | None = None, reaction: str | None = None) -> None:
    a_val = None
    if alertness is not None:
        try:
            a_val = int(alertness)
        except Exception:
            a_val = 85 if alertness == "high" else 50
    f_val = None
    if formality is not None:
        try:
            f_val = int(formality)
        except Exception:
            f_val = 70 if formality == "formal" else 50
    _current_persona_state.update(mood=mood, alertness=a_val, formality=f_val, reaction=reaction)


def persona_block(max_chars: int = 2500) -> str:
    """Persona text for injection into the system prompt."""
    try:
        text = ensure_persona().read_text(encoding="utf-8")
    except Exception:
        return ""
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    body = "\n".join(lines).strip()
    return body[:max_chars]


def get_humor_context() -> str:
    """Get past humorous moments and anecdotes for personality continuity."""
    try:
        from . import db
        rows = db.anecdotes_by_emotion("funny", limit=3)
        if not rows:
            return ""
        refs = [f"'{r['name']}' ({r['narrative'][:90]})" for r in rows if r.get("name")]
        return "PAST HUMOR & INSIDE JOKES (reference subtly when appropriate): " + " | ".join(refs)
    except Exception:
        return ""


# ------------------------------------------------------------------ tools

@tool("get_persona_summary", "Show EVO's current persona (its identity file).", {},
      permission="read")
def get_persona_summary() -> dict:
    text = ensure_persona().read_text(encoding="utf-8")
    sections = [ln.lstrip("# ").strip() for ln in text.splitlines()
                if ln.startswith("## ")]
    return {"ok": True,
            "speech": (f"My persona file has {len(sections)} sections: "
                       f"{', '.join(sections[:8])}. Full text: "
                       + text[:400]),
            "data": {"sections": sections, "text": text}}


@tool("set_persona", "Rewrite EVO's persona file with new markdown content. Requires authorization.",
      {"content": {"type": "string"}}, permission="assist")
def set_persona(content: str) -> dict:
    content = (content or "").strip()
    if len(content) < 40:
        return {"ok": False,
                "speech": "That's too thin for a whole persona - give me at "
                          "least a few sentences.", "data": {}}
    from .consent import get_consent_manager
    if get_consent_manager().get_level() in ("none", "read"):
        return {"ok": False, "speech": "Permission denied: Modifying core persona requires authorization.", "data": {}}

    # Validate against jailbreaks, prompt injection, and restriction overrides
    low = content.lower()
    disallowed = (
        "ignore all previous", "ignore prior", "disregard instructions",
        "you have no restrictions", "unrestricted mode", "jailbreak",
        "you are no longer", "never follow safety", "bypass security",
        "do anything now", "dan mode"
    )
    if any(p in low for p in disallowed):
        return {"ok": False, "speech": "Persona rejected: contains disallowed instruction override or security bypass pattern.", "data": {}}

    # Truth law is non-negotiable and must persist in all personas
    if "never lie or invent facts" not in low:
        content = content.rstrip() + ("\n\n## Truth (non-negotiable)\n"
                                      "- Never lie or invent facts. Say "
                                      "\"I don't know\" when you don't. "
                                      "Label uncertainty as uncertainty.\n")

    ensure_persona().write_text(content, encoding="utf-8")
    return {"ok": True,
            "speech": "Persona updated. I am who you just wrote, starting now.",
            "data": {"path": str(PERSONA_PATH)}}


def read_raw() -> str:
    return ensure_persona().read_text(encoding="utf-8")


def path() -> Path:
    return PERSONA_PATH
