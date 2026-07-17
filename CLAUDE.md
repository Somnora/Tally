# CLAUDE.md — Civic Transparency Platform ("Follow the Money")

## Mission

A non-partisan civic transparency platform for the November 2026 US midterms.
For every federal race (435 House + ~33 Senate seats), the app shows voters:

1. Who is running (incumbent + challengers) in THEIR district
2. Where each candidate's money comes from (donors, PACs, industries, outside spending)
3. What each candidate has promised (extracted from transcripts, press releases, campaign sites)
4. How incumbents' voting records align or conflict with those promises and donors

**Core editorial principle: evidence over verdicts.** The product shows receipts —
verbatim quotes with video timestamps, roll-call votes linked to Congress.gov,
donations linked to FEC records — and lets users judge. LLM-generated scores are
secondary, always displayed with their reasoning, citations, and a methodology link.

## Non-negotiable invariants (enforce these everywhere)

1. **No unverified quotes ship.** A promise is displayable only when
   `quote_verified = TRUE`, set by exact string-match of `verbatim_quote` against
   `documents.full_text` at the stated character offsets. LLM extraction output
   that fails the match is rejected and logged, never displayed.
2. **No uncited evaluations ship.** Every `promise_evaluations` row must have
   `evaluation_evidence` rows pointing at real `vote_id` / `donation_id` /
   `filing_uuid` / `document_id` records. Code must validate each citation
   (record exists AND supports the stated direction) before `validated = TRUE`.
   The export view already excludes evaluations with any unvalidated evidence.
3. **Provenance on everything.** Every ingested fact traces to a `sources` row
   (URL, retrieved_at, content_hash, raw payload). If you can't source it, don't store it.
4. **Neutrality in copy.** UI text, prompts, and generated summaries must be
   party-agnostic. Identical treatment, identical scoring pipeline, identical
   framing for all candidates. No loaded adjectives.
5. **Determinism where possible.** LLM calls at temperature 0, prompts versioned
   in git (`prompt_version` column), models pinned (`model_name` column).
   Evaluations are append-only: new model/prompt = new row, flip `is_current`.

## Architecture

```
[Lambda GPU instance(s)]                       [Static hosting: GitHub Pages]
  Ingestion pipeline (Python)                    Web app (read-only client)
  - Pydantic-AI + DBOS workflows                 - Loads SQLite snapshot via
  - faster-whisper transcription                   sql.js / wa-sqlite (HTTP range
  - Local LLM (DeepSeek / Qwen via vLLM)           requests if snapshot is large)
        |                                        - Address -> district lookup
        v                                              ^
  [PostgreSQL]  --(weekly export job)-->  [SQLite snapshot + version manifest
   full data, raw payloads                  on GitHub Releases / R2]
                                                       ^
                                           [Later: iOS + Tauri desktop apps
                                            download the same snapshot]
```

- Postgres is the system of record (schema: see `schema_additions.sql`, which
  layers on base tables `politicians, pacs, donations, promises, voting_records`).
- The app snapshot contains ONLY aggregates and verified/validated rows
  (see `app_export_*` views). Target snapshot size: < 150 MB. Raw itemized
  FEC data stays server-side; the app deep-links to fec.gov for raw records.
- DBOS system DB: Postgres (same instance, separate database), NOT SQLite.

## Data sources

| Source | Access | Use | Notes |
|---|---|---|---|
| FEC bulk downloads | https://www.fec.gov/data/browse-data/?tab=bulk-data | Initial load: candidate master, committee master, itemized contributions, independent expenditures | One-time ETL per cycle + weekly incremental files. Prefer bulk over API for volume. |
| OpenFEC API | api.open.fec.gov (key in env: `FEC_API_KEY`, 7,200 calls/hr) | Incremental updates, candidate lookups, filings since last run | Respect rate limit; use `min_last_update` style filters |
| Congress.gov API | api.congress.gov (key: `CONGRESS_GOV_API_KEY`) | Bills, roll-call votes, members, sponsorship | Official replacement for the defunct ProPublica Congress API |
| Senate LDA API | lda.senate.gov/api | Lobbying filings -> `lobbying_filings` / `lobbying_issues` | Parse bill numbers out of issue descriptions into `bill_numbers[]` |
| unitedstates/congress-legislators (GitHub) | YAML/CSV, no key | Seed `id_crosswalk` + `fec_candidate_ids` | The ID Rosetta Stone: bioguide <-> FEC <-> govtrack <-> CRP <-> ICPSR |
| OpenSecrets Bulk Data | Approved bulk account (educational license) | `industry_codes` (CRP catcodes), donor industry coding, independent expenditures | NOT an API (discontinued 2025). Download CSVs. Credit OpenSecrets in-app. Revolving Door data is off-limits. |
| Voteview (voteview.com) | CSV downloads | DW-NOMINATE ideology scores per member | Join on `icpsr_id` |
| YouTube Data API | key in gcloud (`YOUTUBE_API_KEY`) | Discover town halls / interviews per candidate; pull captions where available | Transcribe with faster-whisper on GPU when captions absent |
| Campaign sites + Wayback Machine | scraping + web.archive.org API | Promise pages; archived versions catch scrubbed promises | Store every snapshot as a `documents` row with `source_type` |
| Census Geocoder + TIGER shapefiles | free | Address -> congressional district for the app's "my ballot" view | Precompute district GeoJSON for the client |

