"""Quote verification: the anti-hallucination gate for promise extraction.

An extracted promise is displayable ONLY if its verbatim_quote actually
appears in the source document. This module is pure (no DB, no LLM) so the
gate can be tested exhaustively before any LLM code exists.

Decision ladder:
  1. exact      — the quote matches full_text at the stated offsets.
  2. relocated  — the quote appears elsewhere (the LLM's offsets drifted);
     offsets are corrected to where the quote actually is.
  3. normalized — the quote matches after folding whitespace runs and
     typographic punctuation (curly quotes, long dashes). Models routinely
     normalize these when copying; the DOCUMENT's original span is what
     gets stored, so the displayed text is still exactly the source's.
  4. rejected   — the quote is nowhere in the document. It is never stored
     as verified. Rejections are persisted for QA (extraction_rejects).

Callers must store the document slice full_text[char_start:char_end], not
the model's text — identical for rungs 1-2, and the honest text for rung 3.
"""

from dataclasses import dataclass
from typing import Literal

# Typographic characters models commonly "simplify" while copying.
# Length-preserving 1:1 translation, so offset maps stay valid.
_PUNCTUATION_FOLD = str.maketrans({
    "‘": "'", "’": "'",   # curly single quotes
    "“": '"', "”": '"',   # curly double quotes
    "–": "-", "—": "-",   # en / em dash
    " ": " ",                  # no-break space
})


@dataclass(frozen=True)
class QuoteVerification:
    """Outcome of verifying one extracted quote against its source document.

    char_start/char_end are ABSOLUTE offsets into the document's full_text,
    valid only when verified is True (they are -1 on rejection so they can
    never be mistaken for real offsets).
    """

    verified: bool
    method: Literal["exact", "relocated", "normalized", "rejected"]
    char_start: int
    char_end: int


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Fold punctuation and collapse whitespace runs to single spaces.

    Returns the normalized string plus a map from each normalized index to
    the index of the corresponding character in the ORIGINAL text, so a
    match found in normalized space can be translated back to real offsets.
    """
    folded = text.translate(_PUNCTUATION_FOLD)  # length-preserving
    chars: list[str] = []
    origin: list[int] = []
    previous_was_space = False
    for index, ch in enumerate(folded):
        if ch.isspace():
            if previous_was_space:
                continue
            chars.append(" ")
            origin.append(index)
            previous_was_space = True
        else:
            chars.append(ch)
            origin.append(index)
            previous_was_space = False
    return "".join(chars), origin


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

    # Rung 3: whitespace/typography-tolerant match. The tolerance applies to
    # MATCHING only; the caller stores the document's original span.
    normalized_doc, origin_map = _normalize_with_map(full_text)
    normalized_quote, _ = _normalize_with_map(quote)
    normalized_quote = normalized_quote.strip()
    if normalized_quote:
        found_norm = normalized_doc.find(normalized_quote)
        if found_norm != -1:
            original_start = origin_map[found_norm]
            original_end = origin_map[found_norm + len(normalized_quote) - 1] + 1
            return QuoteVerification(True, "normalized", original_start, original_end)

    return QuoteVerification(False, "rejected", -1, -1)
