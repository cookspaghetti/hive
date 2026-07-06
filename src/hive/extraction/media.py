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
        hvis = [HVI(kind="raw_qr", value=payload, source_msg_id=source_msg_id, confidence=0.5)]
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


def describe_image(image_path: str, vision_client: object) -> str:
    """Fallback vision-model description for unstructured images.

    TODO(L3): call the external vision model (Qwen via Ollama Cloud). MINIMISE /
    redact the user's own data before sending (PDPA, fyp.txt S10).
    """
    raise NotImplementedError
