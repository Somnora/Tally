"""Stage 1 — sync_finance: FEC incremental totals + donor industry rollups.

Milestone 2. Pulls candidate totals and itemized contributions since the last
run (OpenFEC API with min_last_update-style filters; bulk weekly files for
volume), stores every payload as a sources row, applies OpenSecrets catcodes
to code donors by industry, and refreshes per-candidate donation rollups.
"""

from pipeline.db import Connection
from pipeline.stages import StageStats


def sync_finance(conn: Connection, politician_id: int, cycle: int, run_id: int) -> StageStats:
    """Refresh finance data for one candidate.

    Contract: every API payload becomes a sources row before any donations
    row is written; donation upserts key on fec_sub_id; the FEC rate limit
    (7,500/hr, backup key on overflow) is respected by the HTTP layer.
    """
    raise NotImplementedError("Milestone 2")
