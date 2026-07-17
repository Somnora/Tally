"""Quote verification: the anti-hallucination gate for promise extraction.

An extracted promise is displayable ONLY if its verbatim_quote actually
appears in the source document. This module is pure (no DB, no LLM) so the
gate can be tested exhaustively before any extraction code exists.

Decision ladder (from the ingestion blueprint):
  1. exact  — the quote matches full_text at the stated offsets; keep them.
  2. relocated — the quote appears elsewhere (the LLM's offsets drifted);
     fix the offsets to where the quote actually is.
  3. rejected — the quote is nowhere in the document. It does not get
     stored as verified, period. Rejections are counted in ingestion stats.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QuoteVerification:
    """Outcome of verifying one extracted quote against its source document.

    char_start/char_end are ABSOLUTE offsets into the document's full_text,
    valid only when verified is True (they are -1 on rejection so they can
    never be mistaken for real offsets).
    """

    verified: bool
    method: Literal["exact", "relocated", "rejected"]
    char_start: int
    char_end: int


def verify_quote(
    full_text: str,
    chunk_offset: int,
    quote: str,
    char_start: int,
    char_end: int,
) -> QuoteVerification:
    """Verify an extracted quote against the full document text.

    Args:
        full_text: the complete document text (promises verify against the
            whole document, not the chunk, so relocation can cross chunks).
        chunk_offset: absolute position of the extraction chunk's first
            character within full_text; the LLM reports chunk-relative offsets.
        quote: the verbatim quote as returned by the extraction model.
        char_start / char_end: chunk-relative offsets reported by the model.
    """
    if not quote:
        # str.find("") returns 0 — an empty quote would always "verify".
        return QuoteVerification(False, "rejected", -1, -1)

    absolute_start = chunk_offset + char_start
    absolute_end = chunk_offset + char_end
    if 0 <= absolute_start < absolute_end and full_text[absolute_start:absolute_end] == quote:
        return QuoteVerification(True, "exact", absolute_start, absolute_end)

    found_at = full_text.find(quote)
    if found_at != -1:
        return QuoteVerification(True, "relocated", found_at, found_at + len(quote))

    return QuoteVerification(False, "rejected", -1, -1)
