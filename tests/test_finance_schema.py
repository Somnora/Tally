"""Tests for the Milestone 2 finance schema and repository functions."""

from typing import Any

from pipeline import db


def _seed_candidate(conn: db.Connection) -> tuple[int, int]:
    """Minimal fixture: source, politician, committee, race, candidacy."""
    source_id = db.insert_source(
        conn, source_type="test_finance", url="https://example.test",
        content_hash="finance-fixture", raw_payload=b"x",
    )
    politician_id = db.upsert_politician_by_fec_id(
        conn, full_name="DOE, JANE", party="IND", state="ME",
        fec_candidate_id="S6ME00001", source_id=source_id,
    )
    db.upsert_committee(
        conn, cmte_id="C00000001", name="Jane Doe for Senate", cmte_type="S",
        cmte_designation="P", party=None, connected_org=None,
        cand_id="S6ME00001", state="ME", cycle=2026, source_id=source_id,
    )
    db.upsert_committee(
        conn, cmte_id="C00000002", name="Example PAC", cmte_type="Q",
        cmte_designation="U", party=None, connected_org="Example Corp",
        cand_id=None, state=None, cycle=2026, source_id=source_id,
    )
    race_id = db.upsert_race(
        conn, cycle=2026, state="ME", office="senate", district=None, senate_class=2,
    )
    db.upsert_candidacy(
        conn, race_id=race_id, politician_id=politician_id,
        fec_candidate_id="S6ME00001", party="IND", incumbent_challenger="C",
        cand_status="C", principal_cmte_id="C00000001", source_id=source_id,
    )
    return politician_id, source_id


def _donation_row(politician_id: int, source_id: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "fec_sub_id": "4020260001",
        "recipient_cmte_id": "C00000001",
        "fec_candidate_id": "S6ME00001",
        "politician_id": politician_id,
        "contributor_name": "Example PAC",
        "contributor_cmte_id": "C00000002",
        "amount": 5000,
        "contributed_at": "2026-03-01",
        "cycle": 2026,
        "transaction_tp": "24K",
        "entity_tp": "PAC",
        "transaction_pgi": "P2026",
        "employer": None,
        "occupation": None,
        "donor_city": None,
        "donor_state": None,
        "donor_zip": None,
        "image_num": "202603019700000001",
        "memo_cd": None,
        "memo_text": None,
        "source_id": source_id,
    }
    row.update(overrides)
    return row


def test_donation_upsert_is_idempotent_and_applies_amendments(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    row = _donation_row(politician_id, source_id)

    db.upsert_donations_bulk(conn, [row])
    db.upsert_donations_bulk(conn, [row])
    assert db.count_rows(conn, "donations") == 1

    # Amended filing: same sub_id, revised amount -> row is updated, not duplicated.
    db.upsert_donations_bulk(conn, [dict(row, amount=7500)])
    amount = conn.execute(
        "SELECT amount FROM donations WHERE fec_sub_id = %s", (row["fec_sub_id"],)
    ).fetchone()
    assert amount is not None and int(amount[0]) == 7500
    assert db.count_rows(conn, "donations") == 1


def test_candidate_totals_upsert_updates_in_place(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    totals: dict[str, Any] = {
        "fec_candidate_id": "S6ME00001", "cycle": 2026, "politician_id": politician_id,
        "total_receipts": 100000, "total_disbursements": 40000, "cash_on_hand": 60000,
        "debts_owed": 0, "individual_itemized": 70000, "individual_unitemized": 20000,
        "pac_contributions": 10000, "coverage_end": "2026-06-30", "source_id": source_id,
    }
    db.upsert_candidate_totals(conn, totals)
    db.upsert_candidate_totals(conn, dict(totals, total_receipts=150000))
    assert db.count_rows(conn, "candidate_totals") == 1
    receipts = conn.execute(
        "SELECT total_receipts FROM candidate_totals WHERE fec_candidate_id = 'S6ME00001'"
    ).fetchone()
    assert receipts is not None and int(receipts[0]) == 150000


def test_finance_views_roll_up_and_exclude_memo_ie_and_refunds(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    rows = [
        _donation_row(politician_id, source_id),  # PAC direct, 5000
        _donation_row(politician_id, source_id, fec_sub_id="4020260002",
                      amount=1000, memo_cd="X"),  # memo: excluded from sums
        _donation_row(politician_id, source_id, fec_sub_id="4020260003",
                      recipient_cmte_id=None, transaction_tp="24E", amount=20000),  # IE support
        _donation_row(politician_id, source_id, fec_sub_id="4020260004",
                      recipient_cmte_id=None, transaction_tp="24A", amount=30000),  # IE oppose
        _donation_row(politician_id, source_id, fec_sub_id="4020260005",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="15", entity_tp="IND", amount=250),  # individual receipt
        _donation_row(politician_id, source_id, fec_sub_id="4020260006",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="22Y", entity_tp="IND", amount=100),  # refund: NOT a receipt
    ]
    db.upsert_donations_bulk(conn, rows)
    db.refresh_finance_views(conn)

    finance = conn.execute(
        "SELECT pac_itemized, ie_support, ie_oppose, individual_itemized_loaded, "
        "individual_refunds FROM mv_candidacy_finance WHERE fec_candidate_id = 'S6ME00001'"
    ).fetchone()
    assert finance is not None
    assert int(finance[0]) == 5000   # memo row's 1000 NOT included
    assert int(finance[1]) == 20000
    assert int(finance[2]) == 30000
    assert int(finance[3]) == 250    # refund's 100 NOT summed as a receipt
    assert int(finance[4]) == 100    # ...but visible in its own column

    top = conn.execute(
        "SELECT committee_name, total_amount, donor_rank FROM mv_top_committee_donors"
    ).fetchall()
    assert len(top) == 1  # IE and memo rows never appear as "donors"
    assert top[0][0] == "Example PAC"
    assert int(top[0][1]) == 5000
