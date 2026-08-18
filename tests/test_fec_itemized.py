"""Tests for the itemized loader: the row mappers, and the committee map.

The mappers are pure. The committee map is not, because what it gets wrong
is a database question: which candidate a committee raises money as.
"""

from datetime import date
from decimal import Decimal

from pipeline import db
from pipeline.etl.fec_itemized import (
    StateContext,
    indiv_row_to_donation,
    parse_amount,
    parse_fec_date,
    pas2_row_to_donation,
)

CTX = StateContext(
    cycle=2026,
    politician_by_fec={"S6ME00001": 11},
    cand_by_cmte={"C00000001": "S6ME00001"},
    known_committees={"C00000001", "C00000002"},
)


def _stats() -> dict[str, int]:
    return {"bad_amount": 0, "contributor_not_in_master": 0}


def test_parse_fec_date() -> None:
    assert parse_fec_date("03152026") == date(2026, 3, 15)
    assert parse_fec_date("") is None
    assert parse_fec_date("13452026") is None  # month 13
    assert parse_fec_date("junk") is None


def test_parse_amount() -> None:
    assert parse_amount("5000") == Decimal("5000")
    assert parse_amount("-250") == Decimal("-250")  # refunds are negative
    assert parse_amount("") is None


def test_pas2_direct_contribution_maps_fully() -> None:
    row = {
        "CMTE_ID": "C00000002", "TRANSACTION_TP": "24K", "ENTITY_TP": "PAC",
        "TRANSACTION_PGI": "P2026", "IMAGE_NUM": "img1", "NAME": "DOE FOR SENATE",
        "CITY": "PORTLAND", "STATE": "ME", "ZIP_CODE": "04101",
        "TRANSACTION_DT": "02012026", "TRANSACTION_AMT": "5000",
        "OTHER_ID": "C00000001", "CAND_ID": "S6ME00001",
        "MEMO_CD": "", "MEMO_TEXT": "", "SUB_ID": "sub1",
    }
    donation = pas2_row_to_donation(row, CTX, source_id=1, stats=_stats())
    assert donation is not None
    assert donation["recipient_cmte_id"] == "C00000001"
    assert donation["contributor_cmte_id"] == "C00000002"
    assert donation["politician_id"] == 11
    assert donation["amount"] == Decimal("5000")
    # pas2's NAME/CITY/STATE describe the recipient, never stored as donor info
    assert donation["contributor_name"] is None
    assert donation["donor_city"] is None


def test_pas2_independent_expenditure_has_no_recipient() -> None:
    row = {
        "CMTE_ID": "C00000002", "TRANSACTION_TP": "24A", "ENTITY_TP": "ORG",
        "TRANSACTION_DT": "02012026", "TRANSACTION_AMT": "30000",
        "OTHER_ID": "C00000001", "CAND_ID": "S6ME00001", "SUB_ID": "sub2",
    }
    donation = pas2_row_to_donation(row, CTX, source_id=1, stats=_stats())
    assert donation is not None
    assert donation["recipient_cmte_id"] is None  # IE: money about, not to
    assert donation["fec_candidate_id"] == "S6ME00001"


def test_pas2_other_states_candidates_are_filtered_out() -> None:
    row = {"CAND_ID": "S6TX00099", "TRANSACTION_AMT": "100", "SUB_ID": "sub3"}
    assert pas2_row_to_donation(row, CTX, source_id=1, stats=_stats()) is None


def test_pas2_bad_amount_counted_not_crashed() -> None:
    stats = _stats()
    row = {"CAND_ID": "S6ME00001", "TRANSACTION_AMT": "??", "SUB_ID": "sub4"}
    assert pas2_row_to_donation(row, CTX, source_id=1, stats=stats) is None
    assert stats["bad_amount"] == 1


