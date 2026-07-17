"""Stage 2 — sync_votes: Congress.gov roll-call votes for incumbents.

Milestone 3. Incumbents only (challengers have no voting record). Every
stored vote deep-links to its Congress.gov page — votes are receipts.
"""

from pipeline.db import Connection
from pipeline.stages import StageStats


def sync_votes(conn: Connection, politician_id: int, congress: int, run_id: int) -> StageStats:
    """Sync roll-call votes for one incumbent.

    Contract: no-op for politicians without a bioguide_id; upserts key on
    (politician_id, congress, chamber, session, roll_call_number); every
    API payload becomes a sources row first.
    """
    raise NotImplementedError("Milestone 3")
