"""Pure parser tests for senate.gov roll-call XML (no network)."""

from datetime import date

from pipeline.senate_gov import (
    parse_vote_detail,
    parse_vote_menu,
    vote_page_url,
    vote_url,
)

MENU_XML = b"""<?xml version="1.0" encoding="UTF-8"?><vote_summary>
  <congress>119</congress>
  <session>2</session>
  <congress_year>2026</congress_year>
  <votes>
    <vote>
      <vote_number>00199</vote_number>
      <vote_date>16-Jul</vote_date>
      <issue>S.J.Res. 198</issue>
      <question>On the Motion to Proceed
         </question>
      <result>Rejected</result>
      <vote_tally><yeas>46</yeas><nays>50</nays></vote_tally>
    </vote>
    <vote>
      <vote_number>00198</vote_number>
      <issue>PN1234</issue>
      <question>On the Nomination</question>
      <result>Confirmed</result>
    </vote>
  </votes>
</vote_summary>"""

DETAIL_XML = b"""<?xml version="1.0" encoding="UTF-8"?><roll_call_vote>
  <congress>119</congress>
  <session>2</session>
  <vote_number>00199</vote_number>
  <vote_date>July 16, 2026, 05:30 PM</vote_date>
  <question>On the Motion to Proceed</question>
  <vote_result>Rejected</vote_result>
  <document>
    <document_name>S.J.Res. 198</document_name>
  </document>
  <members>
    <member>
      <last_name>Collins</last_name>
      <first_name>Susan</first_name>
      <party>R</party>
      <state>ME</state>
      <vote_cast>Yea</vote_cast>
      <lis_member_id>S252</lis_member_id>
    </member>
    <member>
      <last_name>King</last_name>
      <first_name>Angus</first_name>
      <party>I</party>
      <state>ME</state>
      <vote_cast>Not Voting</vote_cast>
      <lis_member_id>S363</lis_member_id>
    </member>
  </members>
</roll_call_vote>"""


def test_parse_vote_menu() -> None:
    entries = parse_vote_menu(MENU_XML)
    assert len(entries) == 2
    assert entries[0].number == 199
    assert entries[0].result == "Rejected"
    assert entries[0].issue == "S.J.Res. 198"
    assert entries[1].number == 198


def test_parse_vote_detail() -> None:
    detail = parse_vote_detail(DETAIL_XML)
    assert detail.number == 199
    assert detail.question == "On the Motion to Proceed"
    assert detail.result == "Rejected"
    assert detail.document_name == "S.J.Res. 198"
    assert detail.voted_at == date(2026, 7, 16)
    assert len(detail.members) == 2
    assert detail.members[0].lis_id == "S252"
    assert detail.members[0].vote_cast == "Yea"
    assert detail.members[1].vote_cast == "Not Voting"


def test_urls_zero_pad_vote_numbers() -> None:
    assert vote_url(119, 2, 7).endswith("vote1192/vote_119_2_00007.xml")
    assert vote_page_url(119, 2, 199).endswith("vote1192/vote_119_2_00199.htm")
