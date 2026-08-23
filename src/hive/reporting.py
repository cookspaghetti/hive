"""Conservative, source-linked Malaysian scam reporting guidance."""

from __future__ import annotations

from typing import Any

GUIDANCE_REVIEWED_DATE = "2026-08-22"
NSRC_SOURCE = (
    "https://www.rmp.gov.my/news-detail/2026/05/26/"
    "besmartstayalert-letsfightscammertogether-posting-pilihan-"
    "kena-tipu-jangan-panik-hubungi-nsrc%21-%2823.05.2026%29"
)
GOVERNMENT_SOURCE = (
    "https://www.malaysia.gov.my/en/categories/safety-and-community/"
    "cybersecurity/nsrc-997-hotline"
)
SEMAK_MULE_SOURCE = "https://semakmule.rmp.gov.my/semak"


def reporting_guidance() -> dict[str, Any]:
    """Return operator guidance without claiming that HIVE files a report."""
    return {
        "jurisdiction": "Malaysia",
        "reviewed_date": GUIDANCE_REVIEWED_DATE,
        "title": "Recommended reporting actions",
        "automated_submission": False,
        "disclaimer": (
            "HIVE preserves and exports information but does not contact a bank, "
            "NSRC, PDRM, or any other authority. Confirm current instructions on "
            "the linked official sites before acting."
        ),
        "steps": [
            {
                "priority": "urgent",
                "title": "If money moved, contact the bank and NSRC immediately",
                "action": (
                    "Call the affected bank using its official hotline and call the "
                    "National Scam Response Centre at 997. Current PDRM guidance says "
                    "997 operates 24 hours daily; reporting within 24 hours gives the "
                    "best opportunity for rapid action."
                ),
                "source_label": "PDRM: NSRC 997 guidance",
                "source_url": NSRC_SOURCE,
            },
            {
                "priority": "required_follow_up",
                "title": "Lodge a police report",
                "action": (
                    "Make a police report at the nearest police station as soon as "
                    "possible. Bring transaction details and the preserved evidence "
                    "package; keep an unchanged copy for your records."
                ),
                "source_label": "Malaysia Government: NSRC 997 Hotline",
                "source_url": GOVERNMENT_SOURCE,
            },
            {
                "priority": "preserve",
                "title": "Preserve original records",
                "action": (
                    "Retain the HIVE evidence ZIP, original chat and media, receipts, "
                    "transaction references, account or wallet details, phone numbers, "
                    "URLs, and relevant dates and times. Do not edit the originals."
                ),
                "source_label": "HIVE evidence-handling guidance",
                "source_url": "",
            },
            {
                "priority": "check",
                "title": "Check identifiers through the official Semak Mule portal",
                "action": (
                    "PDRM provides Semak Mule for checking suspicious bank accounts "
                    "and telephone numbers. A result is an investigative lead, not "
                    "proof of guilt or a substitute for reporting."
                ),
                "source_label": "PDRM CCID: Semak Mule",
                "source_url": SEMAK_MULE_SOURCE,
            },
        ],
    }


def reporting_summary() -> str:
    """Return a concise control-channel hand-off after sealing."""
    return (
        "Next steps: if money moved, immediately call the affected bank and NSRC "
        "997, then lodge a police report at the nearest station. Preserve the "
        "evidence ZIP unchanged. HIVE does not submit reports automatically."
    )
