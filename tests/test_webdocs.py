"""Pure tests for campaign-page link discovery and text extraction."""

from pipeline.webdocs import (
    MIN_TEXT_CHARS,
    discover_child_links,
    discover_issue_links,
    discover_press_index,
    extract_text,
    throttle_key,
)

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


# A house.gov layout, reduced from the four real gap-member sites diagnosed on
# 2026-08-17: no link anywhere carries "issue" in its path (the Issues
# dropdown is script-rendered), the About section fans out into logistics, and
# the substantive labels live in anchor TEXT, not in the URL.
HOUSE_GOV = b"""
<html><body><nav>
  <a href="/about">Meet the Member</a>
  <a href="/about/committees-and-caucuses">Committees and Caucuses</a>
  <a href="/about/staff-page">Staff Page</a>
  <a href="/about/our-district">Our District</a>
  <a href="/about/votes-and-legislation">Votes and Legislation</a>
  <a href="/contact/offices">Office Locations</a>
  <a href="/services/flags">Flags</a>
  <a href="/services/help-federal-agency">Help with a Federal Agency</a>
  <a href="/media/press-releases">Press Releases</a>
  <a href="/media/press-kit">Press Kit</a>
  <a href="/sites/files/issues-flyer.png">Our Issues (flyer)</a>
</nav></body></html>
"""


def test_anchor_text_finds_what_the_path_hides() -> None:
    """"Votes and Legislation" is the policy link; its path alone says so only
    via 'legislat', and other CMSes label it with paths no list anticipates."""
    links = discover_issue_links(HOUSE_GOV, "https://member.house.gov/")
    assert "https://member.house.gov/about/votes-and-legislation" in links


def test_junk_segments_do_not_eat_the_budget() -> None:
    """The original failure: 'about' matched staff pages, district maps and
    event calendars, and a real member got five pages of logistics while the
    issue content sat unfetched. Junk loses even when a keyword matches."""
    links = discover_issue_links(HOUSE_GOV, "https://member.house.gov/")
    assert "https://member.house.gov/about" in links
    for junk in ("committees-and-caucuses", "staff-page", "our-district",
                 "offices", "flags", "help-federal-agency", "press-kit"):
        assert all(junk not in link for link in links), junk


def test_binary_assets_never_match() -> None:
    links = discover_issue_links(HOUSE_GOV, "https://member.house.gov/")
    assert all(not link.endswith(".png") for link in links)


ISSUES_INDEX = b"""
<html><body>
  <a href="/issues/economy">Economy</a>
  <a href="/issues/education">Education</a>
  <a href="/issues/veterans">Veterans</a>
  <a href="/issues">Issues (self)</a>
  <a href="/about">About</a>
  <a href="https://elsewhere.example/issues/economy">External</a>
</body></html>
"""


def test_child_links_walk_one_level_under_the_index() -> None:
    """An /issues page on this CMS is a tile grid; the member's words live at
    /issues/<topic>. The walk must return children only: not the index itself,
    not siblings, not other hosts."""
    children = discover_child_links(ISSUES_INDEX, "https://member.house.gov/issues")
    assert children == [
        "https://member.house.gov/issues/economy",
        "https://member.house.gov/issues/education",
        "https://member.house.gov/issues/veterans",
    ]


def test_press_index_is_found_by_path_or_text_and_media_alone_is_not() -> None:
    assert discover_press_index(HOUSE_GOV, "https://member.house.gov/") == \
        "https://member.house.gov/media/press-releases"
    media_only = b'<a href="/media">Media</a><a href="/media/gallery">Photos</a>'
    assert discover_press_index(media_only, "https://member.house.gov/") is None


def test_press_release_slugs_stay_out_of_issue_discovery() -> None:
    """Slugs like ".../leads-legislation-establish-..." are keyword bait; left
    in, they arrive through issue discovery mislabelled and eat the budget the
    dedicated press walk exists to serve."""
    html = b'''
      <a href="/media/press-releases/leads-legislation-establish-reserve">Leads Legislation</a>
      <a href="/media/in-the-news/member-plans-new-push">In the News</a>
      <a href="/issues">Issues</a>
    '''
    links = discover_issue_links(html, "https://member.house.gov/")
    assert links == ["https://member.house.gov/issues"]


# -- politeness ------------------------------------------------------------

def test_official_site_subdomains_share_one_politeness_clock() -> None:
    """Every member's site is a subdomain of house.gov but they share
    servers. Spacing per subdomain would let 435 of them be hit at once."""
    assert throttle_key("https://cammack.house.gov/issues") == "house.gov"
    assert throttle_key("https://guest.house.gov/") == "house.gov"
    assert throttle_key("https://www.collins.senate.gov/x") == "senate.gov"


def test_unrelated_campaign_domains_get_their_own_clocks() -> None:
    """The reason this is per host at all: 3,000 campaigns on 3,000 servers
    were queued behind one global clock, which made a national pass take days
    while no server saw traffic worth throttling."""
    keys = {throttle_key(u) for u in (
        "https://www.grahamforsenate.com/", "https://abdulforsenate.com/issues",
        "https://alexbores.nyc")}
    assert len(keys) == 3
