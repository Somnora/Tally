"""Naming the money that has no ceiling on it.

Direct committee contributions stop at $10,000 per candidate. Independent
expenditure does not stop anywhere, and for most candidates carrying any of
it, it is the larger number. The site used to print those totals with nobody's
name attached while naming every capped $10,000 donor beside them, which reads
as evasion and was: the spending committee's id was in the row all along.

These tests hold that line. Each one asserts either that a spender is named,
or that naming them did not quietly break the accounting rules the finance
views already enforce.
"""

from typing import Any

from pipeline import db
from tests.test_finance_schema import _donation_row, _seed_candidate


def _ie(politician_id: int, source_id: int, sub_id: str, cmte_id: str,
        name: str, amount: int, tp: str, **extra: Any) -> dict[str, Any]:
    """One independent expenditure row, for or against our fixture candidate."""
    return _donation_row(
        politician_id, source_id, fec_sub_id=sub_id, recipient_cmte_id=None,
        contributor_cmte_id=cmte_id, contributor_name=name,
        transaction_tp=tp, amount=amount, **extra,
    )


def _spenders(conn: db.Connection) -> list[tuple[str, str, int]]:
    rows = conn.execute(
        "SELECT spender_name, stance, total_amount, spender_rank "
        "FROM mv_top_outside_spenders ORDER BY stance, spender_rank"
    ).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]


def _committee(conn: db.Connection, source_id: int, cmte_id: str, name: str) -> None:
    db.upsert_committee(
        conn, cmte_id=cmte_id, name=name, cmte_type="O", cmte_designation="U",
        party=None, connected_org=None, cand_id=None, state=None, cycle=2026,
        source_id=source_id,
    )


def test_outside_spending_is_attributed_to_the_committee_that_spent_it(
    conn: db.Connection,
) -> None:
    politician_id, source_id = _seed_candidate(conn)
    _committee(conn, source_id, "C00000003", "Big Outside Group")
    db.upsert_donations_bulk(conn, [
        _ie(politician_id, source_id, "4020260010", "C00000003",
            "Big Outside Group", 750_000, "24E"),
    ])
    db.refresh_finance_views(conn)
    assert _spenders(conn) == [("Big Outside Group", "supporting", 750_000)]


def test_the_lone_opposing_spender_is_never_crowded_out_by_supporters(
    conn: db.Connection,
) -> None:
    """The rank cap partitions by stance, not by candidate.

    Ranking a candidate's spenders on amount alone would let a crowd of small
    supporters bury the one committee spending against them, which is the
    single fact a reader is most likely to want.
    """
    politician_id, source_id = _seed_candidate(conn)
    rows = []
    for n in range(12):
        cmte = f"C0000{n + 10:04d}"
        _committee(conn, source_id, cmte, f"Friendly PAC {n}")
        rows.append(_ie(politician_id, source_id, f"402026{n + 100:04d}", cmte,
                        f"Friendly PAC {n}", 100_000 - n, "24E"))
    _committee(conn, source_id, "C00009999", "Hostile PAC")
    rows.append(_ie(politician_id, source_id, "4020260999", "C00009999",
                    "Hostile PAC", 25, "24A"))
    db.upsert_donations_bulk(conn, rows)
    db.refresh_finance_views(conn)

    spenders = _spenders(conn)
    assert ("Hostile PAC", "opposing", 25) in spenders, (
        "a committee spending against this candidate vanished because twelve "
        "supporters outranked it"
    )
    assert sum(1 for s in spenders if s[1] == "supporting") == 10


def test_outside_spending_never_appears_as_a_donation(conn: db.Connection) -> None:
    """Money spent ABOUT a candidate is not money given TO them.

    The two must never merge: a super PAC that spent $750,000 supporting
    somebody did not donate to them, and showing it in the donor list would
    misstate both the relationship and the legal limit.
    """
    politician_id, source_id = _seed_candidate(conn)
    _committee(conn, source_id, "C00000003", "Big Outside Group")
    db.upsert_donations_bulk(conn, [
        _ie(politician_id, source_id, "4020260010", "C00000003",
            "Big Outside Group", 750_000, "24E"),
    ])
    db.refresh_finance_views(conn)
    donor_names = [
        str(r[0]) for r in conn.execute(
            "SELECT committee_name FROM mv_top_committee_donors").fetchall()
    ]
    assert "Big Outside Group" not in donor_names


