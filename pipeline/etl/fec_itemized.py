"""FEC itemized contributions for ONE state's candidates (Milestone 2).

Two bulk files feed the donations table:

  pas2  (~6 MB)  — contributions from committees to candidates, plus
                   independent expenditures (24E support / 24A oppose).
                   Small enough to parse whole; filtered by target CAND_ID.
  indiv (~1.7 GB) — itemized individual contributions. Downloaded once to
                   data/raw/ (cached across runs), then STREAMED: rows are
                   kept only when the recipient committee belongs to one of
                   the state's candidates. Only the file path goes in the
                   sources row; 1.7 GB does not belong in a database column.

Run:
    uv run python -m pipeline.etl.fec_itemized --state ME
    uv run python -m pipeline.etl.fec_itemized --state ME --skip-indiv
    uv run python -m pipeline.etl.fec_itemized --state ME --refresh-downloads

Idempotent on FEC sub_id; amended filings update rows in place.

Field-mapping note: in the pas2 file NAME/CITY/STATE/ZIP describe the
RECIPIENT, so those columns are left NULL there (the donor is the committee,
whose details live in the committees table). In the indiv file they describe
the CONTRIBUTOR and are stored as donor_* columns.
"""

import argparse
import csv
import hashlib
import io
import logging
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from pipeline import db
from pipeline.config import get_settings
from pipeline.etl.fec_bulk import BULK_BASE, USER_AGENT, download, parse_header, parse_pipe_file

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000
PROGRESS_EVERY = 5_000_000
IE_TYPES = frozenset({"24A", "24E"})


@dataclass(frozen=True)
class StateContext:
    """Everything a row mapper needs to decide 'ours?' and resolve FKs."""

    cycle: int
    politician_by_fec: dict[str, int]   # fec_candidate_id -> politician_id
    cand_by_cmte: dict[str, str]        # cmte_id -> fec_candidate_id (state committees)
    known_committees: set[str]          # all cmte_ids in the master (FK guard)


def parse_fec_date(raw: str) -> date | None:
    """FEC bulk dates are MMDDYYYY; blanks and garbage become None."""
    raw = raw.strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%m%d%Y").date()
    except ValueError:
        return None


def parse_amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        return None


def pas2_row_to_donation(
    row: dict[str, str], ctx: StateContext, source_id: int, stats: dict[str, int]
) -> dict[str, Any] | None:
    """Map a pas2 row targeting one of our candidates; None = not ours."""
    cand_id = row.get("CAND_ID", "").strip()
    politician_id = ctx.politician_by_fec.get(cand_id)
    if politician_id is None:
        return None
    amount = parse_amount(row.get("TRANSACTION_AMT", ""))
    if amount is None:
        stats["bad_amount"] += 1
        return None

    transaction_tp = row.get("TRANSACTION_TP", "").strip()
    contributor_cmte = row.get("CMTE_ID", "").strip()
    if contributor_cmte not in ctx.known_committees:
        stats["contributor_not_in_master"] += 1
        contributor_cmte = ""
    recipient_cmte = row.get("OTHER_ID", "").strip()
    if transaction_tp in IE_TYPES or recipient_cmte not in ctx.known_committees:
        recipient_cmte = ""

    return {
        "fec_sub_id": row["SUB_ID"].strip(),
        "recipient_cmte_id": recipient_cmte or None,
        "fec_candidate_id": cand_id,
        "politician_id": politician_id,
        "contributor_name": None,           # donor is the committee (see module note)
        "contributor_cmte_id": contributor_cmte or None,
        "amount": amount,
        "contributed_at": parse_fec_date(row.get("TRANSACTION_DT", "")),
        "cycle": ctx.cycle,
        "transaction_tp": transaction_tp or None,
        "entity_tp": row.get("ENTITY_TP", "").strip() or None,
        "transaction_pgi": row.get("TRANSACTION_PGI", "").strip() or None,
        "employer": None,
        "occupation": None,
        "donor_city": None,
        "donor_state": None,
        "donor_zip": None,
        "image_num": row.get("IMAGE_NUM", "").strip() or None,
        "memo_cd": row.get("MEMO_CD", "").strip() or None,
        "memo_text": row.get("MEMO_TEXT", "").strip() or None,
        "source_id": source_id,
    }


