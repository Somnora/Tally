"""Pure tests for campaign-page link discovery and text extraction."""

from pipeline.webdocs import MIN_TEXT_CHARS, discover_issue_links, extract_text

HOMEPAGE = b"""
<html><body>
  <nav>
    <a href="/">Home</a>
    <a href="/issues">Issues</a>
    <a href="/issues">Issues duplicate</a>
    <a href="/about-jane">About Jane</a>
    <a href="/donate">Donate</a>
    <a href="https://other-site.example/issues">External issues page</a>
    <a href="/platform#healthcare">Platform</a>
    <a href="mailto:info@example.test">Email us</a>
  </nav>
  <main><p>Placeholder</p></main>
</body></html>
"""


def test_discover_issue_links_same_site_dedupe_and_keywords() -> None:
    links = discover_issue_links(HOMEPAGE, "https://jane.example/")
    assert "https://jane.example/issues" in links
    assert "https://jane.example/platform" in links       # fragment stripped
    assert "https://jane.example/about-jane" in links
    assert all("other-site.example" not in link for link in links)
    assert all("donate" not in link for link in links)
    assert len(links) == len(set(links))                  # deduped


def test_discover_issue_links_respects_cap() -> None:
    many = b"".join(
        f'<a href="/issues-{i}">Issue {i}</a>'.encode() for i in range(20)
    )
    links = discover_issue_links(b"<html><body>" + many + b"</body></html>",
                                 "https://jane.example/", cap=6)
    assert len(links) == 6


def test_extract_text_rejects_thin_pages() -> None:
    thin = b"<html><body><main><p>Donate now!</p></main></body></html>"
    assert extract_text(thin) is None


def test_extract_text_keeps_substantive_pages() -> None:
    paragraphs = "".join(
        f"<p>Position statement number {i}: we support better infrastructure "
        f"funding for rural communities across the state of Maine.</p>"
        for i in range(12)
    )
    html = f"<html><body><main><article><h1>Issues</h1>{paragraphs}</article></main></body></html>"
    text = extract_text(html.encode("utf-8"))
    assert text is not None
    assert len(text) >= MIN_TEXT_CHARS
    assert "rural communities" in text
