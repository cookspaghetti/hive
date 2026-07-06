"""L1 human-emulation middleware tests (fyp.txt L1)."""

from hive.middleware.linguistic import inject_noise
from hive.middleware.pipeline import apply
from hive.middleware.temporal import compute_delay


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


def test_delay_within_bounds_and_scales_with_length():
    short = compute_delay("ok", "confused_elderly", seed=3)
    long = compute_delay("this is a much longer reply that takes time to type", "confused_elderly", seed=3)
    assert 2.0 <= short <= 45.0
    assert long > short


def test_elderly_slower_than_young():
    reply = "let me check my account details for you"
    elderly = compute_delay(reply, "confused_elderly", seed=5)
    young = compute_delay(reply, "naive_young_adult", seed=5)
    assert elderly > young


def test_pipeline_returns_text_and_delay():
    r = apply("how do i do it", "confused_elderly", incoming_len=40, seed=9)
    assert isinstance(r.text, str) and r.text
    assert r.delay_s >= 2.0