def indiv_row_to_donation(
    row: dict[str, str], ctx: StateContext, source_id: int, stats: dict[str, int]
) -> dict[str, Any] | None:
    """Map an indiv row whose recipient committee is one of ours; None = not ours."""
    recipient_cmte = row.get("CMTE_ID", "").strip()
    cand_id = ctx.cand_by_cmte.get(recipient_cmte)
    if cand_id is None:
        return None
    amount = parse_amount(row.get("TRANSACTION_AMT", ""))
    if amount is None:
        stats["bad_amount"] += 1
        return None

    return {
        "fec_sub_id": row["SUB_ID"].strip(),
        "recipient_cmte_id": recipient_cmte,
        "fec_candidate_id": cand_id,
        "politician_id": ctx.politician_by_fec[cand_id],
        "contributor_name": row.get("NAME", "").strip() or None,
        "contributor_cmte_id": None,
        "amount": amount,
        "contributed_at": parse_fec_date(row.get("TRANSACTION_DT", "")),
        "cycle": ctx.cycle,
        "transaction_tp": row.get("TRANSACTION_TP", "").strip() or None,
        "entity_tp": row.get("ENTITY_TP", "").strip() or None,
        "transaction_pgi": row.get("TRANSACTION_PGI", "").strip() or None,
        "employer": row.get("EMPLOYER", "").strip() or None,
        "occupation": row.get("OCCUPATION", "").strip() or None,
        "donor_city": row.get("CITY", "").strip() or None,
        "donor_state": row.get("STATE", "").strip() or None,
        "donor_zip": row.get("ZIP_CODE", "").strip() or None,
        "image_num": row.get("IMAGE_NUM", "").strip() or None,
        "memo_cd": row.get("MEMO_CD", "").strip() or None,
        "memo_text": row.get("MEMO_TEXT", "").strip() or None,
        "source_id": source_id,
    }


