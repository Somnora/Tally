"""Joining the two FEC identities a member acquires when they change seats.

This is the most dangerous edit in the codebase. Wrong, it puts one member's
voting record on another member's page: a false claim about a named person,
and the failure everything else here exists to prevent. Every test below is a
case the linker must REFUSE.
"""

from pipeline.etl.link_candidacies import pair_candidate_ids


def _row(cand_id: str, name: str, state: str, party: str, office: str,
         ici: str) -> dict[str, str]:
    return {"CAND_ID": cand_id, "CAND_NAME": name, "CAND_OFFICE_ST": state,
            "CAND_PTY_AFFILIATION": party, "CAND_OFFICE": office, "CAND_ICI": ici}


def test_pairs_a_sitting_member_with_their_run_for_another_seat() -> None:
    pairs = pair_candidate_ids([
        _row("H8MI11254", "STEVENS, HALEY", "MI", "DEM", "H", "I"),
        _row("S6MI00426", "STEVENS, HALEY", "MI", "DEM", "S", "O"),
    ])
    assert len(pairs) == 1
    assert pairs[0].incumbent_fec_id == "H8MI11254"
    assert pairs[0].other_fec_id == "S6MI00426"


def test_two_people_sharing_a_surname_and_state_are_never_paired() -> None:
    """Sherrod Brown and Shontel Brown are both Ohio Democrats.

    An earlier ad-hoc query matched them, because it compared surnames and
    first initials. Grouping on the full filed name is what makes that
    impossible: the FEC files these as different strings because they are
    different people.
    """
    pairs = pair_candidate_ids([
        _row("H0OH11111", "BROWN, SHONTEL M", "OH", "DEM", "H", "I"),
        _row("S0OH22222", "BROWN, SHERROD", "OH", "DEM", "S", "O"),
    ])
    assert pairs == []


def test_two_incumbent_records_are_not_settled_by_this_rule() -> None:
    """Adam Schiff holds House and Senate records both flagged incumbent.

    That means the move already happened and both entries are live. Which one
    the votes belong to is not a question this evidence answers, so it does
    not answer it.
    """
    pairs = pair_candidate_ids([
        _row("H0CA27085", "SCHIFF, ADAM", "CA", "DEM", "H", "I"),
        _row("S4CA00555", "SCHIFF, ADAM", "CA", "DEM", "S", "I"),
    ])
    assert pairs == []


def test_same_name_in_a_different_state_is_not_the_same_person() -> None:
    pairs = pair_candidate_ids([
        _row("H0TX11111", "SMITH, JOHN", "TX", "REP", "H", "I"),
        _row("S0FL22222", "SMITH, JOHN", "FL", "REP", "S", "O"),
    ])
    assert pairs == []


def test_same_name_under_a_different_party_is_not_the_same_person() -> None:
    pairs = pair_candidate_ids([
        _row("H0TX11111", "SMITH, JOHN", "TX", "REP", "H", "I"),
        _row("S0TX22222", "SMITH, JOHN", "TX", "DEM", "S", "O"),
    ])
    assert pairs == []


def test_two_runs_for_the_same_office_are_not_a_seat_change() -> None:
    pairs = pair_candidate_ids([
        _row("H0TX11111", "SMITH, JOHN", "TX", "REP", "H", "I"),
        _row("H2TX22222", "SMITH, JOHN", "TX", "REP", "H", "C"),
    ])
    assert pairs == []


def test_a_group_with_no_incumbent_at_all_is_skipped() -> None:
    pairs = pair_candidate_ids([
        _row("H0TX11111", "SMITH, JOHN", "TX", "REP", "H", "C"),
        _row("S0TX22222", "SMITH, JOHN", "TX", "REP", "S", "O"),
    ])
    assert pairs == []


def test_rows_missing_a_grouping_field_are_ignored_not_grouped_as_blank() -> None:
    """Blank name or state would otherwise collapse unrelated candidates into
    one group and pair them with each other."""
    pairs = pair_candidate_ids([
        _row("H0TX11111", "", "TX", "REP", "H", "I"),
        _row("S0TX22222", "", "TX", "REP", "S", "O"),
        _row("H0TX33333", "SMITH, JOHN", "", "REP", "H", "I"),
        _row("S0TX44444", "SMITH, JOHN", "", "REP", "S", "O"),
    ])
    assert pairs == []
