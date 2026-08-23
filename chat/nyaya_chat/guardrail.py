"""Two-tier intent classification guardrail for the chat agent.

Prevents non-legal messages (greetings, capability questions, off-topic
chitchat) from entering the full supervisor -> tools -> synthesis pipeline.
This eliminates 10-30s of wasted latency and produces appropriate responses
instead of the default "could not find a basis in the corpus" refusal.

Tier 1: Rule-based regex/keyword classifier (instant, 0ms). Catches the
most common patterns deterministically.

Tier 2: LLM intent classifier (~0.5-1s, only if Tier 1 returns "unknown").
Uses the already-loaded Nemotron model with a short classification prompt
and max_tokens=32. Fails open to LEGAL on timeout or error.

When the intent is not LEGAL, the server emits a canned SSE response
directly -- no supervisor call, no tool calls, no synthesis call.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import re
from typing import Any

from .config import Settings

log = logging.getLogger("nyaya_chat.guardrail")


class Intent(enum.Enum):
    """Classification of user message intent."""
    LEGAL = "legal"
    GREETING = "greeting"
    CAPABILITY = "capability"
    THANKS = "thanks"
    OFF_TOPIC = "off_topic"


# ---------------------------------------------------------------------------
# Canned responses (emitted as SSE token events, no LLM call).
# ---------------------------------------------------------------------------

_RESPONSES: dict[Intent, str] = {
    Intent.GREETING: (
        "Hello! I'm Nyaya, an assistant for Indian law. I can answer questions "
        "about the Constitution of India, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, "
        "commercial statutes, and landmark Supreme Court judgments. "
        "Ask me about any provision, section, or case -- I'll cite the exact source."
    ),
    Intent.CAPABILITY: (
        "I'm Nyaya, a retrieval-grounded assistant for Indian law. I can:\n\n"
        "- **Look up specific sections** of IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA\n"
        "- **Fetch Constitution articles** by number (e.g. Article 21, Article 14)\n"
        "- **Search semantically** across the entire corpus (e.g. \"good faith definition\")\n"
        "- **Compare provisions** across acts (e.g. IPC s.302 vs BNS s.103)\n"
        "- **Retrieve landmark Supreme Court judgments** by citation or case name\n"
        "- **Show cross-references** between provisions (e.g. what replaced IPC 302)\n\n"
        "Every answer is grounded in the actual legal text with inline citations. "
        'Try asking: *"What is IPC section 302?"* or *"What does Article 21 guarantee?"'
    ),
    Intent.THANKS: (
        "You're welcome! Feel free to ask another question about Indian law."
    ),
    Intent.OFF_TOPIC: (
        "I'm Nyaya, an assistant for Indian law. I can't help with that, but I can "
        "answer questions about the Constitution of India, IPC, CrPC, CPC, Evidence Act, "
        "BNS/BNSS/BSA 2023, commercial statutes, and landmark Supreme Court judgments. "
        'Try asking: *"What is IPC section 302?"* or *"What does Article 21 guarantee?"'
    ),
}


def get_canned_response(intent: Intent) -> str:
    """Return the canned response string for a non-legal intent."""
    return _RESPONSES.get(intent, _RESPONSES[Intent.OFF_TOPIC])


# ---------------------------------------------------------------------------
# Tier 1: Rule-based classifier (instant, 0ms).
# ---------------------------------------------------------------------------

# Greetings: short messages that are purely social. Anchored to avoid
# matching "hi, I have a question about IPC 302" (which should go to Tier 2).
_GREETING_RE = re.compile(
    r"^\s*(hi|hello+|hey+"
    r"|good\s+(morning|afternoon|evening|night)"
    r"|greetings|howdy|yo|sup|wassup"
    r"|hi\s+there|hello\s+there"
    r")\s*[!.?~]*\s*$",
    re.IGNORECASE,
)

# Capability: asking what the assistant can do or who it is.
_CAPABILITY_RE = re.compile(
    r"(what\s+(can|do)\s+you\s+(do|know|help|answer|handle)"
    r"|who\s+are\s+you|tell\s+me\s+about\s+yourself"
    r"|what\s+(is|are)\s+your\s+(capabilities?|features|scope|limit)"
    r"|how\s+(do|can)\s+(i|you)\s+(use|ask|interact)"
    r"|what\s+is\s+nyaya"
    r"|\bhelp\b\s*$"
    r")",
    re.IGNORECASE,
)

# Thanks: short expressions of gratitude.
_THANKS_RE = re.compile(
    r"^\s*(thanks|thank\s+you|thx|ty"
    r"|great|awesome|perfect|helpful|excellent|wonderful|amazing|brilliant"
    r"|much\s+obliged|appreciate\s+it|very\s+helpful"
    r"|well\s+done|good\s+job|nice\s+work"
    r")\s*[!.?]*\s*$",
    re.IGNORECASE,
)

# Off-topic: clearly non-legal topics. These are keyword-based (not anchored)
# because off-topic questions can be phrased many ways. The keywords are chosen
# to never appear in legal questions.
_OFF_TOPIC_KEYWORDS = [
    # Weather
    "weather", "temperature", "forecast", "rain", "snowfall",
    # Food/cooking
    "recipe", "cook", "baking", "ingredient", "biryani recipe", "how to make",
    # Entertainment
    "joke", "riddle", "poem", "song", "lyrics", "movie", "film", "netflix",
    "game", "gaming", "chess move", "sudoku", "crossword",
    # Tech/coding
    "python code", "javascript", "java program", "sql query", "debug",
    "stack overflow", "git push", "docker run", "npm install", "regex for",
    # Sports
    "cricket score", "football match", "ipl score", "world cup score",
    # Finance (non-legal)
    "stock price", "bitcoin", "crypto", "share market",
    # Health/medical
    "symptom", "diagnosis", "medicine for", "doctor", "fever", "headache",
    # Misc
    "horoscope", "zodiac", "dating", "relationship advice",
    "translate", "translate this",
    # Foreign law (clearly non-Indian)
    "us constitution", "american law", "eu law", "uk law", "chinese law",
    "first amendment", "bill of rights", "magna carta",
]


def _check_off_topic(message: str) -> bool:
    """Check if the message contains clear off-topic keywords."""
    lower = message.lower()
    for kw in _OFF_TOPIC_KEYWORDS:
        if kw in lower:
            return True
    return False


def classify_intent_tier1(message: str) -> Intent | None:
    """Rule-based intent classification. Returns Intent or None (unknown).

    This is deterministic and instant (<1ms). It catches the most common
    non-legal patterns. When it returns None, the caller should fall back
    to Tier 2 (LLM classification).
    """
    stripped = message.strip()
    if not stripped:
        return None

    # Check most specific patterns first (thanks is a subset of greeting
    # in some phrasings, so check thanks first).
    if _THANKS_RE.match(stripped):
        return Intent.THANKS

    if _GREETING_RE.match(stripped):
        return Intent.GREETING

    if _CAPABILITY_RE.search(stripped):
        # Make sure it's not a legal question that happens to contain
        # capability-like words. If the message also contains legal
        # keywords, treat it as legal (let the pipeline handle it).
        if not _has_legal_keywords(stripped):
            return Intent.CAPABILITY

    if _check_off_topic(stripped):
        return Intent.OFF_TOPIC

    return None


# Legal keywords that indicate the message is a legal question even if it
# contains capability/off-topic words. Used to avoid false positives.
_LEGAL_KEYWORDS = [
    "section", "article", "ipc", "crpc", "cpc", "bns", "bnss", "bsa",
    "evidence act", "constitution", "amendment", "judgment", "judgement",
    "supreme court", "high court", "petition", "writ", "provision",
    "act", "s.", "art.", "law", "legal", "penal", "criminal", "civil",
    "court", "bail", "fir", "arrest", "trial", "appeal", "defendant",
    "plaintiff", "murder", "theft", "fraud", "cheating", "defamation",
    "dowry", "rape", "assault", "contract", "property", "tax",
    "gst", "arbitration", "consumer", "companies act",
    "kesavananda", "basic structure", "fundamental right",
    "directive principle", "preamble",
]


def _has_legal_keywords(message: str) -> bool:
    """Check if the message contains legal keywords."""
    lower = message.lower()
    return any(kw in lower for kw in _LEGAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Tier 2: LLM intent classifier (fallback for ambiguous messages).
# ---------------------------------------------------------------------------

_CLASSIFIER_PROMPT = (
    "You are an intent classifier for Nyaya, an assistant for Indian law. "
    "Classify the user's message into exactly one of these categories:\n\n"
    "- legal: A question about Indian law (Constitution, IPC, CrPC, CPC, "
    "Evidence Act, BNS/BNSS/BSA, commercial statutes, Supreme Court judgments, "
    "or any legal concept/procedure/question)\n"
    "- greeting: A greeting or social pleasantry (hello, hi, good morning, "
    "how are you)\n"
    "- capability: Asking what you can do, who you are, or what topics you cover\n"
    "- off_topic: Anything not related to Indian law (weather, jokes, cooking, "
    "coding, foreign law, medical advice, etc.)\n\n"
    "Rules:\n"
    "1. If the message contains ANY legal question or concept, classify as 'legal'.\n"
    "2. When in doubt, classify as 'legal'.\n"
    "3. Respond with ONLY the category name (one word), nothing else.\n"
)


async def classify_intent_tier2(
    message: str,
    model: Any,
    settings: Settings,
) -> Intent:
    """LLM-based intent classification. Falls back to LEGAL on error/timeout.

    Uses the already-loaded Nemotron model with a short classification prompt
    and max_tokens=32 (enough for a single word). The call is wrapped in a
    timeout; if it fails, we fail open (treat as legal and run the pipeline).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        msgs = [
            SystemMessage(content=_CLASSIFIER_PROMPT),
            HumanMessage(content=message),
        ]
        result = await asyncio.wait_for(
            model.ainvoke(msgs),
            timeout=settings.guardrail_classifier_timeout_s,
        )
        raw = result.content if isinstance(result.content, str) else str(result.content)
        label = raw.strip().lower()

        # Parse the label -- the model might add extra text, so extract the
        # first word and match it.
        first_word = label.split()[0] if label.split() else ""
        for intent in Intent:
            if intent.value == first_word:
                log.info("guardrail tier2: classified as %s (raw=%r)", intent.value, raw[:50])
                return intent

        # If we can't parse the label, fail open.
        log.warning("guardrail tier2: unparseable response %r, failing open to LEGAL", raw[:80])
        return Intent.LEGAL

    except TimeoutError:
        log.warning("guardrail tier2: timeout after %ss, failing open to LEGAL",
                    settings.guardrail_classifier_timeout_s)
        return Intent.LEGAL
    except Exception as exc:
        log.warning("guardrail tier2: error %s, failing open to LEGAL", exc)
        return Intent.LEGAL


# ---------------------------------------------------------------------------
# Public API: classify_intent (two-tier).
# ---------------------------------------------------------------------------

async def classify_intent(
    message: str,
    model: Any,
    settings: Settings,
) -> Intent:
    """Two-tier intent classification.

    Tier 1: instant regex/keyword matching. If it returns a non-None
    intent, that's the answer (no LLM call needed).

    Tier 2: if Tier 1 returns None (unknown), make a lightweight LLM
    classification call. Falls open to LEGAL on error/timeout.

    When ``settings.guardrail_enabled`` is False, always returns LEGAL
    (the guardrail is bypassed entirely).
    """
    if not settings.guardrail_enabled:
        return Intent.LEGAL

    # Tier 1: rule-based (instant)
    tier1 = classify_intent_tier1(message)
    if tier1 is not None:
        log.info("guardrail tier1: classified as %s", tier1.value)
        return tier1

    # Tier 2: LLM-based (only for messages Tier 1 couldn't classify)
    return await classify_intent_tier2(message, model, settings)
