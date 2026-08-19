"""L3 extraction tests (fyp.txt L3), fully offline."""

from hive.extraction.engine import extract_hvis, merge_hvis
from hive.extraction.media import classify_payload
from hive.extraction.regex_rules import extract_regex


class FakeNer:
    """Fake GLiNER backend returning canned spans."""

    def __init__(self, spans):
        self.spans = spans

    def predict(self, text, labels):
        return self.spans


def _kinds(hvis):
    return sorted(h.kind for h in hvis)


def test_regex_finds_url_and_phone():
    hvis = extract_regex("click http://evil.co and call 012-345 6789", 1)
    assert "url" in _kinds(hvis)
    assert "phone_my" in _kinds(hvis)


def test_regex_normalizes_bare_domain_for_sandboxing():
    hvis = extract_regex("8Bit.co this is the website. Trust me", 2)

    assert [(item.kind, item.value) for item in hvis] == [("url", "https://8Bit.co")]


def test_bare_domain_extraction_ignores_email_addresses_and_decimal_versions():
    hvis = extract_regex("email scammer@example.com about version 1.44.0", 3)

    assert not any(item.kind == "url" for item in hvis)


def test_bank_account_only_with_keyword():
    with_kw = extract_regex("transfer to Maybank account 1234567890", 1)
    without_kw = extract_regex("my lucky number is 1234567890", 1)
    assert any(h.kind == "bank_account" and h.value == "1234567890" for h in with_kw)
    assert not any(h.kind == "bank_account" for h in without_kw)


def test_crypto_and_telegram():
    hvis = extract_regex("send to 0x52908400098527886E0F7030069857D2E4169EE7 or @scammerboss", 2)
    assert "crypto_eth" in _kinds(hvis)
    assert "telegram_id" in _kinds(hvis)


def test_engine_merges_regex_and_ner():
    ner = FakeNer([("bank name", "Maybank", 0.95)])
    hvis = extract_hvis("pay to Maybank account 1234567890", 3, ner_backend=ner)
    kinds = _kinds(hvis)
    assert "bank_name" in kinds       # from NER
    assert "bank_account" in kinds    # from regex


def test_engine_dedup_keeps_higher_confidence():
    # Regex emits a context-gated bank_account at 0.6; NER reports the same
    # account with higher confidence. Dedup should keep one, the higher score.
    ner = FakeNer([("bank account number", "1234567890", 0.95)])
    hvis = extract_hvis("Maybank account 1234567890", 4, ner_backend=ner)
    accounts = [h for h in hvis if h.kind == "bank_account"]
    assert len(accounts) == 1
    assert accounts[0].confidence == 0.95


def test_engine_extracts_organization_and_location_labels():
    ner = FakeNer(
        [
            ("company name", "ABC Garage", 0.91),
            ("location", "Kajang", 0.88),
        ]
    )

    hvis = extract_hvis("ABC Garage showroom is in Kajang", 5, ner_backend=ner)

    assert {(item.kind, item.value) for item in hvis} == {
        ("organization", "ABC Garage"),
        ("location", "Kajang"),
    }


def test_cross_message_merge_normalizes_person_honorifics():
    existing = extract_hvis(
        "Good morning Mr Alex",
        6,
        ner_backend=FakeNer([("person name", "Mr Alex", 0.61)]),
    )
    incoming = extract_hvis(
        "Hello Mr alex",
        7,
        ner_backend=FakeNer([("person name", "Mr alex", 0.73)]),
    )

    accepted = merge_hvis(existing, incoming)

    assert accepted == [existing[0]]
    assert len(existing) == 1
    assert existing[0].value == "Mr alex"
    assert existing[0].confidence == 0.73
    assert existing[0].source_msg_id == 7


def test_qr_payload_url_classified():
    hvis = classify_payload("http://phish.example/login", 5)
    assert any(h.kind == "url" for h in hvis)


def test_qr_payload_unknown_kept_raw():
    hvis = classify_payload("00020101021226580014A00000061501234", 6)
    assert any(h.kind == "raw_qr" for h in hvis)
