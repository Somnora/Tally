"""Bill metadata: key normalization, payload parsing, and the vote join.

The generated bill_key column is the load-bearing piece here. If it maps
two spellings of the same bill apart, or maps a nomination onto a real
bill, the evaluation stage cites the wrong legislation. So it is tested
against the exact spellings that appear in the live voting_records table.
"""

from datetime import date

import pytest

from pipeline import db
from pipeline.etl.load_bills import (
    newest_summary,
    split_bill_key,
    strip_html,
    subject_terms,
)

# -- pure parsing --------------------------------------------------------------

def test_split_bill_key_covers_every_type_in_the_vote_table() -> None:
    assert split_bill_key("HR-8595") == ("hr", 8595)
    assert split_bill_key("S-1071") == ("s", 1071)
    assert split_bill_key("HRES-1009") == ("hres", 1009)
    assert split_bill_key("SRES-195") == ("sres", 195)
    assert split_bill_key("HJRES-104") == ("hjres", 104)
    assert split_bill_key("SJRES-10") == ("sjres", 10)
    assert split_bill_key("HCONRES-108") == ("hconres", 108)
    assert split_bill_key("SCONRES-22") == ("sconres", 22)


def test_split_bill_key_refuses_rather_than_guesses() -> None:
    # A nomination is not a bill; building /bill/119/pn/11 would fetch nonsense.
    assert split_bill_key("PN-11") is None
    assert split_bill_key("QQ-1") is None
    assert split_bill_key("HR-") is None
    assert split_bill_key("") is None


def test_strip_html_leaves_readable_prose() -> None:
    raw = "<p><strong>Title</strong></p><p>This bill provides funds.</p>"
    assert strip_html(raw) == "Title This bill provides funds."


def test_newest_summary_prefers_the_latest_action() -> None:
    payload = {
        "summaries": [
            {"actionDate": "2026-04-30", "text": "<p>Introduced version.</p>"},
            {"actionDate": "2026-07-29", "text": "<p>Passed House version.</p>"},
        ]
    }
    assert newest_summary(payload) == "Passed House version."


def test_newest_summary_is_none_when_absent() -> None:
    assert newest_summary({"summaries": []}) is None
    assert newest_summary({}) is None


def test_subject_terms_deduplicates_and_sorts() -> None:
    payload = {
        "subjects": {
            "legislativeSubjects": [
                {"name": "Health care coverage and access"},
                {"name": "Accounting and auditing"},
                {"name": "Health care coverage and access"},
            ],
            "policyArea": {"name": "Health"},
        }
    }
    assert subject_terms(payload) == [
        "Accounting and auditing",
        "Health care coverage and access",
    ]


def test_subject_terms_empty_rather_than_placeholder() -> None:
    # A bill with no subjects can never be topic-matched. That must be
    # visible in the data, not hidden behind a stand-in value.
    assert subject_terms({}) == []
    assert subject_terms({"subjects": {"legislativeSubjects": []}}) == []


# -- the generated join key ----------------------------------------------------

def _politician(conn: db.Connection, name: str) -> int:
    # sources enforces "payload or path" at the schema level, so even a test
    # row has to carry its bytes.
    source_id = db.insert_source(
        conn, source_type="test", url=f"https://example.test/{name}",
        content_hash=f"hash-{name}", raw_payload=b"{}",
    )
    # politicians enforces "bioguide or FEC id": nobody is stored without a
    # real identifier to join on.
    bioguide = "T" + str(abs(hash(name)) % 10**6).zfill(6)
    cur = conn.execute(
        "INSERT INTO politicians (full_name, bioguide_id, source_id) "
        "VALUES (%s, %s, %s) RETURNING politician_id",
        (name, bioguide, source_id),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _vote(conn: db.Connection, politician_id: int, roll: int, bill_number: str | None) -> None:
    conn.execute(
        "INSERT INTO voting_records (politician_id, congress, chamber, session, "
        "roll_call_number, bill_number, position, congress_gov_url, source_id) "
        "SELECT %s, 119, 'house', 1, %s, %s, 'yea', 'https://example.test/v', "
        "min(source_id) FROM sources",
        (politician_id, roll, bill_number),
    )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("HR 8595", "HR-8595"),
        ("H.R. 8595", "HR-8595"),      # same bill, other spelling
        ("S.J.Res. 10", "SJRES-10"),
        ("SJRES 10", "SJRES-10"),
        ("HCONRES 108", "HCONRES-108"),
        ("PN11-22", None),             # nomination, not a bill
        (None, None),
    ],
)
def test_bill_key_normalizes_every_spelling(
    conn: db.Connection, stored: str | None, expected: str | None
) -> None:
    politician_id = _politician(conn, f"Key Test {stored!r}")
    _vote(conn, politician_id, roll=1, bill_number=stored)
    cur = conn.execute(
        "SELECT bill_key FROM voting_records WHERE politician_id = %s", (politician_id,)
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == expected


def test_backfill_queue_lists_only_undescribed_bills(conn: db.Connection) -> None:
    politician_id = _politician(conn, "Queue Test")
    _vote(conn, politician_id, roll=1, bill_number="HR 8595")
    _vote(conn, politician_id, roll=2, bill_number="H.R. 8595")  # same bill again
    _vote(conn, politician_id, roll=3, bill_number="PN11-22")    # never a bill

    pending = db.bill_keys_needing_metadata(conn, politician_id)
    assert pending == [(119, "HR-8595")], "two spellings are one bill; PN is not one"

    db.upsert_bill(
        conn, congress=119, bill_key="HR-8595", bill_type="hr", bill_number=8595,
        title="An appropriations Act", policy_area="Economics and Public Finance",
        subjects=["Appropriations"], summary_text="Provides FY2027 appropriations.",
        introduced_date=date(2026, 4, 30), latest_action="Received in the Senate.",
        latest_action_date=date(2026, 7, 29), sponsor_bioguide="C001053",
        congress_gov_url="https://api.congress.gov/v3/bill/119/hr/8595", source_id=None,
    )
    assert db.bill_keys_needing_metadata(conn, politician_id) == []


def test_upsert_bill_is_idempotent_and_refreshes(conn: db.Connection) -> None:
    common = {
        "congress": 119, "bill_key": "HR-1", "bill_type": "hr", "bill_number": 1,
        "policy_area": "Health", "subjects": ["Health"], "summary_text": None,
        "introduced_date": None, "latest_action_date": None, "sponsor_bioguide": None,
        "congress_gov_url": "https://api.congress.gov/v3/bill/119/hr/1",
        "source_id": None,
    }
    first = db.upsert_bill(conn, title="Introduced title",
                           latest_action="Introduced in House", **common)
    second = db.upsert_bill(conn, title="Engrossed title",
                            latest_action="Passed House", **common)
    assert first == second, "same bill must not create a second row"

    cur = conn.execute(
        "SELECT title, latest_action FROM bills WHERE bill_id = %s", (first,)
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "Engrossed title"
    assert row[1] == "Passed House"


def test_politician_lookup_refuses_ambiguous_names(conn: db.Connection) -> None:
    _politician(conn, "Susan M. Collins")
    _politician(conn, "COLLINS, CHRIS")
    assert db.politician_id_by_name(conn, "Susan M. Collins") is not None
    assert db.politician_id_by_name(conn, "Nobody At All") is None
    with pytest.raises(ValueError, match="matches several"):
        db.politician_id_by_name(conn, "%Collins%")
