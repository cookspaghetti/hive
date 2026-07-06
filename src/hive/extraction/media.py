"""Tiered media extraction (fyp.txt L3, media handling).

Privacy-preserving by design: QR codes are decoded LOCALLY with pyzbar +
OpenCV (no external call). A vision model is used only as a fallback for
unstructured images, which routes content to an external service (PDPA
consideration, fyp.txt S10). Original tiered design — contrast with Hermes's
always-LLM vision approach (reference-mapping.md).
"""

from __future__ import annotations

from hive.state import HVI


def decode_qr(image_path: str, source_msg_id: int) -> list[HVI]:
    """Decode QR payloads locally; feed any URL/bank data back as HVIs.

    TODO(L3): pyzbar.decode(cv2.imread(...)); classify payload.
    """
    raise NotImplementedError


def describe_image(image_path: str) -> str:
    """Fallback vision-model description for unstructured images.

    TODO(L3): call external vision model; MINIMISE/ redact user's own data
    before sending.
    """
    raise NotImplementedError
