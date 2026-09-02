"""L3 extraction tests (fyp.txt L3), fully offline."""

from hive.extraction.engine import extract_contextual_hvis, extract_hvis, merge_hvis
from hive.extraction.media import classify_payload
from hive.extraction.regex_rules import extract_regex
from hive.state import Message


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


def test_ner_rejects_url_fragments_and_role_words():
    class NoisyBackend:
        def predict(self, text, labels):
            return [
                ("url", "https", 0.8),
                ("url", "link", 0.8),
                ("person name", "uncle", 0.8),
                ("person name", "calling", 0.8),
                ("url", "valid.example/pay", 0.8),
            ]

    hvis = extract_hvis("uncle open the link", 1, ner_backend=NoisyBackend())

    assert [(item.kind, item.value) for item in hvis] == [
        ("url", "https://valid.example/pay")
    ]


def test_regex_normalizes_bare_domain_for_sandboxing():
    hvis = extract_regex("8Bit.co this is the website. Trust me", 2)

    assert [(item.kind, item.value) for item in hvis] == [("url", "https://8Bit.co")]


def test_bare_domain_extraction_ignores_email_addresses_and_decimal_versions():
    hvis = extract_regex("email scammer@example.com about version 1.44.0", 3)

    assert not any(item.kind == "url" for item in hvis)
    assert not any(item.kind == "telegram_id" for item in hvis)


def test_bank_account_only_with_keyword():
    with_kw = extract_regex("transfer to Maybank account 1234567890", 1)
    without_kw = extract_regex("my lucky number is 1234567890", 1)
    accidental_prefix = extract_regex("accident reference 9988776655", 1)
    assert any(h.kind == "bank_account" and h.value == "1234567890" for h in with_kw)
    assert not any(h.kind == "bank_account" for h in without_kw)
    assert not any(h.kind == "bank_account" for h in accidental_prefix)


def test_regex_normalizes_bank_alias_and_prefers_account_over_phone():
    hvis = extract_regex("MBB account 0123456789", 11)

    assert {(item.kind, item.value) for item in hvis} == {
        ("bank_name", "Maybank"),
        ("bank_account", "0123456789"),
    }


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


def test_ner_rejects_phone_label_without_a_number():
    ner = FakeNer([("phone number", "phone number", 0.91)])

    assert extract_hvis("Just send me your phone number", 41, ner_backend=ner) == []


def test_mbb_account_context_overrides_ner_phone_misclassification():
    ner = FakeNer([("phone number", "257282782992", 0.56)])

    hvis = extract_hvis("There you go: 257282782992 Mbb", 42, ner_backend=ner)

    assert [(item.kind, item.value) for item in hvis] == [
        ("bank_name", "Maybank"),
        ("bank_account", "257282782992"),
    ]
    assert hvis[1].extractor == "regex"


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


def test_context_extracts_explicit_english_and_mandarin_names():
    messages = [
        Message("stranger", "my name is John Tan", 1.0, 80),
        Message("stranger", "我叫陈伟", 2.0, 81),
    ]

    hvis = extract_contextual_hvis(messages, {80, 81})

    assert {(item.kind, item.value, item.source_msg_id) for item in hvis} == {
        ("person_name", "John Tan", 80),
        ("person_name", "陈伟", 81),
    }


def test_context_stops_person_name_before_manglish_particle():
    messages = [Message("stranger", "My name is Kevin Lim lah.", 1.0, 82)]

    hvis = extract_contextual_hvis(messages, {82})

    assert [(item.kind, item.value, item.source_msg_id) for item in hvis] == [
        ("person_name", "Kevin Lim", 82)
    ]


def test_context_links_agent_alias_to_preceding_name_message():
    messages = [
        Message("agent", "the bank account under what name ah?", 1.0, -1),
        Message("stranger", "yes quick", 2.0, 90),
        Message("stranger", "petasan", 3.0, 91),
        Message("stranger", "this my agent", 4.0, 92),
    ]

    hvis = extract_contextual_hvis(messages, {90, 91, 92})

    assert [(item.kind, item.value, item.source_msg_id) for item in hvis] == [
        ("person_name", "petasan", 91)
    ]


def test_context_extracts_account_split_across_messages():
    messages = [
        Message("stranger", "use Maybank", 1.0, 100),
        Message("stranger", "1234567890", 2.0, 101),
    ]

    hvis = extract_contextual_hvis(messages, {100, 101})

    assert [(item.kind, item.value, item.source_msg_id) for item in hvis] == [
        ("bank_account", "1234567890", 101)
    ]


def test_context_rejects_status_words_and_unscoped_numbers():
    messages = [
        Message("stranger", "I am ready", 1.0, 110),
        Message("stranger", "this is the website", 2.0, 111),
        Message("stranger", "order reference", 3.0, 112),
        Message("stranger", "1234567890", 4.0, 113),
    ]

    assert extract_contextual_hvis(messages, {110, 111, 112, 113}) == []


def test_context_does_not_treat_generic_i_am_phrases_as_names():
    messages = [
        Message("stranger", "I am your agent", 1.0, 120),
        Message("stranger", "I'm only trying to help", 2.0, 121),
        Message("stranger", "I am done", 3.0, 122),
        Message("stranger", "I'm serious", 4.0, 123),
    ]

    assert extract_contextual_hvis(messages, {120, 121, 122, 123}) == []


def test_qr_payload_url_classified():
    hvis = classify_payload("http://phish.example/login", 5)
    assert any(h.kind == "url" for h in hvis)
    assert all(h.extractor == "qr" for h in hvis)


def test_qr_payload_unknown_kept_raw():
    hvis = classify_payload("00020101021226580014A00000061501234", 6)
    assert any(h.kind == "raw_qr" for h in hvis)
