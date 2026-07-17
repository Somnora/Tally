"""Pure unit tests for the itemized-row mappers (no network, no DB)."""

from datetime import date
from decimal import Decimal

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
