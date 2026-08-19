"""Reading a campaign's declared website off its FEC Form 1 filing.

The value of this source is that the campaign names its own site, so no step
here may guess. Every test below is either a shape a real 2026 filing takes
(measured against 250 sampled candidates on 2026-08-18) or a case where
guessing would put one candidate's words on another candidate's page.
"""

from typing import Any

from pipeline.etl.campaign_sites import is_non_site, normalize_url, websites_from_payload


def _cmte(cmte_id: str, website: str | None, designation: str = "P") -> dict[str, Any]:
    return {"committee_id": cmte_id, "website": website, "designation": designation}


# -- normalize_url ----------------------------------------------------------

def test_bare_uppercase_host_is_the_common_filed_shape() -> None:
    """Most filings look like this; the FEC stores the field upper-cased."""
    assert normalize_url("WWW.KENPAXTON.COM") == "https://www.kenpaxton.com"


def test_declared_scheme_and_trailing_slash_collapse_to_one_url() -> None:
    """Two committees filing the same site must not become two rows."""
    assert normalize_url("HTTPS://COLINALLRED.COM/") == normalize_url("colinallred.com")


def test_http_scheme_is_honoured_not_upgraded() -> None:
    """A campaign that filed http may have no certificate; forcing https
    would turn a reachable site into a fetch failure we would then report as
    the campaign having no site."""
    assert normalize_url("http://example-campaign.org") == "http://example-campaign.org"


def test_path_case_survives_but_host_case_does_not() -> None:
    """Hosts are case-insensitive, paths are not: lowercasing a path turns a
    working address into a 404."""
    assert normalize_url("EXAMPLE.COM/Issues/Health") == "https://example.com/Issues/Health"


def test_placeholders_are_not_websites() -> None:
    for filed in ("N/A", "n/a", "None", "TBD", "-", "--", ".", "  ", "unknown"):
        assert normalize_url(filed) is None, filed


def test_a_real_domain_containing_a_placeholder_word_survives() -> None:
    """Placeholders match whole, so these are untouched."""
    assert normalize_url("nonesuchforcongress.com") == "https://nonesuchforcongress.com"
    assert normalize_url("tbdfororegon.org") == "https://tbdfororegon.org"


def test_an_email_address_is_not_a_website() -> None:
    assert normalize_url("info@examplecampaign.com") is None


def test_a_mistyped_domain_is_rejected_rather_than_repaired() -> None:
    """A real 2026 filing reads 'VONDRASFORCONGRESS,ORG'. The comma is
    obviously a period, and we still refuse: inventing a domain the campaign
    did not file is a worse failure than recording that we have no site."""
    assert normalize_url("VONDRASFORCONGRESS,ORG") is None


def test_free_text_is_not_a_website() -> None:
    assert normalize_url("under construction") is None
    assert normalize_url("see facebook page") is None


# -- is_non_site ------------------------------------------------------------

def test_social_and_fundraising_hosts_are_not_campaign_sites() -> None:
    """Real filings name these. They carry no issue pages to read and their
    robots.txt closes the door, so they are counted, not crawled."""
    for url in ("https://facebook.com/PETERLARSONFORCONGRESS",
                "https://www.facebook.com/somebody",
                "https://secure.actblue.com/donate/x",
                "https://linktr.ee/candidate"):
        assert is_non_site(url), url


def test_a_host_merely_ending_in_a_blocked_name_is_still_a_campaign_site() -> None:
    """phoenix.com ends with 'x.com' as a substring. Suffix matching has to
    respect the label boundary or it would silently discard real sites."""
    assert not is_non_site("https://phoenix.com")
    assert not is_non_site("https://votebeforex.com")
    assert is_non_site("https://x.com/handle")


# -- websites_from_payload --------------------------------------------------

def test_only_authorized_committees_speak_for_a_candidate() -> None:
    """A PAC or party committee (designation U) may name its own website.
    Storing that as the candidate's would put a spender's words on a
    candidate's page, which is the misattribution that gave one senator
    $51.5M of party money earlier in this project."""
    found = websites_from_payload({"results": [
        _cmte("C00000001", "OUTSIDEGROUP.ORG", designation="U"),
        _cmte("C00000002", "REALCAMPAIGN.COM", designation="P"),
    ]})
    assert [url for _, _, url in found] == ["https://realcampaign.com"]


def test_the_filed_value_is_kept_verbatim_beside_the_normalized_one() -> None:
    """The filing is the evidence. Storing only our cleaned-up version would
    have us assert the campaign said something it did not."""
    found = websites_from_payload({"results": [_cmte("C00000001", " WWW.EXAMPLE.COM ")]})
    assert found == [("C00000001", "WWW.EXAMPLE.COM", "https://www.example.com")]


def test_two_committees_filing_the_same_site_yield_one_row() -> None:
    found = websites_from_payload({"results": [
        _cmte("C00000001", "EXAMPLE.COM"),
        _cmte("C00000002", "https://example.com/", designation="A"),
    ]})
    assert len(found) == 1


def test_two_committees_filing_different_sites_yield_both() -> None:
    """Both were declared; we do not get to pick which one the campaign meant."""
    found = websites_from_payload({"results": [
        _cmte("C00000001", "EXAMPLE.COM"),
        _cmte("C00000002", "OTHEREXAMPLE.COM", designation="A"),
    ]})
    assert len(found) == 2


def test_missing_and_unusable_values_produce_nothing() -> None:
    assert websites_from_payload({"results": [
        _cmte("C00000001", None),
        _cmte("C00000002", "N/A"),
        _cmte("C00000003", ""),
        {"committee_id": "C00000004", "designation": "P"},
    ]}) == []


def test_an_empty_payload_is_not_an_error() -> None:
    assert websites_from_payload({}) == []
    assert websites_from_payload({"results": []}) == []


def test_a_campaign_on_a_free_site_builder_is_still_a_campaign_site() -> None:
    """These are small campaigns, which are the ones least likely to be
    covered anywhere else. Filtering them out because the host looks cheap
    would rebuild by hand the bias this whole pass exists to remove."""
    assert not is_non_site("https://janedoe.wixsite.com/campaign")
    assert not is_non_site("https://smithforcongress.godaddysites.com")
