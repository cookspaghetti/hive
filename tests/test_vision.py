"""L3 vision-fallback tests (fyp.txt L3), fully offline.

describe_image / extract_from_image with a fake vision client (no network,
no model). Verifies the tiered flow: local QR first, vision fallback only when
structured extraction finds nothing.
"""

from unittest import mock

from hive.extraction.media import describe_image, extract_from_image


class FakeVision:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def describe(self, data_url, prompt):
        self.calls.append((data_url, prompt))
        return self.text


def test_describe_image_encodes_and_calls_vision(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    vc = FakeVision("Fake bank login page, account 1234567890")
    out = describe_image(str(img), vc)
    assert out.startswith("Fake bank login")
    # a base64 data URL was sent
    assert vc.calls[0][0].startswith("data:image/png;base64,")


def test_extract_from_image_uses_vision_when_no_qr(tmp_path):
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"not-a-real-qr")
    vc = FakeVision("transfer to Maybank account 1234567890 now")
    # decode_qr will find nothing (cv2 returns None / no codes) -> vision fallback
    with mock.patch("hive.extraction.media.decode_qr", return_value=[]):
        hvis = extract_from_image(str(img), source_msg_id=5, vision_client=vc)
    assert any(h.kind == "bank_account" and h.value == "1234567890" for h in hvis)


def test_extract_from_image_prefers_local_qr(tmp_path):
    from hive.state import HVI

    img = tmp_path / "qr.png"
    img.write_bytes(b"qr")
    vc = FakeVision("should not be called")
    with mock.patch(
        "hive.extraction.media.decode_qr",
        return_value=[HVI(kind="url", value="http://x.co", source_msg_id=5)],
    ):
        hvis = extract_from_image(str(img), source_msg_id=5, vision_client=vc)
    assert any(h.kind == "url" for h in hvis)
    assert vc.calls == []  # vision NOT invoked when QR already yielded HVIs


def test_unstructured_vision_description_does_not_become_raw_qr(tmp_path):
    img = tmp_path / "character.png"
    img.write_bytes(b"not-a-real-qr")
    vc = FakeVision("A cartoon character wearing a white shirt and green trousers.")

    with mock.patch("hive.extraction.media.decode_qr", return_value=[]):
        hvis = extract_from_image(str(img), source_msg_id=9, vision_client=vc)

    assert hvis == []