def test_memo_rows_do_not_double_count_a_spender(conn: db.Connection) -> None:
    """Same discipline as every other finance view: conduit detail lines are
    informational, and summing them would report the same dollars twice."""
    politician_id, source_id = _seed_candidate(conn)
    _committee(conn, source_id, "C00000003", "Big Outside Group")
    db.upsert_donations_bulk(conn, [
        _ie(politician_id, source_id, "4020260010", "C00000003",
            "Big Outside Group", 750_000, "24E"),
        _ie(politician_id, source_id, "4020260011", "C00000003",
            "Big Outside Group", 750_000, "24E", memo_cd="X"),
    ])
    db.refresh_finance_views(conn)
    assert _spenders(conn) == [("Big Outside Group", "supporting", 750_000)]


def test_named_spending_reconciles_with_the_total_shown_beside_it(
    conn: db.Connection,
) -> None:
    """The named list and the headline total are computed by different
    queries. If they disagree the page contradicts itself in public."""
    politician_id, source_id = _seed_candidate(conn)
    for n, (cmte, name, amount, tp) in enumerate((
        ("C00000003", "Group A", 500_000, "24E"),
        ("C00000004", "Group B", 250_000, "24E"),
        ("C00000005", "Group C", 90_000, "24A"),
    )):
        _committee(conn, source_id, cmte, name)
        db.upsert_donations_bulk(conn, [
            _ie(politician_id, source_id, f"402026020{n}", cmte, name, amount, tp)])
    db.refresh_finance_views(conn)

    finance = conn.execute(
        "SELECT ie_support, ie_oppose FROM mv_candidacy_finance "
        "WHERE fec_candidate_id = 'S6ME00001'").fetchone()
    assert finance is not None
    named = _spenders(conn)
    assert sum(s[2] for s in named if s[1] == "supporting") == int(finance[0])
    assert sum(s[2] for s in named if s[1] == "opposing") == int(finance[1])


# -- coverage disclosure --------------------------------------------------------
#
# The donor list looks complete and is not. We hold nearly every committee
# contribution and almost none of the itemized individual contributions, and
# individual money is the larger share of what campaigns raise. The export
# carries both sides of that ratio so the page can say so out loud.

def _totals(conn: db.Connection, politician_id: int, source_id: int,
            **overrides: Any) -> None:
    totals: dict[str, Any] = {
        "fec_candidate_id": "S6ME00001", "cycle": 2026,
        "politician_id": politician_id, "total_receipts": 1_000_000,
        "total_disbursements": 0, "cash_on_hand": 0, "debts_owed": 0,
        "individual_itemized": 600_000, "individual_unitemized": 300_000,
        "pac_contributions": 100_000, "coverage_end": "2026-06-30",
        "source_id": source_id,
    }
    totals.update(overrides)
    db.upsert_candidate_totals(conn, totals)


def _finance_export(conn: db.Connection) -> dict[str, Any]:
    cur = conn.execute(db.load_sql("export_finance"), {"cycle": 2026})
    columns = [d[0] for d in cur.description or []]
    row = cur.fetchone()
    assert row is not None
    return dict(zip(columns, row, strict=True))


def test_coverage_measures_individual_money_we_hold_against_the_official_total(
    conn: db.Connection,
) -> None:
    politician_id, source_id = _seed_candidate(conn)
    _totals(conn, politician_id, source_id)
    db.upsert_donations_bulk(conn, [
        _donation_row(politician_id, source_id, fec_sub_id="4020260301",
                      amount=5_000),  # committee money: not part of this ratio
        _donation_row(politician_id, source_id, fec_sub_id="4020260302",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="15", entity_tp="IND", amount=250),
    ])
    db.refresh_finance_views(conn)

    finance = _finance_export(conn)
    assert int(finance["individual_itemized_official"]) == 600_000
    assert int(finance["individual_itemized_loaded"]) == 250


