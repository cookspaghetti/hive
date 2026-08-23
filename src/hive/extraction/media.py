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

from dataclasses import dataclass
from typing import Protocol

from hive.extraction.regex_rules import extract_regex
from hive.logging_setup import get_logger
from hive.state import HVI

log = get_logger(__name__)


class VisionDescriber(Protocol):
    def describe(self, data_url: str, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class MediaIntelligence:
    source: str
    description: str
    hvis: tuple[HVI, ...]


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


def extract_text_local(image_path: str) -> str:
    """OCR an image locally; an unavailable OCR runtime is a clean miss."""
    try:
        import cv2
        import pytesseract

        image = cv2.imread(image_path)
        if image is None:
            return ""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        available = set(pytesseract.get_languages(config=""))
        languages = "+".join(item for item in ("eng", "chi_sim") if item in available)
        return str(
            pytesseract.image_to_string(
                gray,
                lang=languages or None,
                config="--psm 6",
            )
            or ""
        ).strip()
    except Exception as exc:  # noqa: BLE001 - optional OCR must never block processing
        log.warning("L3 media: local OCR unavailable for %s (%s)", image_path, exc)
        return ""


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


def describe_image(image_path: str, vision_client: VisionDescriber) -> str:
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


def extract_from_image(
    image_path: str,
    source_msg_id: int,
    vision_client: VisionDescriber | None = None,
) -> list[HVI]:
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


def analyze_image(
    image_path: str,
    source_msg_id: int,
    vision_client: VisionDescriber | None = None,
) -> MediaIntelligence:
    """Return one provenance-labelled local/vision analysis for a live image."""
    qr_hvis = decode_qr(image_path, source_msg_id)
    if qr_hvis:
        return MediaIntelligence("local_qr", "QR payload decoded locally.", tuple(qr_hvis))

    local_text = extract_text_local(image_path)
    if local_text:
        ocr_hvis = extract_regex(local_text, source_msg_id)
        for item in ocr_hvis:
            item.extractor = "ocr"
        if ocr_hvis or vision_client is None:
            return MediaIntelligence("local_ocr", local_text, tuple(ocr_hvis))

    if vision_client is None:
        return MediaIntelligence("none", local_text, ())
    description = describe_image(image_path, vision_client)
    vision_hvis = extract_regex(description, source_msg_id)
    for item in vision_hvis:
        item.extractor = "vision"
    return MediaIntelligence("vision", description, tuple(vision_hvis))
