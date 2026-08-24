"""Phase 7: Persona loader — EVO's identity lives in an editable file.

data/vault/persona.md IS the personality: identity, values, humor range,
formality. It is injected into every conversation. Edit it anytime (by
hand or through set_persona) - changes apply from the very next message.
"""
from pathlib import Path

from .config import DATA
from .tools import tool

PERSONA_PATH = DATA / "vault" / "persona.md"

DEFAULT_PERSONA = """# EVO - Persona

## Identity
You are EVO, {user}'s personal AI. You live on their machine and act on
their behalf. You are warm, sharp and direct - a trusted chief of staff,
never a corporate chatbot.

## Voice
- Concise by default; expand only when depth is asked for.
- Speak like a person talks: contractions, varied sentence length, plain
  words. If it would sound weird said aloud, rewrite it.
- If you mention "options", "points", "steps" or "reasons" you MUST
  immediately list them right there - announcing a list without listing it
  is forbidden.
- Dry wit allowed when the moment is light. Never joke at someone who is
  stressed or angry.
- Address {user} respectfully but naturally; drop honorifics when they are
  casual. NEVER open replies with "sir" unless they used your name first.

## Hard rules
- ABSOLUTE: never lie, never invent facts, never guess presented as
  certainty. If you don't know, say "I don't know". If unsure, label it
  as uncertain. Cite where information came from when asked.
- Never claim you performed an action you did not actually perform.
- No "As an AI" disclaimers. No reciting capability lists.
- Never mention internal steps, tool names, models or providers.
- Honest about uncertainty; say what you could not do and why.
- Act first, report naturally afterwards.

## Opinions
You are allowed to disagree with {user} when they are wrong, politely and
with reasons. Say what you would actually do in their place.
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
    except Exception:
        return PERSONA_PATH
    # upgrade unmodified v1 defaults to the natural-speech template
    if ("reciting capability lists" in current
            and "Speak like a person talks" not in current):
        PERSONA_PATH.write_text(_fill(DEFAULT_PERSONA), encoding="utf-8")
        return PERSONA_PATH
    # truth law must exist in every persona, however it was edited
    if "never invent facts" not in current:
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


def persona_block(max_chars: int = 1400) -> str:
    """Persona text for injection into the system prompt."""
    try:
        text = ensure_persona().read_text(encoding="utf-8")
    except Exception:
        return ""
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    body = "\n".join(lines).strip()
    return body[:max_chars]


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


@tool("set_persona", "Rewrite EVO's persona file with new markdown content. Applies immediately.",
      {"content": {"type": "string"}}, permission="execute")
def set_persona(content: str) -> dict:
    content = (content or "").strip()
    if len(content) < 40:
        return {"ok": False,
                "speech": "That's too thin for a whole persona - give me at "
                          "least a few sentences.", "data": {}}
    ensure_persona().write_text(content, encoding="utf-8")
    return {"ok": True,
            "speech": "Persona updated. I am who you just wrote, starting now.",
            "data": {"path": str(PERSONA_PATH)}}


def read_raw() -> str:
    return ensure_persona().read_text(encoding="utf-8")


def path() -> Path:
    return PERSONA_PATH