def test_coordinated_party_spending_cannot_inflate_coverage_past_complete(
    conn: db.Connection,
) -> None:
    """Found by a pre-publish check, after the first version of this measure
    reported 388 candidates whose coverage exceeded 100 percent.

    Our committee sums include coordinated party expenditures and in-kind
    transfers, which the FEC's PAC contribution total does not count, and
    itemized filings routinely post-date the summary they belong to. A
    combined ratio therefore reported holding more money than exists, which
    discredits the disclosure it is supposed to be making. Coverage is
    measured on individual contributions alone, where neither distortion
    applies, so committee-side rows must not move it at all.
    """
    politician_id, source_id = _seed_candidate(conn)
    _totals(conn, politician_id, source_id, pac_contributions=1_000)
    db.upsert_donations_bulk(conn, [
        _donation_row(politician_id, source_id, fec_sub_id="4020260401",
                      transaction_tp="24C", amount=900_000),   # coordinated
        _donation_row(politician_id, source_id, fec_sub_id="4020260402",
                      transaction_tp="24Z", amount=400_000),   # in-kind
        _donation_row(politician_id, source_id, fec_sub_id="4020260403",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="15", entity_tp="IND", amount=250),
    ])
    db.refresh_finance_views(conn)

    finance = _finance_export(conn)
    held = int(finance["individual_itemized_loaded"])
    owed = int(finance["individual_itemized_official"])
    assert held == 250, "committee-side money leaked into the individual ratio"
    assert held <= owed, "coverage claims we hold more than the FEC says exists"


def test_unitemized_money_counts_against_neither_side_of_coverage(
    conn: db.Connection,
) -> None:
    """Small-dollar giving under the itemization threshold has no itemized
    record anywhere, not even at the FEC. Counting it as money we failed to
    load would report a coverage gap nobody could ever close."""
    politician_id, source_id = _seed_candidate(conn)
    _totals(conn, politician_id, source_id, individual_unitemized=900_000)
    db.refresh_finance_views(conn)
    assert int(_finance_export(conn)["individual_itemized_official"]) == 600_000


def test_coverage_is_zero_not_absent_when_we_hold_nothing(
    conn: db.Connection,
) -> None:
    """A candidate we have loaded no itemized rows for must report 0 held,
    not null. The page divides by these, and a null would render as a blank
    where the honest answer is 'none of it'."""
    politician_id, source_id = _seed_candidate(conn)
    _totals(conn, politician_id, source_id)
    db.refresh_finance_views(conn)
    assert int(_finance_export(conn)["individual_itemized_loaded"]) == 0


def test_a_conduit_that_nets_to_nothing_is_not_shown_as_having_bundled(
    conn: db.Connection,
) -> None:
    """Receipt lines can be negative when a contribution is reattributed or
    corrected. One real conduit nets below zero, and "bundled -$1,606" is
    arithmetically right and communicatively useless: on net that
    organisation routed nothing, so it is not listed as having routed
    something."""
    politician_id, source_id = _seed_candidate(conn)
    _committee(conn, source_id, "C00000031", "Refunded Conduit")
    _committee(conn, source_id, "C00000032", "Real Conduit")
    db.upsert_donations_bulk(conn, [
        _donation_row(politician_id, source_id, fec_sub_id="4020260501",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="15E", entity_tp="IND", amount=500,
                      conduit_cmte_id="C00000031"),
        _donation_row(politician_id, source_id, fec_sub_id="4020260502",
                      contributor_cmte_id=None, contributor_name="SMITH, ALEX",
                      transaction_tp="15E", entity_tp="IND", amount=-500,
                      conduit_cmte_id="C00000031"),
        _donation_row(politician_id, source_id, fec_sub_id="4020260503",
                      contributor_cmte_id=None, contributor_name="ROE, SAM",
                      transaction_tp="15E", entity_tp="IND", amount=900,
                      conduit_cmte_id="C00000032"),
    ])
    db.refresh_finance_views(conn)
    cur = conn.execute(db.load_sql("export_conduits"))
    names = [str(r[2]) for r in cur.fetchall()]
    assert names == ["Real Conduit"], f"got {names}"