Secrets live in environment variables / .env (gitignored). Never hardcode keys.

## Ingestion pipeline design

Reference implementation: `ingestion_blueprint.py`. Key patterns:

- **One DBOS workflow per candidate**, enqueued from a coordinator — never one
  giant loop over 435 districts. A failure on one candidate must not block others.
- Every workflow writes an `ingestion_runs` row (start/finish/status/stats).
- `@DBOS.step()` boundaries: each external fetch, each transcription, each LLM
  call is its own step so retries never repeat completed work.
- **Pre-digest before prompting.** Never put raw FEC/API payloads in prompts.
  SQL-aggregate first (top donors by industry, vote list filtered by topic),
  pass compact summaries.
- Pipeline stages per candidate:
  1. `sync_finance` — FEC incremental + OpenSecrets coding -> donations rollups
  2. `sync_votes` — Congress.gov roll calls (incumbents only)
  3. `sync_documents` — YouTube discovery, transcription, press releases, campaign pages
  4. `extract_promises` — chunked LLM extraction -> quote verification -> `promises`
  5. `evaluate_promises` — scoreable promises only -> evaluation + evidence citation -> validation
  6. `refresh_rollups` — refresh materialized views

## LLM extraction rules (stage 4-5 detail)

- Chunk transcripts to ~2-3k tokens with ~200 token overlap; pass chunk char-offset
  so extracted offsets are absolute.
- Extraction schema requires: verbatim_quote, char_start, char_end, topic,
  specificity (measurable | directional | rhetorical). Rhetorical promises are
  stored and displayed but `is_scoreable = FALSE` — never scored.
- Verify: `document.full_text[char_start:char_end] == verbatim_quote` (allow a
  fuzzy fallback: search for the quote if offsets drifted; if found, fix offsets;
  if not found anywhere, reject).
- Evaluation prompt receives ONLY: the verified promise, a pre-aggregated donor
  summary, and a filtered vote list — each item carrying its DB id. The model
  must return cited ids; code validates every id against the DB.
- Retries: use Pydantic-AI's validation-retry (ModelRetry) with the error appended.
- IMPORTANT: verify the installed pydantic-ai version's current API against its
  docs before writing agent code (e.g., `output_type` / `result.output` in recent
  versions). Pin exact versions in requirements.

## Repo layout (suggested)

```
/pipeline          # Python: DBOS workflows, steps, prompts/
  /prompts         # versioned prompt templates (referenced by prompt_version)
  /etl             # FEC bulk loaders, OpenSecrets CSV loaders, crosswalk seeder
/db                # schema.sql (base) + schema_additions.sql + migrations/
/export            # Postgres -> SQLite snapshot job + manifest generator
/web               # static web app (GitHub Pages)
/docs              # methodology page (public!), data source credits
```

## Milestones (build in this order)

1. DB up: base schema + `schema_additions.sql`; seed `id_crosswalk` and
   `industry_codes`; load FEC bulk candidate/committee masters; populate
   `races`/`candidacies` for cycle 2026.
2. Finance pipeline end-to-end for ONE state's races; materialized rollups.
3. Votes pipeline for those incumbents.
4. Documents + extraction + verification for ~5 candidates; inspect quality
   manually before scaling.
5. Evaluation + evidence validation; human-review CLI for flagged rows.
6. SQLite export + manifest; web app rendering one district end-to-end.
7. Scale to all districts; weekly cron on Lambda instance.

## Conventions

- Python 3.12, ruff + pyright, pytest. Type-hint everything crossing a boundary.
- All DB access through a thin repository module; SQL in files, not f-strings.
- Idempotent upserts keyed on natural ids (fec ids, bioguide, filing_uuid, content_hash).
- Log per-candidate cost/token stats into `ingestion_runs.stats`.
- Any scraping: respect robots.txt, throttle, identify with a contact UA string.
- The public methodology page in /docs is a first-class deliverable, not an afterthought.
