"""Join the two FEC identities a member acquires when they change seats.

The FEC issues a candidate id per office, so a sitting House member running
for the Senate has two of them and nothing in the bulk data connects the
pair. The consequence was visible on the site: Haley Stevens's Senate page
showed a sitting Congresswoman with no voting record and no promises, as
though we had never heard of her, while 644 roll calls sat in the database
under her House id.

Linking people is the most dangerous edit in this codebase. Get it wrong and
one member's votes appear on another member's page, which is a false claim
about a named person and the exact failure everything else here is built to
prevent. So a link is made only on two independent kinds of evidence:

  1. The FEC's own candidate master lists both ids with a byte-identical
     CAND_NAME, the same office state, and the same party, across two
     different offices, with exactly one of them declared incumbent. This is
     one source, one format, self-reported by the candidate. It is not a name
     matched across two systems, which is the thing we refuse to do.
  2. Our own database independently agrees: the incumbent-side id resolves to
     a politician who has a bioguide id AND real roll-call votes, and the
     other side has none. FEC's incumbency flag alone is not enough, because
     it is candidate-declared and stale entries carry it: Tom Cotton and
     Peter Meijer both present as incumbents against old candidacies.

Anything failing either test is skipped, not guessed. On the 2026 file that
is 28 name matches reduced to 14 links, with 105 ambiguous groups never
considered.

Run:  uv run python -m pipeline.etl.link_candidacies --cycle 2026
      uv run python -m pipeline.etl.link_candidacies --cycle 2026 --apply
"""

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pipeline import db
from pipeline.etl.fec_bulk import (
    BULK_BASE,
    download,
    parse_header,
    parse_pipe_file,
    store_download,
)

logger = logging.getLogger(__name__)

BASIS = (
    "FEC candidate master lists both ids with identical CAND_NAME, office "
    "state and party across two offices with one incumbent; our database "
    "confirms roll-call votes on the incumbent id and none on the other"
)


@dataclass(frozen=True)
class NamePair:
    """Two FEC ids the candidate master presents as one person."""

    name: str
    incumbent_fec_id: str
    other_fec_id: str


def pair_candidate_ids(rows: Sequence[dict[str, str]]) -> list[NamePair]:
    """FEC-internal evidence only: no cross-system name matching happens here.

    Grouped on the exact filed name plus office state plus party. A group
    qualifies only when it spans more than one office and contains exactly one
    self-declared incumbent; two incumbents means the member already moved and
    both records are live, which is not a case this can settle.
    """
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get("CAND_NAME", "").strip(),
               row.get("CAND_OFFICE_ST", "").strip(),
               row.get("CAND_PTY_AFFILIATION", "").strip())
        if not all(key):
            continue
        groups.setdefault(key, []).append(row)

    pairs: list[NamePair] = []
    for (name, _state, _party), group in groups.items():
        if len({r.get("CAND_OFFICE", "").strip() for r in group}) < 2:
            continue
        incumbents = [r for r in group if r.get("CAND_ICI", "").strip() == "I"]
        others = [r for r in group if r.get("CAND_ICI", "").strip() != "I"]
        if len(incumbents) != 1 or not others:
            continue
        for other in others:
            pairs.append(NamePair(
                name=name,
                incumbent_fec_id=incumbents[0]["CAND_ID"].strip(),
                other_fec_id=other["CAND_ID"].strip(),
            ))
    return pairs


def load(cycle: int, apply_changes: bool) -> dict[str, int]:
    suffix = str(cycle)[-2:]
    stats = {"pairs_from_fec": 0, "linked": 0, "skipped_no_candidacy": 0,
             "skipped_no_votes": 0, "skipped_target_has_votes": 0,
             "skipped_no_bioguide": 0, "already_linked": 0,
             "candidacies_repointed": 0, "donations_repointed": 0}

    header = parse_header(download(f"{BULK_BASE}/data_dictionaries/cn_header_file.csv"))
    url = f"{BULK_BASE}/{cycle}/cn{suffix}.zip"
    raw = download(url)
    pairs = pair_candidate_ids(parse_pipe_file(raw, header))
    stats["pairs_from_fec"] = len(pairs)

    with db.connect() as conn:
        run_id = db.start_run(conn, "link_candidacies")
        conn.commit()
        try:
            source_id = store_download(conn, "fec_bulk_cn_identity", url, raw)
            for pair in pairs:
                resolved = db.resolve_identity_pair(
                    conn, pair.incumbent_fec_id, pair.other_fec_id, cycle)
                if resolved is None:
                    stats["skipped_no_candidacy"] += 1
                    continue
                if (resolved.already_linked
                        or resolved.incumbent_politician_id
                        == resolved.other_politician_id):
                    stats["already_linked"] += 1
                    continue
                if not resolved.incumbent_bioguide:
                    stats["skipped_no_bioguide"] += 1
                    continue
                if resolved.incumbent_votes == 0:
                    stats["skipped_no_votes"] += 1
                    continue
                if resolved.other_votes > 0:
                    stats["skipped_target_has_votes"] += 1
                    continue

                logger.info("link %s: %s -> %s (%d votes)", pair.name,
                            pair.incumbent_fec_id, pair.other_fec_id,
                            resolved.incumbent_votes)
                stats["linked"] += 1
                if apply_changes:
                    moved = db.apply_identity_link(
                        conn,
                        incumbent_fec_id=pair.incumbent_fec_id,
                        other_fec_id=pair.other_fec_id,
                        politician_id=resolved.incumbent_politician_id,
                        superseded_politician_id=resolved.other_politician_id,
                        basis=BASIS, source_id=source_id, cycle=cycle,
                    )
                    stats["candidacies_repointed"] += moved[0]
                    stats["donations_repointed"] += moved[1]
            if not apply_changes:
                conn.rollback()
                logger.warning("DRY RUN: nothing written. Re-run with --apply")
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
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--apply", action="store_true",
                        help="write the links; without it the run is a preview")
    args = parser.parse_args()
    stats = load(args.cycle, args.apply)
    for key in sorted(stats):
        logger.info("%-28s %d", key, stats[key])


if __name__ == "__main__":
    main()
