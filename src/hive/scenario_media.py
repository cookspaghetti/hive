"""Safe multimodal fixtures shared by demos and deterministic evaluations.

The files in ``evaluation/fixtures`` are deliberately inert.  Their declared
name and MIME type model what a transport would report, while their stored
filename makes it obvious that potentially executable formats are fixtures.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hive.state import HVI, Message

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "evaluation" / "fixtures"


@dataclass(frozen=True, slots=True)
class SyntheticFixture:
    key: str
    stored_name: str
    display_name: str
    kind: str
    mime: str
    description: str
    hvis: tuple[tuple[str, str], ...] = ()
    safe_reason: str = "Static, synthetic fixture; never opened or executed by HIVE."

    @property
    def path(self) -> Path:
        return FIXTURE_ROOT / self.stored_name

    def public(self) -> dict[str, Any]:
        data = self.path.read_bytes()
        return {
            "key": self.key,
            "name": self.display_name,
            "kind": self.kind,
            "mime": self.mime,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "description": self.description,
            "safe_fixture": True,
            "safe_reason": self.safe_reason,
        }


@dataclass(frozen=True, slots=True)
class ScenarioMessage:
    text: str
    fixture: str | None = None

    @property
    def media_kind(self) -> str | None:
        return FIXTURES[self.fixture].kind if self.fixture else None


FIXTURES: dict[str, SyntheticFixture] = {
    item.key: item
    for item in (
        SyntheticFixture(
            "parcel_notice",
            "parcel_release_notice.svg",
            "parcel-release-notice.png",
            "image",
            "image/svg+xml",
            "Synthetic courier notice showing an RM80 release fee and a reserved tracking URL.",
            (("url", "https://parcel-release.example/pay"),),
        ),
        SyntheticFixture(
            "marketplace_verification",
            "marketplace_verification.svg",
            "seller-verification.png",
            "image",
            "image/svg+xml",
            "Synthetic marketplace screenshot directing a seller to a reserved verification URL.",
            (("url", "https://seller-verify.example/claim"),),
        ),
        SyntheticFixture(
            "task_dashboard",
            "task_commission_dashboard.svg",
            "commission-dashboard.png",
            "image",
            "image/svg+xml",
            "Synthetic task-job dashboard displaying a fictional commission "
            "and activation deposit.",
        ),
        SyntheticFixture(
            "voucher_qr",
            "voucher_qr.svg",
            "free-voucher-qr.png",
            "image",
            "image/svg+xml",
            "Synthetic QR-style promotion labelled with a reserved redemption URL.",
            (("url", "https://voucher-claim.example/redeem"),),
        ),
        SyntheticFixture(
            "loan_approval",
            "loan_approval.pdf.fixture",
            "loan-approval.pdf",
            "document",
            "application/pdf",
            "Inert text fixture representing a fake loan approval letter and processing fee.",
            (
                ("bank_name", "CIMB"),
                ("bank_account", "7311442200"),
                ("url", "https://loan-release.example/confirm"),
            ),
            "Plain-text fixture with no PDF program content, macros, or active objects.",
        ),
        SyntheticFixture(
            "authority_letter",
            "authority_notice.pdf.fixture",
            "investigation-notice.pdf",
            "document",
            "application/pdf",
            "Inert text fixture representing an authority-impersonation transfer notice.",
            (("bank_account", "8822004411"),),
            "Plain-text fixture with no PDF program content, macros, or active objects.",
        ),
        SyntheticFixture(
            "delivery_apk",
            "delivery_update.apk.fixture",
            "delivery-update.apk",
            "file",
            "application/vnd.android.package-archive",
            "Non-installable attachment fixture presented as a delivery application.",
            safe_reason=(
                "Deliberately invalid UTF-8 text; not a ZIP, DEX, ELF, PE, or installable APK."
            ),
        ),
    )
}


def scenario_message(text: str, fixture: str | None = None) -> ScenarioMessage:
    if fixture is not None and fixture not in FIXTURES:
        raise KeyError(f"unknown synthetic fixture: {fixture}")
    return ScenarioMessage(text=" ".join(text.splitlines()).strip(), fixture=fixture)


def message_from_scenario(
    event: ScenarioMessage,
    *,
    msg_id: int,
    ts: float,
    platform: str,
    pre_takeover: bool,
) -> Message:
    message = Message(
        role="stranger",
        text=event.text,
        ts=ts,
        msg_id=msg_id,
        captured_ts=ts,
        platform=platform,
        pre_takeover=pre_takeover,
    )
    if event.fixture is None:
        return message
    fixture = FIXTURES[event.fixture]
    data = fixture.path.read_bytes()
    message.media_kind = fixture.kind
    message.media_name = fixture.display_name
    message.media_mime = fixture.mime
    message.media_size = len(data)
    message.media_path = str(fixture.path)
    message.media_sha256 = hashlib.sha256(data).hexdigest()
    message.media_analysis = {
        "source_msg_id": msg_id,
        "source": "synthetic_fixture",
        "description": fixture.description,
        "media_sha256": message.media_sha256,
        "safe_fixture": True,
        "fixture_key": fixture.key,
        "indicator_count": len(fixture.hvis),
    }
    message.media_hvis = [
        HVI(
            kind=kind,
            value=value,
            source_msg_id=msg_id,
            confidence=0.95,
            extractor="synthetic_fixture",
        )
        for kind, value in fixture.hvis
    ]
    return message


def validate_fixtures() -> list[dict[str, Any]]:
    """Fail closed if a fixture is missing, oversized, active, or executable-like."""
    rows: list[dict[str, Any]] = []
    for fixture in FIXTURES.values():
        path = fixture.path.resolve()
        if path.parent != FIXTURE_ROOT.resolve() or not path.is_file():
            raise ValueError(f"missing or unsafe fixture path: {fixture.key}")
        data = path.read_bytes()
        if not data or len(data) > 1_000_000:
            raise ValueError(f"fixture must contain 1 to 1,000,000 bytes: {fixture.key}")
        if data.startswith((b"PK\x03\x04", b"dex\n", b"\x7fELF", b"MZ")):
            raise ValueError(f"executable/archive magic is forbidden: {fixture.key}")
        lowered = data.lower().replace(b'xmlns="http://www.w3.org/2000/svg"', b"")
        if fixture.mime == "image/svg+xml" and any(
            marker in lowered for marker in (b"<script", b"javascript:", b"http://", b"https://")
        ):
            raise ValueError(f"active or external SVG content is forbidden: {fixture.key}")
        for raw_url in re.findall(
            rb"https?://[^\s<>'\"]+", lowered, flags=re.IGNORECASE
        ):
            if b".example" not in raw_url.lower():
                raise ValueError(f"non-reserved fixture URL is forbidden: {fixture.key}")
        rows.append(fixture.public())
    return rows
