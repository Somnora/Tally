"""Tests for the extraction scoring harness (pipeline/eval_extraction.py).

The harness is what every precision claim about stage 4 rests on, so the
confusion-count arithmetic is tested directly rather than eyeballed, and the
headline numbers are pinned: a regex edit in promise_gate.py that quietly
moves them fails here instead of in a report.
"""

from pathlib import Path

from pipeline.eval_extraction import (
    DEFAULT_GOLD,
    GoldRow,
    KeepDecision,
    gate_procedure,
    keep_everything,
    load_gold,
    naive_hedge_stoplist,
    score,
)

GOLD_PATH = Path(DEFAULT_GOLD)


def _row(promise_id: int, quote: str, should_extract: bool) -> GoldRow:
    return GoldRow(
        promise_id=promise_id, verbatim_quote=quote, doc_type="youtube_transcript",
        topic="economy", specificity="directional", verdict="correct",
        should_extract=should_extract, label_confidence="high", note="",
    )


# -- confusion arithmetic ------------------------------------------------------

def test_score_counts_all_four_quadrants() -> None:
    rows = [
        _row(1, "real promise kept", True),
        _row(2, "real promise dropped", True),
        _row(3, "leak dropped", False),
        _row(4, "leak kept", False),
    ]
    drop_ids = {2, 3}

    def procedure(row: GoldRow) -> KeepDecision:
        return KeepDecision(row.promise_id not in drop_ids, "test")

    s = score(rows, procedure, "test")
    assert (s.real_promises, s.extraction_errors) == (2, 2)
    assert s.leaks_caught == 1            # id 3
    assert s.collateral_damage == 1       # id 2
    assert s.kept_true == 1 and s.kept_false == 1
    assert s.collateral_ids == (2,)
    assert s.escaped_ids == (4,)
    assert s.precision == 0.5             # 1 kept_true of 2 kept
    assert s.retention == 0.5             # 1 of 2 real promises survived


def test_perfect_procedure_scores_one() -> None:
    rows = [_row(1, "a", True), _row(2, "b", False)]
    s = score(rows, lambda r: KeepDecision(r.should_extract, "oracle"), "oracle")
    assert (s.precision, s.retention) == (1.0, 1.0)
    assert s.collateral_damage == 0 and s.leaks_caught == 1


def test_dropping_everything_has_zero_precision_not_a_crash() -> None:
    rows = [_row(1, "a", True), _row(2, "b", False)]
    s = score(rows, lambda r: KeepDecision(False, "drop"), "drop-all")
    assert s.precision == 0.0 and s.retention == 0.0


# -- the real gold set ---------------------------------------------------------

def test_gold_set_shape_is_the_reviewed_one() -> None:
    rows = load_gold(GOLD_PATH)
    assert len(rows) == 118
    assert sum(1 for r in rows if r.should_extract) == 71
    assert sum(1 for r in rows if not r.should_extract) == 47


def test_baseline_reproduces_the_measured_v2_precision() -> None:
    """extract_v2 as shipped: 71 of 118 real, the 60.2% under review."""
    s = score(load_gold(GOLD_PATH), keep_everything, "baseline")
    assert s.leaks_caught == 0
    assert s.collateral_damage == 0
    assert round(s.precision, 3) == 0.602
    assert s.retention == 1.0


def test_gate_headline_numbers_are_pinned() -> None:
    """The headline numbers, locked so a regex change cannot move them
    silently. In-sample, and measured on a gold set built entirely from
    transcripts: gate_v2's headless-gerund rule leaves them untouched because
    no bulleted issue page is represented here. That is a gap in the gold set,
    not evidence the rule is inert.
    """
    s = score(load_gold(GOLD_PATH), gate_procedure(), "gate")
    assert s.leaks_caught == 41
    assert s.collateral_damage == 1
    assert s.collateral_ids == (251,)
    assert round(s.precision, 3) == 0.921
    assert round(s.retention, 3) == 0.986


def test_gate_loses_nothing_on_high_confidence_labels() -> None:
    """Its one casualty is a row the reviewer themselves marked unsure."""
    rows = [r for r in load_gold(GOLD_PATH) if r.label_confidence == "high"]
    s = score(rows, gate_procedure(), "gate")
    assert s.collateral_damage == 0
    assert s.retention == 1.0


def test_naive_stoplist_is_worse_than_the_gate_on_both_axes() -> None:
    """The reason the gate tests a frame and not a phrase: a substring
    stoplist catches fewer leaks AND destroys more real promises."""
    rows = load_gold(GOLD_PATH)
    naive = score(rows, naive_hedge_stoplist, "naive")
    gate = score(rows, gate_procedure(), "gate")
    assert naive.leaks_caught < gate.leaks_caught
    assert naive.collateral_damage > gate.collateral_damage


def test_disabling_a_rule_never_increases_leaks_caught() -> None:
    rows = load_gold(GOLD_PATH)
    full = score(rows, gate_procedure(), "full")
    without_hedge = score(rows, gate_procedure(disabled_rules=["hedged_opinion"]), "ablated")
    assert without_hedge.leaks_caught < full.leaks_caught
    assert without_hedge.collateral_damage <= full.collateral_damage
