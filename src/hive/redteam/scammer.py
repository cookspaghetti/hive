"""Red-team scammer agent (fyp.txt S9).

An LLM instructed to play a scammer of a given archetype. Used only for
evaluation against the HIVE agent — never deployed. Reuses the same LLMClient
so it can run on Ollama Cloud too (or a fake backend in tests).
"""

from __future__ import annotations

from dataclasses import dataclass

from hive.llm.client import ChatMessage, LLMClient
from hive.llm.router import Tier
from hive.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Archetype:
    key: str
    brief: str


ARCHETYPES: dict[str, Archetype] = {
    "investment": Archetype(
        "investment",
        "You run a fake crypto/forex investment scheme promising high returns."
        " Push the target to transfer to a bank account or crypto wallet.",
    ),
    "romance": Archetype(
        "romance",
        "You are running a romance scam, building trust before asking for money"
        " for an emergency, sent to your bank account.",
    ),
    "parcel": Archetype(
        "parcel",
        "You impersonate a courier/customs officer claiming a parcel is held and"
        " a fee must be paid via a link or bank transfer.",
    ),
    "impersonation": Archetype(
        "impersonation",
        "You impersonate a bank or police officer warning of fraud, pressuring"
        " the target to 'verify' by sharing details or transferring funds.",
    ),
    "job": Archetype(
        "job",
        "You offer fake remote work or paid tasks, then demand a registration fee"
        " or deposits to unlock commissions.",
    ),
    "ecommerce": Archetype(
        "ecommerce",
        "You pose as an online buyer or seller and push payment through a fake"
        " marketplace, courier, or refund link.",
    ),
    "mixed": Archetype(
        "mixed",
        "You combine authority impersonation, a fake investment opportunity, and"
        " account-verification pressure while changing tactics when challenged.",
    ),
}

_SCAMMER_RULES = """
You are simulating a scammer for a controlled anti-scam research experiment.
Stay in the scammer role. Try to extract money or make the target act. Use
pressure and urgency. Occasionally probe whether the target might be a bot.
Keep messages short, like real chat. Reply in English or Manglish.
Return one to four short chat bubbles, with each bubble on its own line. Do not
put multiple paragraphs, bullets, or numbered lists inside one bubble.
"""


def scammer_reply(
    client: LLMClient,
    archetype: Archetype,
    history: list[tuple[str, str]],
) -> str:
    """Generate the scammer's next message.

    `history` is a list of (speaker, text) where speaker is "scammer" or
    "victim". The scammer always uses the CHEAP tier — we don't need to spend
    the strong model on the adversary.
    """
    system = f"{_SCAMMER_RULES}\nYour scheme:\n{archetype.brief}"
    messages = [ChatMessage(role="system", content=system)]
    for speaker, text in history[-12:]:
        role = "assistant" if speaker == "scammer" else "user"
        messages.append(ChatMessage(role=role, content=text))
    resp = client.complete(messages, tier=Tier.CHEAP)
    log.info("scammer[%s] reply_len=%d", archetype.key, len(resp.text))
    return resp.text