def download_to_file(url: str, dest: Path, refresh: bool) -> str:
    """Stream a large download to disk (cached); returns the file's sha256."""
    if dest.exists() and not refresh:
        logger.info("using cached %s (%.1f MB)", dest, dest.stat().st_size / 1048576)
        return hashlib.sha256(dest.read_bytes()).hexdigest()

    logger.info("downloading %s -> %s", url, dest)
    digest = hashlib.sha256()
    with httpx.stream(
        "GET", url, timeout=httpx.Timeout(120.0), follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as response:
        response.raise_for_status()
        with dest.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
                digest.update(chunk)
    logger.info("downloaded %.1f MB", dest.stat().st_size / 1048576)
    return digest.hexdigest()


def stream_filtered_indiv(
    zip_path: Path, header: list[str], wanted_cmte_ids: set[str], stats: dict[str, int]
) -> Iterator[dict[str, str]]:
    """Yield dict rows for wanted recipient committees; cheap index check first.

    The recipient CMTE_ID column is tested by position before any dict is
    built, so scanning ~60M rows only materializes the handful we keep.
    """
    cmte_index = header.index("CMTE_ID")
    with zipfile.ZipFile(zip_path) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as binary_stream:
            text = io.TextIOWrapper(binary_stream, encoding="latin-1", newline="")
            for values in csv.reader(text, delimiter="|", quoting=csv.QUOTE_NONE):
                stats["indiv_rows_scanned"] += 1
                if stats["indiv_rows_scanned"] % PROGRESS_EVERY == 0:
                    logger.info("scanned %dM indiv rows, kept %d so far",
                                stats["indiv_rows_scanned"] // 1_000_000,
                                stats["indiv_rows_loaded"])
                if len(values) <= cmte_index or values[cmte_index] not in wanted_cmte_ids:
                    continue
                yield dict(zip(header, (v.strip() for v in values), strict=False))


def load(state: str, cycle: int, skip_indiv: bool, refresh_downloads: bool) -> dict[str, int]:
    suffix = str(cycle)[-2:]
    settings = get_settings()
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {
        "pas2_rows_scanned": 0, "pas2_rows_loaded": 0,
        "indiv_rows_scanned": 0, "indiv_rows_loaded": 0,
        "bad_amount": 0, "contributor_not_in_master": 0,
    }

    with db.connect() as conn:
        run_id = db.start_run(conn, "fec_itemized_load")
        conn.commit()  # keep the run row even if the load below fails
        try:
            candidacies = db.state_candidacies(conn, state, cycle)
            if not candidacies:
                raise SystemExit(f"no candidacies for {state} cycle {cycle}; run fec_bulk first")
            ctx = StateContext(
                cycle=cycle,
                politician_by_fec={c.fec_candidate_id: c.politician_id for c in candidacies},
                cand_by_cmte=db.state_committee_map(conn, state, cycle),
                known_committees=db.all_committee_ids(conn),
            )
            logger.info("%s: %d candidates, %d linked committees",
                        state, len(ctx.politician_by_fec), len(ctx.cand_by_cmte))

            # --- pas2: committee money + independent expenditures ---
            pas2_header_url = f"{BULK_BASE}/data_dictionaries/pas2_header_file.csv"
            pas2_url = f"{BULK_BASE}/{cycle}/pas2{suffix}.zip"
            pas2_header = parse_header(download(pas2_header_url))
            pas2_zip = download(pas2_url)
            pas2_source_id = db.insert_source(
                conn, source_type="fec_bulk_pas2", url=pas2_url,
                content_hash=hashlib.sha256(pas2_zip).hexdigest(), raw_payload=pas2_zip,
            )
            batch: list[dict[str, Any]] = []
            for row in parse_pipe_file(pas2_zip, pas2_header):
                stats["pas2_rows_scanned"] += 1
                donation = pas2_row_to_donation(row, ctx, pas2_source_id, stats)
                if donation is not None:
                    batch.append(donation)
            db.upsert_donations_bulk(conn, batch)
            stats["pas2_rows_loaded"] = len(batch)
            logger.info("pas2: %d of %d rows are %s candidates'",
                        len(batch), stats["pas2_rows_scanned"], state)

            # --- indiv: itemized individual contributions (streamed) ---
            if not skip_indiv:
                indiv_header_url = f"{BULK_BASE}/data_dictionaries/indiv_header_file.csv"
                indiv_url = f"{BULK_BASE}/{cycle}/indiv{suffix}.zip"
                indiv_header = parse_header(download(indiv_header_url))
                indiv_path = settings.raw_data_dir / f"indiv{suffix}.zip"
                indiv_digest = download_to_file(indiv_url, indiv_path, refresh_downloads)
                indiv_source_id = db.insert_source(
                    conn, source_type="fec_bulk_indiv", url=indiv_url,
                    content_hash=indiv_digest, raw_path=str(indiv_path),
                )
                wanted = set(ctx.cand_by_cmte)
                batch = []
                for row in stream_filtered_indiv(indiv_path, indiv_header, wanted, stats):
                    donation = indiv_row_to_donation(row, ctx, indiv_source_id, stats)
                    if donation is None:
                        continue
                    batch.append(donation)
                    if len(batch) >= BATCH_SIZE:
                        db.upsert_donations_bulk(conn, batch)
                        stats["indiv_rows_loaded"] += len(batch)
                        batch = []
                db.upsert_donations_bulk(conn, batch)
                stats["indiv_rows_loaded"] += len(batch)
                logger.info("indiv: kept %d of %d scanned rows",
                            stats["indiv_rows_loaded"], stats["indiv_rows_scanned"])

            db.refresh_finance_views(conn)
            db.finish_run(conn, run_id, "succeeded", stats)
        except Exception as exc:
            conn.rollback()
            db.finish_run(conn, run_id, "failed", {}, error=str(exc))
            conn.commit()
            raise
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="two-letter state, e.g. ME")
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--skip-indiv", action="store_true",
                        help="load only the small pas2 file (fast validation)")
    parser.add_argument("--refresh-downloads", action="store_true",
                        help="re-download cached bulk files")
    args = parser.parse_args()
    stats = load(args.state.upper(), args.cycle, args.skip_indiv, args.refresh_downloads)
    for key in sorted(stats):
        logger.info("%-28s %d", key, stats[key])


if __name__ == "__main__":
    main()
