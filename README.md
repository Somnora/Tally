# Tally

A non-partisan civic transparency platform for the November 2026 US midterms.
For every federal race, it shows who is running, where their money comes
from, what they promised, and how their votes align with both. Full brief:
[CLAUDE.md](CLAUDE.md). Public methodology: [docs/methodology.md](docs/methodology.md).

## Status: Milestone 2 complete (finance pipeline, Maine pilot)

Database schema, reference data, 2026 FEC masters, and the full finance
pipeline for one pilot state (Maine) are live: itemized contributions and
independent expenditures from bulk files, official totals from the OpenFEC
API via durable per-candidate DBOS workflows, and rollup materialized views
with an official-vs-loaded consistency check. Extraction and evaluation
stages remain typed stubs (`pipeline/stages/`).

| Loaded | Count |
|---|---|
| id_crosswalk (current members) | 537 |
| committees (2026 master) | 20,173 |
| races (435 House, 33 Senate class 2, 2 specials) | 470 |
| politicians / candidacies (2026) | 4,079 |
| donations (ME pilot: itemized + IEs) | 99,280 |
| candidate_totals (ME, official FEC aggregates) | 25 |

## Setup

Requires Python 3.12 (via [uv](https://docs.astral.sh/uv/)) and a local
PostgreSQL 15+.

```sh
uv sync                                  # install pinned dependencies
cp .env.example .env                     # then fill in your API keys
createdb civic && createdb civic_dbos    # app data + DBOS workflow state
uv run python -m pipeline.migrate        # apply schema (see db/README.md)
```

## Loaders (all idempotent; re-running never duplicates rows)

```sh
# 1. ID crosswalk: bioguide <-> FEC <-> govtrack <-> ICPSR <-> OpenSecrets
uv run python -m pipeline.etl.seed_crosswalk

# 2. OpenSecrets CRP industry codes (needs the manually downloaded file;
#    see the module docstring for where to get it)
uv run python -m pipeline.etl.seed_industry_codes path/to/CRP_Categories.txt

# 3. FEC candidate + committee masters, races, candidacies for a cycle
uv run python -m pipeline.etl.fec_bulk --cycle 2026

# 4. Itemized contributions + independent expenditures for one state
#    (first run downloads the 1.7 GB indiv file once; cached afterwards)
uv run python -m pipeline.etl.fec_itemized --state ME

# 5. Official FEC totals per candidate (durable DBOS workflows, one per
#    candidate; needs FEC_API_KEY in .env and the civic_dbos database)
uv run python -m pipeline.workflows --state ME

# 6. Verification report: official vs loaded, with divergences flagged
uv run python -m pipeline.report --state ME
```

Every loader records the raw download as a `sources` row (URL, retrieval
time, sha256, payload) before storing derived rows, and logs its run and row
counts in `ingestion_runs`.

## Tests and checks

```sh
uv run pytest          # unit + DB tests (DB tests skip if Postgres is down)
uv run ruff check .    # lint
uv run pyright         # types (strict on pipeline/)
```

The most important tested code right now is `pipeline/verify.py`, the quote
verification gate: extracted promise quotes must exactly match the source
document or they are rejected. It is pure logic, tested ahead of any LLM
integration.

## Layout

```
pipeline/          config, db repository, migrate runner, verify gate
pipeline/etl/      seed_crosswalk, seed_industry_codes, fec_bulk
pipeline/stages/   typed stubs for milestones 2-5 (finance, votes,
                   documents, extraction, evaluation)
db/                schema.sql, schema_additions.sql, sql/ (all queries),
                   migrations/ (see db/README.md)
docs/              methodology.md (public), ingestion blueprint
tests/             pytest suite + fixtures
data/raw/          bulk downloads (gitignored)
```

## Credits

Campaign finance data: Federal Election Commission. Legislative data:
Congress.gov. Donor industry classification: OpenSecrets, under their bulk
data license. Member IDs: the unitedstates/congress-legislators project.
Ideology scores: Voteview.