def test_indiv_row_maps_donor_details() -> None:
    row = {
        "CMTE_ID": "C00000001", "TRANSACTION_TP": "15", "ENTITY_TP": "IND",
        "NAME": "SMITH, ALEX", "CITY": "BANGOR", "STATE": "ME", "ZIP_CODE": "04401",
        "EMPLOYER": "SELF-EMPLOYED", "OCCUPATION": "LOBSTERMAN",
        "TRANSACTION_DT": "05202026", "TRANSACTION_AMT": "250",
        "MEMO_CD": "", "MEMO_TEXT": "", "SUB_ID": "sub5", "IMAGE_NUM": "img5",
        "TRANSACTION_PGI": "P2026", "OTHER_ID": "",
    }
    donation = indiv_row_to_donation(row, CTX, source_id=2, stats=_stats())
    assert donation is not None
    assert donation["recipient_cmte_id"] == "C00000001"
    assert donation["fec_candidate_id"] == "S6ME00001"
    assert donation["contributor_name"] == "SMITH, ALEX"
    assert donation["employer"] == "SELF-EMPLOYED"
    assert donation["occupation"] == "LOBSTERMAN"
    assert donation["donor_state"] == "ME"


def test_indiv_row_for_unknown_committee_is_filtered_out() -> None:
    row = {"CMTE_ID": "C00999999", "TRANSACTION_AMT": "250", "SUB_ID": "sub6"}
    assert indiv_row_to_donation(row, CTX, source_id=2, stats=_stats()) is None


def test_committee_map_excludes_committees_a_candidate_does_not_raise_as(
    conn: db.Connection,
) -> None:
    """committees.cand_id is not a claim that the committee raises money AS
    that candidate.

    The FEC master populates cand_id for committees merely associated with a
    candidate. The NRSC carries a Senate candidate's id there, so joining on
    cand_id alone attributed the NRSC's 755,501 individual contributions,
    $51.5 million, to Dan Sullivan, whose own committee raised $2.3 million.
    Party committees, joint fundraisers and leadership PACs are the large
    ones, so the error fell hardest on leadership figures. Only authorized
    committees, designation P and A, may map.
    """
    source_id = db.insert_source(
        conn, source_type="test_cmte_map", url="https://example.test/map",
        content_hash="cmte-map-fixture", raw_payload=b"x",
    )
    politician_id = db.upsert_politician_by_fec_id(
        conn, full_name="DOE, JANE", party="IND", state="ME",
        fec_candidate_id="S6ME00001", source_id=source_id,
    )
    for cmte_id, name, designation in (
        ("C00000001", "Jane Doe for Senate", "P"),   # her own committee
        ("C00000010", "Doe Victory Fund", "J"),      # joint fundraiser
        ("C00000011", "Big Party Committee", "U"),   # party committee
        ("C00000012", "Doe Leadership PAC", "D"),    # leadership PAC
        ("C00000013", "Doe Authorized Two", "A"),    # a second authorized one
    ):
        db.upsert_committee(
            conn, cmte_id=cmte_id, name=name, cmte_type="S",
            cmte_designation=designation, party=None, connected_org=None,
            cand_id="S6ME00001", state="ME", cycle=2026, source_id=source_id,
        )
    race_id = db.upsert_race(
        conn, cycle=2026, state="ME", office="senate", district=None, senate_class=2,
    )
    db.upsert_candidacy(
        conn, race_id=race_id, politician_id=politician_id,
        fec_candidate_id="S6ME00001", party="IND", incumbent_challenger="C",
        cand_status="C", principal_cmte_id="C00000001", source_id=source_id,
    )

    mapped = set(db.state_committee_map(conn, "ME", 2026))
    assert mapped == {"C00000001", "C00000013"}, (
        "only authorized committees may raise money as this candidate; "
        f"got {sorted(mapped)}"
    )
    # The same restriction has to hold for a national pass, which is the one
    # that actually meets the NRSC.
    assert set(db.state_committee_map(conn, "ALL", 2026)) == {"C00000001", "C00000013"}


