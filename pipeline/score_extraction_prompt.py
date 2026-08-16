"""Score an extraction prompt against the labelled gold set, writing nothing.

Re-extraction is destructive. The extract stage deletes a document's promises
before reinserting them, and promise_reviews.promise_id cascades on delete, so
re-running extraction to try a new prompt would silently take all 107 human
and triage review verdicts with it, along with the promise_id linkage the gold
set is built on. That is a bad way to find out whether v3 is an improvement.

So this runs the candidate prompt over the same documents, through the same
quote-verification gate, and compares what comes out against
data/review/gold_v2.jsonl WITHOUT touching the database.

Matching is by character-span overlap rather than string equality, because a
better prompt legitimately trims or extends a span while still extracting the
same promise. Gold spans come from the promises table; candidate spans come
from verify_quote. Two spans are the same promise when they overlap by at
least half the shorter one.

Three numbers matter, and the third is the one that keeps this honest:

  leaks_reextracted   of the 47 quotes the reviewers rejected, how many the
                      candidate prompt still pulls out. Lower is better.
  good_retained       of the 71 real promises, how many survive. This is the
                      guard rail: a prompt that extracts nothing scores
                      perfectly on leaks and destroys the product.
  novel               spans the candidate extracted that overlap no gold row
                      at all. These are UNLABELLED. They may be real promises
                      v2 missed, or a new failure class. The gold set cannot
                      say, and this tool does not guess.

Needs a live model endpoint. Run:
  uv run python -m pipeline.score_extraction_prompt --prompt-version extract_v3
"""

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from pipeline import db
from pipeline.config import get_settings
from pipeline.stages.extract_promises import (
    ExtractionResult,
    build_agent,
    chunk_document,
    load_prompt,
)
from pipeline.verify import verify_quote

logger = logging.getLogger(__name__)

GOLD_PATH = Path("data/review/gold_v2.jsonl")
MIN_OVERLAP_RATIO = 0.5


@dataclass(frozen=True)
class Span:
    document_id: int
    char_start: int
    char_end: int

    def overlaps(self, other: "Span") -> bool:
        if self.document_id != other.document_id:
            return False
        overlap = min(self.char_end, other.char_end) - max(self.char_start, other.char_start)
        if overlap <= 0:
            return False
        shorter = min(self.char_end - self.char_start, other.char_end - other.char_start)
        return shorter > 0 and overlap / shorter >= MIN_OVERLAP_RATIO


def gold_spans(conn: db.Connection) -> dict[int, tuple[Span, bool, str]]:
    """promise_id -> (span, should_extract, verdict), for labelled promises."""
    labels: dict[int, tuple[bool, str]] = {}
    with GOLD_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                labels[int(row["promise_id"])] = (
                    bool(row["should_extract"]), str(row.get("verdict", "")),
                )
    out: dict[int, tuple[Span, bool, str]] = {}
    rows = conn.execute(
        "SELECT promise_id, document_id, char_start, char_end FROM promises "
        "WHERE promise_id = ANY(%s)", (list(labels),)
    ).fetchall()
    for promise_id, document_id, start, end in rows:
        should_extract, verdict = labels[int(promise_id)]
        out[int(promise_id)] = (
            Span(int(document_id), int(start), int(end)), should_extract, verdict,
        )
    return out


def extract_spans(
    agent: Agent[None, ExtractionResult], document: db.DocumentForExtraction
) -> list[Span]:
    """Run the candidate prompt over one document. Writes nothing."""
    spans: list[Span] = []
    for chunk_offset, chunk_text in chunk_document(document.full_text):
        result = agent.run_sync(chunk_text).output
        for promise in result.promises:
            verification = verify_quote(
                document.full_text, chunk_offset, promise.verbatim_quote,
                promise.char_start, promise.char_end,
            )
            # Same gate as production: an unverifiable quote was never
            # really extracted, so it must not count for or against a prompt.
            if verification.verified:
                spans.append(Span(document.document_id, verification.char_start,
                                  verification.char_end))
    return spans


def main() -> None:
    parser = argparse.ArgumentParser(description="Score an extraction prompt offline")
    parser.add_argument("--prompt-version", default="extract_v3")
    parser.add_argument("--limit-documents", type=int,
                        help="stop after N documents (smoke test)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    if not settings.vllm_base_url or not settings.local_model:
        parser.error("set VLLM_BASE_URL and LOCAL_MODEL in .env (start a GPU first)")

    # Fail fast if the prompt does not exist, before any model time is spent.
    load_prompt(args.prompt_version)

    with db.connect() as conn:
        gold = gold_spans(conn)
        document_ids = sorted({span.document_id for span, _, _ in gold.values()})
        if args.limit_documents:
            document_ids = document_ids[: args.limit_documents]
        rows = conn.execute(
            "SELECT document_id, doc_type, title, url, full_text FROM documents "
            "WHERE document_id = ANY(%s) ORDER BY document_id", (document_ids,)
        ).fetchall()
        documents = [
            db.DocumentForExtraction(int(r[0]), str(r[1]),
                                     None if r[2] is None else str(r[2]),
                                     str(r[3]), str(r[4]))
            for r in rows
        ]

    import pipeline.stages.extract_promises as stage
    original = stage.PROMPT_VERSION
    stage.PROMPT_VERSION = args.prompt_version
    try:
        agent = build_agent()
    finally:
        stage.PROMPT_VERSION = original

    print(f"Scoring {args.prompt_version} over {len(documents)} documents "
          f"against {len(gold)} labelled promises. Nothing is written.\n")

    found: list[Span] = []
    for index, document in enumerate(documents, start=1):
        found.extend(extract_spans(agent, document))
        print(f"  {index}/{len(documents)} documents, {len(found)} verified spans so far")

    matched: set[int] = set()
    for promise_id, (span, _, _) in gold.items():
        if any(span.overlaps(candidate) for candidate in found):
            matched.add(promise_id)
    novel = [c for c in found
             if not any(span.overlaps(c) for span, _, _ in gold.values())]

    bad = {pid for pid, (_, should, _) in gold.items() if not should}
    good = {pid for pid, (_, should, _) in gold.items() if should}
    leaks = matched & bad
    retained = matched & good
    kept = len(leaks) + len(retained)

    print(f"\n{args.prompt_version} vs gold ({len(good)} real promises, "
          f"{len(bad)} extraction errors)")
    print(f"  leaks re-extracted   {len(leaks):>3}/{len(bad)}   "
          f"(v2 extracted all {len(bad)})")
    print(f"  real promises kept   {len(retained):>3}/{len(good)}")
    if kept:
        print(f"  precision on labelled spans   {len(retained)}/{kept} = "
              f"{len(retained) / kept:.0%}   (v2 was {len(good)}/{len(good) + len(bad)} = "
              f"{len(good) / (len(good) + len(bad)):.0%})")
    print(f"  novel spans (UNLABELLED, gold cannot judge these)   {len(novel)}")
    print("\nA novel span is not automatically a win or a loss. It is a real "
          "promise v2 missed, or a new failure class, and only review can say "
          "which.")


if __name__ == "__main__":
    main()
