"""Knowledge Exchange context stays factual and reaches cold Scientists."""

from scientist.scientist import _COLD_START, _build_research_start_messages


def test_cold_start_receives_seed_then_global_coverage():
    messages = _build_research_start_messages(
        first_round=True,
        seed_pack="CHILD WORLD SEED",
        coverage_pack="GLOBAL COVERAGE",
    )
    assert [item["content"] for item in messages] == [
        _COLD_START,
        "CHILD WORLD SEED",
        "GLOBAL COVERAGE",
    ]


def test_resume_starts_with_recomputed_coverage_only():
    messages = _build_research_start_messages(
        first_round=False,
        seed_pack="OLD SEED MUST NOT REPLAY",
        coverage_pack="GLOBAL COVERAGE",
    )
    assert messages == [{"role": "user", "content": "GLOBAL COVERAGE"}]


def test_empty_coverage_adds_no_message():
    messages = _build_research_start_messages(
        first_round=True,
        seed_pack=None,
        coverage_pack="",
    )
    assert len(messages) == 1