def test_a_redesignated_committee_maps_to_the_seat_its_owner_is_running_for(
    conn: db.Connection,
) -> None:
    """A member seeking a different seat keeps their committee.

    The committee is then the principal committee of BOTH candidacies while
    the FEC's own cand_id names the live one. Trusting principal_cmte_id
    alongside that linkage mapped one committee to two candidates, and the
    map being a dict meant one silently won: Chris Pappas's $5.6 million
    landed on a House seat he is not running for while his Senate campaign,
    which the FEC credits with $7.5 million, read zero. 40 live candidacies
    were showing nothing for this reason.
    """
    source_id = db.insert_source(
        conn, source_type="test_redesignation", url="https://example.test/rd",
        content_hash="redesignation-fixture", raw_payload=b"x",
    )
    house_pid = db.upsert_politician_by_fec_id(
        conn, full_name="ROE, SAM (HOUSE)", party="IND", state="ME",
        fec_candidate_id="H8ME00001", source_id=source_id,
    )
    senate_pid = db.upsert_politician_by_fec_id(
        conn, full_name="ROE, SAM (SENATE)", party="IND", state="ME",
        fec_candidate_id="S6ME00002", source_id=source_id,
    )
    # One committee, redesignated to the Senate run: FEC's cand_id says so.
    db.upsert_committee(
        conn, cmte_id="C00000021", name="Sam Roe for Senate", cmte_type="S",
        cmte_designation="P", party=None, connected_org=None,
        cand_id="S6ME00002", state="ME", cycle=2026, source_id=source_id,
    )
    house_race = db.upsert_race(
        conn, cycle=2026, state="ME", office="house", district="01", senate_class=None)
    senate_race = db.upsert_race(
        conn, cycle=2026, state="ME", office="senate", district=None, senate_class=2)
    # Both candidacies still name it as their principal committee.
    for race_id, pid, fec_id in ((house_race, house_pid, "H8ME00001"),
                                 (senate_race, senate_pid, "S6ME00002")):
        db.upsert_candidacy(
            conn, race_id=race_id, politician_id=pid, fec_candidate_id=fec_id,
            party="IND", incumbent_challenger="C", cand_status="C",
            principal_cmte_id="C00000021", source_id=source_id,
        )

    mapping = db.state_committee_map(conn, "ALL", 2026)
    assert mapping["C00000021"] == "S6ME00002", (
        "money went to the seat its owner is no longer running for"
    )


def test_a_conduit_is_recorded_as_the_router_not_the_donor() -> None:
    """OTHER_ID names who passed the money on, not who gave it.

    Writing it into contributor_cmte_id would sweep every conduit into the
    top-donor rollup as though it had donated, inventing large committee
    donors out of money those committees never gave, and double counting it
    against the individuals who actually gave it. Separate roles, separate
    columns.
    """
    ctx = StateContext(
        cycle=2026,
        politician_by_fec={"S6ME00001": 7},
        cand_by_cmte={"C00000001": "S6ME00001"},
        known_committees={"C00000001", "C00999999"},
    )
    row = {
        "CMTE_ID": "C00000001", "SUB_ID": "4111120261234567890",
        "NAME": "SMITH, ALEX", "TRANSACTION_AMT": "500",
        "TRANSACTION_TP": "15E", "ENTITY_TP": "IND",
        "TRANSACTION_DT": "03012026", "OTHER_ID": "C00999999",
        "MEMO_TEXT": "* EARMARKED CONTRIBUTION: SEE BELOW",
    }
    donation = indiv_row_to_donation(row, ctx, source_id=1, stats={"bad_amount": 0})
    assert donation is not None
    assert donation["conduit_cmte_id"] == "C00999999"
    assert donation["contributor_cmte_id"] is None, (
        "the conduit must never occupy the donor field"
    )
    assert donation["contributor_name"] == "SMITH, ALEX"


def test_an_unknown_conduit_is_dropped_rather_than_stored_dangling() -> None:
    ctx = StateContext(
        cycle=2026, politician_by_fec={"S6ME00001": 7},
        cand_by_cmte={"C00000001": "S6ME00001"},
        known_committees={"C00000001"},   # the conduit is NOT in the master
    )
    row = {
        "CMTE_ID": "C00000001", "SUB_ID": "4111120261234567891",
        "NAME": "SMITH, ALEX", "TRANSACTION_AMT": "500",
        "TRANSACTION_TP": "15E", "ENTITY_TP": "IND",
        "TRANSACTION_DT": "03012026", "OTHER_ID": "C00404040",
    }
    donation = indiv_row_to_donation(row, ctx, source_id=1, stats={"bad_amount": 0})
    assert donation is not None
    assert donation["conduit_cmte_id"] is None
