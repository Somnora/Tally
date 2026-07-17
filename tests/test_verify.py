"""Tests for the quote-verification gate (pipeline/verify.py).

This is the invariant that keeps hallucinated quotes out of the product, so
it gets tested before any LLM code exists.
"""

from pipeline.verify import verify_quote

DOCUMENT = (
    "Thank you all for coming. I will vote to cap insulin prices at $35 "
    "for every American, and I mean every American. We also need to talk "
    "about housing. I will vote to cap insulin prices at $35 next session."
)


def test_exact_match_at_stated_offsets() -> None:
    quote = "I will vote to cap insulin prices at $35"
    start = DOCUMENT.index(quote)
    result = verify_quote(DOCUMENT, 0, quote, start, start + len(quote))
    assert result.verified
    assert result.method == "exact"
    assert DOCUMENT[result.char_start : result.char_end] == quote


def test_chunk_offset_makes_relative_offsets_absolute() -> None:
    chunk_offset = 40
    quote = "every American"
    absolute = DOCUMENT.index(quote)
    relative = absolute - chunk_offset
    result = verify_quote(DOCUMENT, chunk_offset, quote, relative, relative + len(quote))
    assert result.verified
    assert result.method == "exact"
    assert result.char_start == absolute


def test_drifted_offsets_are_relocated_by_search() -> None:
    quote = "We also need to talk about housing."
    true_start = DOCUMENT.index(quote)
    drifted = true_start + 7  # LLM was off by a few characters
    result = verify_quote(DOCUMENT, 0, quote, drifted, drifted + len(quote))
    assert result.verified
    assert result.method == "relocated"
    assert result.char_start == true_start
    assert DOCUMENT[result.char_start : result.char_end] == quote


def test_relocated_offsets_always_slice_back_to_the_quote() -> None:
    # Repeated phrase: relocation must still return a span that slices to the
    # quote, even though it picks the first occurrence.
    quote = "I will vote to cap insulin prices at $35"
    result = verify_quote(DOCUMENT, 0, quote, 9999, 9999 + len(quote))
    assert result.verified
    assert DOCUMENT[result.char_start : result.char_end] == quote


def test_quote_not_in_document_is_rejected() -> None:
    result = verify_quote(DOCUMENT, 0, "I promise to abolish the IRS", 0, 28)
    assert not result.verified
    assert result.method == "rejected"
    assert (result.char_start, result.char_end) == (-1, -1)


def test_paraphrased_quote_is_rejected_not_fuzzy_matched() -> None:
    # Close paraphrase of text that IS in the document — must still reject:
    # verbatim means verbatim.
    result = verify_quote(DOCUMENT, 0, "I will cap insulin prices at $35", 24, 56)
    assert not result.verified
    assert result.method == "rejected"


def test_empty_quote_is_rejected() -> None:
    result = verify_quote(DOCUMENT, 0, "", 0, 0)
    assert not result.verified
    assert result.method == "rejected"


def test_negative_absolute_offsets_fall_back_to_search() -> None:
    quote = "Thank you all for coming."
    result = verify_quote(DOCUMENT, 0, quote, -50, -25)
    assert result.verified
    assert result.method == "relocated"
    assert result.char_start == 0
