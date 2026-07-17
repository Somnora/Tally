"""Unit test for the OpenFEC totals payload mapping (pure, no network)."""

from pipeline.stages.sync_finance import map_totals_payload


def test_map_totals_payload_full() -> None:
    result = {
        "receipts": 6471587.0,
        "disbursements": 2100000.5,
        "last_cash_on_hand_end_period": 4300000.25,
        "last_debts_owed_by_committee": 0.0,
        "individual_itemized_contributions": 5000000.0,
        "individual_unitemized_contributions": 900000.0,
        "other_political_committee_contributions": 1839379.0,
        "coverage_end_date": "2026-06-30T00:00:00+00:00",
        "some_field_we_dont_use": 42,
    }
    mapped = map_totals_payload(result)
    assert mapped["total_receipts"] == 6471587.0
    assert mapped["cash_on_hand"] == 4300000.25
    assert mapped["pac_contributions"] == 1839379.0
    assert mapped["coverage_end"] == "2026-06-30"  # date part only


def test_map_totals_payload_handles_missing_fields() -> None:
    mapped = map_totals_payload({})
    assert mapped["total_receipts"] is None
    assert mapped["coverage_end"] is None
