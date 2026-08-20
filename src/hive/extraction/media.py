"""Tiered media extraction (fyp.txt L3, media handling).

Privacy-preserving by design: QR codes are decoded LOCALLY with pyzbar +
OpenCV (no external call). A vision model is used only as a fallback for
unstructured images, which routes content to an external service (PDPA
consideration, fyp.txt S10). Original tiered design — contrast with Hermes's
always-LLM vision approach (reference-mapping.md).

`classify_payload` is pure and testable; `decode_qr` does the cv2/pyzbar work
(lazy imports so those native deps are only needed in production).
"""

from __future__ import annotations

from hive.extraction.regex_rules import extract_regex
from hive.logging_setup import get_logger
from hive.state import HVI

log = get_logger(__name__)


def classify_payload(payload: str, source_msg_id: int) -> list[HVI]:
    """Turn a decoded QR/text payload into HVIs.

    A URL payload becomes a url HVI (and will trigger the L4 sandbox); other
    payloads are run through the regex engine for bank/crypto/etc. Malaysian
    DuitNow QR strings often embed a merchant/account — captured as raw_qr.
    """
    payload = payload.strip()
    if not payload:
        return []
    hvis = extract_regex(payload, source_msg_id)
    if not hvis:
        # Unrecognised structured payload — keep it verbatim as evidence.
        hvis = [
            HVI(
                kind="raw_qr",
                value=payload,
                source_msg_id=source_msg_id,
                confidence=0.5,
                extractor="qr",
            )
        ]
    log.info("L3 media: qr payload classified into %d HVI(s)", len(hvis))
    return hvis


def decode_qr(image_path: str, source_msg_id: int) -> list[HVI]:
    """Decode QR codes in an image locally and classify their payloads."""
    import cv2  # lazy import
    from pyzbar.pyzbar import decode  # lazy import

    img = cv2.imread(image_path)
    if img is None:
        log.warning("L3 media: could not read image %s", image_path)
        return []
    hvis: list[HVI] = []
    for code in decode(img):
        payload = code.data.decode("utf-8", errors="replace")
        hvis.extend(classify_payload(payload, source_msg_id))
    log.info("L3 media: decoded %s -> %d HVI(s)", image_path, len(hvis))
    return hvis


_VISION_PROMPT = (
    "You are assisting an anti-scam analyst. Describe this image factually and "
    "concisely. Transcribe any visible text verbatim, and call out bank names, "
    "account numbers, phone numbers, URLs, or payment/transfer details if "
    "present. Do not speculate."
)


def _encode_data_url(image_path: str) -> str:
    """Read an image and return a base64 data URL (lazy imports)."""
    import base64
    import mimetypes

    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def describe_image(image_path: str, vision_client) -> str:
    """Fallback vision-model description for unstructured images (fyp.txt L3).

    Used only when local structured extraction (QR/regex) finds nothing. Sends
    the image to the external vision model (Qwen via Ollama Cloud) — a
    third-party disclosure, so apply it only to scammer-supplied media, never
    the user's own data (PDPA, fyp.txt S10). The returned text is fed back
    through the regex HVI pipeline by the caller.

    `vision_client` must expose `describe(data_url: str, prompt: str) -> str`.
    """
    data_url = _encode_data_url(image_path)
    text = vision_client.describe(data_url, _VISION_PROMPT)
    log.info("L3 media: vision model described image (%d chars)", len(text))
    return text


def extract_from_image(image_path: str, source_msg_id: int, vision_client=None) -> list[HVI]:
    """Tiered image extraction (fyp.txt L3): local QR first, vision fallback.

    Tries local QR decoding; if nothing structured is found and a vision client
    is supplied, describes the image and runs the description through the regex
    HVI pipeline. Keeps processing local/free by default.
    """
    hvis = decode_qr(image_path, source_msg_id)
    if hvis or vision_client is None:
        return hvis
    description = describe_image(image_path, vision_client)
    # A natural-language description is not a QR payload. Retain only concrete
    # structured indicators and never turn an arbitrary visual description into
    # a raw_qr hard signal.
    hvis = extract_regex(description, source_msg_id)
    for item in hvis:
        item.extractor = "vision"
    return hvis
