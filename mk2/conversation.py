"""Natural conversation flow and state machine for EVO MK2.

Handles mid-turn interruptions ('never mind', 'stop'), continuation markers
('and?', 'tell me more'), topic switching, and implicit corrections.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .bus import bus

log = logging.getLogger("mk2.conversation")


class FlowState(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    CONTINUATION = "continuation"
    TOPIC_SWITCH = "topic_switch"
    CORRECTION = "correction"


_INTERRUPT_PATTERNS = re.compile(
    r"^(never mind|nevermind|stop|cancel|wait|hold on|shut up|quiet|abort|pause)[.!]?$",
    re.IGNORECASE,
)

_CONTINUATION_PATTERNS = re.compile(
    r"^(and\??|and then\??|what else\??|tell me more|continue|go on|keep going|more)[.!?]?$",
    re.IGNORECASE,
)

_TOPIC_SWITCH_PATTERNS = re.compile(
    r"^(by the way|on another note|actually|different question|new topic|switching gears|moving on)[,:]?\s*",
    re.IGNORECASE,
)

_CORRECTION_PATTERNS = re.compile(
    r"^(no,?\s+(i meant|i mean|actually)|not that,?|rather,?|instead of that,?)\s+",
    re.IGNORECASE,
)

CORRECTION_MARKERS = re.compile(
    r"\b(no,?\s*|actually,?\s*|not\s+that,?\s*|i\s+meant,?\s*|correction:?\s*)\b",
    re.IGNORECASE,
)


def detect_correction(user_text: str, last_reply: str = "") -> Optional[str]:
    """Detect if user is correcting the assistant's previous response."""
    clean = (user_text or "").strip()
    if not CORRECTION_MARKERS.search(clean):
        return None
    corrected = CORRECTION_MARKERS.sub("", clean).strip()
    return corrected if corrected else clean


@dataclass
class ConversationContext:
    state: FlowState = FlowState.IDLE
    last_user_turn: str = ""
    last_evo_reply: str = ""
    last_topic: str = ""
    turns_count: int = 0
    current_cancel_flag: bool = False
    last_interrupted_at: float = 0.0


import threading
_conv_lock = threading.Lock()
_conv_ctx = ConversationContext()


def get_conversation_context() -> ConversationContext:
    with _conv_lock:
        return _conv_ctx


def evaluate_turn_intent(text: str) -> dict:
    """Analyze incoming utterance for flow transitions (interruption, continuation, switch, correction)."""
    clean = (text or "").strip()
    if not clean:
        return {"intent": "empty", "transformed_text": ""}

    with _conv_lock:
        # 1. Interruption check
        if _INTERRUPT_PATTERNS.match(clean):
            _conv_ctx.state = FlowState.INTERRUPTED
            _conv_ctx.current_cancel_flag = True
            _conv_ctx.last_interrupted_at = time.time()
            bus.publish("conversation.interrupted", {"text": clean})
            log.info("Conversation interrupted by user utterance: '%s'", clean)
            return {
                "intent": "interruption",
                "immediate_reply": "Understood, standing by.",
                "transformed_text": clean,
            }

        # 2. Continuation check
        if _CONTINUATION_PATTERNS.match(clean):
            _conv_ctx.state = FlowState.CONTINUATION
            last_t = _conv_ctx.last_topic or _conv_ctx.last_user_turn
            transformed = f"Continue elaborating on: {last_t}" if last_t else "Please elaborate further."
            log.info("Continuation marker detected ('%s') -> '%s'", clean, transformed)
            return {
                "intent": "continuation",
                "immediate_reply": None,
                "transformed_text": transformed,
            }

        # 3. Topic switch check
        m_switch = _TOPIC_SWITCH_PATTERNS.match(clean)
        if m_switch:
            _conv_ctx.state = FlowState.TOPIC_SWITCH
            remainder = clean[m_switch.end():].strip()
            log.info("Topic switch detected. New topic: '%s'", remainder)
            return {
                "intent": "topic_switch",
                "immediate_reply": None,
                "transformed_text": remainder or clean,
            }

        # 4. Implicit correction check
        m_corr = _CORRECTION_PATTERNS.match(clean)
        if m_corr:
            _conv_ctx.state = FlowState.CORRECTION
            corr_details = clean[m_corr.end():].strip()
            transformed = f"Correction to previous answer: {corr_details}. Previous context was: {_conv_ctx.last_user_turn}"
            log.info("Correction detected -> '%s'", transformed)
            return {
                "intent": "correction",
                "immediate_reply": None,
                "transformed_text": transformed,
            }

        _conv_ctx.state = FlowState.IDLE
        _conv_ctx.current_cancel_flag = False
        return {"intent": "normal", "immediate_reply": None, "transformed_text": clean}


def record_turn_completion(user_text: str, reply: str) -> None:
    """Update conversation state machine upon turn completion."""
    with _conv_lock:
        _conv_ctx.last_user_turn = user_text
        _conv_ctx.last_evo_reply = reply
        _conv_ctx.turns_count += 1
    # Simple topic extraction: first 4-5 words or key subject
    words = [w for w in user_text.split() if len(w) > 3 and w.lower() not in ("what", "how", "when", "where", "tell", "show")]
    if words:
        _conv_ctx.last_topic = " ".join(words[:4])
