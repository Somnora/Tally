"""Unit tests for vote-position normalization (pure)."""

from pipeline.etl.congress_votes import normalize_position


def test_canonical_positions() -> None:
    assert normalize_position("Yea") == "yea"
    assert normalize_position("Aye") == "yea"      # House vocabulary
    assert normalize_position("Nay") == "nay"
    assert normalize_position("No") == "nay"
    assert normalize_position("Present") == "present"
    assert normalize_position("Not Voting") == "not_voting"
    assert normalize_position("Guilty") == "guilty"
    assert normalize_position("Not  Guilty") == "not_guilty"  # whitespace noise


def test_non_positions_are_none_not_guessed() -> None:
    # Speaker elections record candidate names as the vote cast.
    assert normalize_position("Johnson") is None
    assert normalize_position("") is None
