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


# -- rung 3: normalized matching (whitespace / typographic folding) ------------

WEB_DOCUMENT = (
    "Our Plan\n\nHealth Care\nChellie believes access to care is a right —\n"
    "she’ll fight to “lower drug prices” for every family.\nRead more below."
)


def test_whitespace_only_mismatch_is_recovered_not_rejected() -> None:
    # Model copied verbatim but collapsed the newline into a space.
    quote = "Chellie believes access to care is a right"
    doc = WEB_DOCUMENT.replace(
        "believes access", "believes\naccess"
    )
    result = verify_quote(doc, 0, quote, 0, len(quote))
    assert result.verified
    assert result.method == "normalized"
    # The document slice is the stored text: same words, document's spacing.
    assert doc[result.char_start:result.char_end].split() == quote.split()


def test_typographic_punctuation_fold_is_recovered() -> None:
    # Model emitted straight quotes/apostrophe/dash for the doc's curly ones.
    quote = 'she\'ll fight to "lower drug prices" for every family.'
    result = verify_quote(WEB_DOCUMENT, 0, quote, 0, len(quote))
    assert result.verified
    assert result.method == "normalized"
    stored = WEB_DOCUMENT[result.char_start:result.char_end]
    assert stored == "she’ll fight to “lower drug prices” for every family."


def test_normalized_rung_still_rejects_paraphrase() -> None:
    result = verify_quote(WEB_DOCUMENT, 0, "she will fight to lower drug costs", 0, 30)
    assert not result.verified
    assert result.method == "rejected"


def test_normalized_offsets_slice_document_exactly() -> None:
    quote = "access  to   care is a right"  # extra internal whitespace from model
    result = verify_quote(WEB_DOCUMENT, 0, quote, 0, len(quote))
    assert result.verified
    assert WEB_DOCUMENT[result.char_start:result.char_end] == "access to care is a right"
