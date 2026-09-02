"""Post-generation persona validator for EVO MK2.

Enforces strict JARVIS persona integrity on all LLM outputs before they are
spoken or displayed. Strips corporate AI tropes, model leaks, and hedging.
"""
from __future__ import annotations

import logging
import re
from typing import Tuple

from . import llm

log = logging.getLogger("mk2.validator")

PERSONA_RULES = [
    ("no_as_an_ai", r"\b(As an AI|As an assistant|I'm an AI|I am an AI|as a large language model|language model)\b"),
    ("no_capability_list", r"(I can help you with:|I can assist you with:|My capabilities include:)"),
    ("no_internal_refs", r"\b(OpenAI|Anthropic|Claude|GPT-4|Gemini Pro|LLM model)\b"),
    ("no_hedging", r"^(I think|I believe|It seems to me that|Perhaps|Maybe),?\s+"),
]


def validate_response(text: str, timeout: float = 4.0, check_cancel = None) -> Tuple[bool, str]:
    """Check text against persona rules; rewrite naturally if violations exist."""
    t = (text or "").strip()
    if not t:
        return True, ""

    violations = []
    for rule_name, pattern in PERSONA_RULES:
        if re.search(pattern, t, re.IGNORECASE):
            violations.append(rule_name)

    if not violations:
        return True, t

    if check_cancel and check_cancel():
        return True, t

    log.info("Persona violations detected: %s. Rewriting response...", violations)

    # Fast correction prompt
    prompt = (
        f"Original text:\n{t}\n\n"
        f"Violations detected: {', '.join(violations)}\n\n"
        "Rewrite this response to sound like EVO:\n"
        "- Direct, concise, confident, and natural.\n"
        "- NEVER mention being an AI or language model.\n"
        "- NEVER mention model names or corporate disclaimers.\n"
        "- Preserve the actual factual answer or message."
    )

    try:
        fixed = llm.chat([
            {"role": "system", "content": "You are a persona editor transforming assistant replies into crisp EVO dialogue."},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.2, timeout=timeout)

        cleaned = fixed.strip()
        if cleaned:
            return True, cleaned
    except Exception as exc:
        log.warning("Response validator rewrite failed: %s", exc)

    # Simple regex scrub fallback
    scrubbed = t
    for _, pattern in PERSONA_RULES:
        scrubbed = re.sub(pattern, "", scrubbed, flags=re.IGNORECASE)
    return True, scrubbed.strip()
