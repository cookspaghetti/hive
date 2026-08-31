"""L1 human-emulation middleware tests (fyp.txt L1)."""

from hive.middleware.chunking import plan_reply, split_reply
from hive.middleware.language import assess_language_alignment, detect_language
from hive.middleware.linguistic import inject_noise
from hive.middleware.pipeline import apply
from hive.middleware.temporal import compute_delay, compute_phone_check_delay


def test_noise_is_deterministic_with_seed():
    a = inject_noise("how do i do it", "confused_elderly", seed=42)
    b = inject_noise("how do i do it", "confused_elderly", seed=42)
    assert a == b


def test_noise_preserves_short_text_wordcount_roughly():
    out = inject_noise("please transfer money now", "small_business_owner", seed=1, manglish=False)
    # word count unchanged (typos are intra-word, no particle appended)
    assert len(out.split()) == len("please transfer money now".split())


def test_zero_rate_no_typos_but_maybe_particle():
    out = inject_noise("hello there friend", "naive_young_adult", rate=0.0, manglish=False, seed=7)
    assert out == "hello there friend"


def test_mandarin_is_not_corrupted_by_latin_typo_emulation():
    text = "请告诉我你们公司的名字和地址。"
    assert inject_noise(text, "confused_elderly", rate=1.0, manglish=True, seed=2) == text


def test_language_detector_supports_english_mandarin_and_code_switching():
    assert detect_language("You need to verify your account lah") == "english"
    assert detect_language("请告诉我你的银行账号") == "mandarin"
    assert detect_language("请你 open your banking app now") == "mixed"


def test_language_alignment_flags_clear_switch_without_rewriting_reply():
    reply = "I need a moment to check this"

    assessment = assess_language_alignment("请现在转账给我", reply)
    result = apply(reply, "confused_elderly", incoming_text="请现在转账给我", seed=3)

    assert assessment.status == "mismatch"
    assert assessment.expected == "mandarin"
    assert assessment.observed == "english"
    assert result.language_alignment == assessment
    assert result.text == reply


def test_mandarin_reply_with_short_security_acronym_remains_aligned():
    result = apply(
        "我没有收到 OTP，可以再发吗？",
        "confused_elderly",
        incoming_text="请把 OTP 发给我",
        seed=5,
    )

    assert result.language_alignment.status == "aligned"
    assert result.language_alignment.expected == "mandarin"
    assert result.language_alignment.observed == "mandarin"


def test_delay_within_bounds_and_scales_with_length():
    short = compute_delay("ok", "confused_elderly", seed=3)
    long = compute_delay(
        "this is a much longer reply that takes time to type",
        "confused_elderly",
        seed=3,
    )
    assert 2.0 <= short <= 45.0
    assert long > short


def test_elderly_slower_than_young():
    reply = "let me check my account details for you"
    elderly = compute_delay(reply, "confused_elderly", seed=5)
    young = compute_delay(reply, "naive_young_adult", seed=5)
    assert elderly > young


def test_latency_varies_across_equivalent_responses_without_leaving_bounds():
    delays = {
        compute_delay(
            "I need a moment to check that account name",
            "small_business_owner",
            incoming_len=48,
            seed=seed,
        )
        for seed in range(20)
    }

    assert len(delays) >= 15
    assert all(2.0 <= delay <= 45.0 for delay in delays)


def test_pipeline_returns_text_and_delay():
    r = apply("how do i do it", "confused_elderly", incoming_len=40, seed=9)
    assert isinstance(r.text, str) and r.text
    assert r.delay_s >= 2.0


def test_explicit_reply_bubbles_are_preserved_and_timed():
    result = apply(
        "wait ah ||| which account should I use?",
        "small_business_owner",
        seed=4,
    )

    assert len(result.messages) == 2
    assert len(result.message_delays_s) == 2
    assert all(result.messages)
    assert result.message_delays_s[1] >= 0.8
    assert len(result.message_typing_s) == 2
    assert all(
        typing <= delay
        for typing, delay in zip(
            result.message_typing_s,
            result.message_delays_s,
            strict=True,
        )
    )
    assert result.planned_total_delay_s == sum(result.message_delays_s)
    assert result.planned_typing_s == sum(result.message_typing_s)


def test_model_selected_pace_is_removed_and_scales_timing():
    fast = apply("[[pace:fast]] wait ah ||| what account?", "confused_elderly", seed=8)
    slow = apply("[[pace:slow]] wait ah ||| what account?", "confused_elderly", seed=8)

    assert fast.pace == "fast"
    assert slow.pace == "slow"
    assert all("pace" not in message for message in (*fast.messages, *slow.messages))
    assert fast.message_delays_s[0] < slow.message_delays_s[0]


def test_obvious_busy_context_calibrates_normal_model_pace_to_slow():
    result = apply(
        "[[pace:normal]] sorry I am busy now ||| I check later",
        "overseas_worker",
        incoming_text="No rush, reply after your shift",
        seed=8,
    )

    assert result.pace == "slow"


def test_phone_check_timing_is_bounded_and_pace_sensitive():
    fast = compute_phone_check_delay(
        "overseas_worker",
        minimum=3.5,
        maximum=12.0,
        pace="fast",
        seed=12,
    )
    slow = compute_phone_check_delay(
        "overseas_worker",
        minimum=3.5,
        maximum=12.0,
        pace="slow",
        seed=12,
    )

    assert 3.5 <= fast <= slow <= 12.0


def test_overlong_reply_drops_crammed_middle_but_keeps_final_question():
    reply = (
        "First reaction that is deliberately somewhat long. "
        "Second polished explanation that a human would probably skip. "
        "Third unnecessary explanation with even more detail. "
        "Can you send the account holder name?"
    )

    plan = plan_reply(
        reply,
        target_chars=45,
        max_chars=60,
        max_bubbles=3,
        max_total_chars=120,
    )

    assert len(plan.messages) == 2
    assert all(len(message) <= 60 for message in plan.messages)
    assert plan.messages[-1] == "Can you send the account holder name?"


def test_existing_manglish_does_not_get_extra_particle_per_bubble():
    result = apply(
        "[[pace:normal]] wait ah ||| send again leh",
        "naive_young_adult",
        seed=2,
    )

    assert result.messages == ("wait ah", "send again leh")


def test_repeated_opening_words_are_not_turned_into_awkward_double_typos():
    output = inject_noise(
        "Okay okay I am checking",
        "naive_young_adult",
        rate=1.0,
        manglish=False,
        seed=4,
    )

    assert output.split()[:2] == ["Okay", "okay"]
    assert (
        sum(
            a != b
            for a, b in zip(
                output.split(),
                "Okay okay I am checking".split(),
                strict=True,
            )
        )
        <= 1
    )


def test_long_english_and_mandarin_replies_split_without_cramping():
    english = (
        "I just saw your messages. I am outside now and cannot check the banking app. "
        "Can you send the account holder name again? I will try when I get home."
    )
    mandarin = (
        "我刚刚才看到你的消息。现在人在外面，不方便打开银行应用。"
        "你可以再发一次收款人的名字吗？我回家后再试。"
    )

    assert 1 < len(split_reply(english)) <= 3
    chunks = split_reply(mandarin, max_chars=35, target_chars=24)
    assert 1 < len(chunks) <= 3
    assert "".join(chunks).replace(" ", "") == mandarin
