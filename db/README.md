# Database layout and migrations

## Files

- `schema.sql` — base entities (politicians, committees/pacs, donations,
  voting_records, promises, races, candidacies). Baseline migration **0001**.
- `schema_additions.sql` — provenance (`sources`), documents, ingestion runs,
  id crosswalk, industry codes, lobbying, evaluations + evidence, and the
  `app_export_*` views. Baseline migration **0002**.
- `migrations/` — every schema change made *after* the baseline, as numbered
  SQL files starting at `0003_...sql`.
- `sql/` — query files used by the repository module (`pipeline/db.py`).
  All application SQL lives here, never inline in Python.

## How to apply

```sh
uv run python -m pipeline.migrate            # applies to DATABASE_URL from .env
```

The runner keeps a `schema_migrations` table (filename, sha256, applied_at).
On each run it applies, in order, anything not yet recorded: first the two
baseline files, then `migrations/*.sql` sorted by filename. Each file runs in
its own transaction — a failed migration rolls back cleanly and stops the run.

## Why migrations matter

A database outlives any single version of the code. Once real data is loaded
(days of FEC downloads, transcriptions, LLM extractions), you can't just drop
the database and re-run `schema.sql` — you'd lose everything. So the schema
must only ever move *forward*, one recorded, repeatable step at a time:

1. **Every environment converges on the same schema.** Laptop, Lambda box,
   a fresh checkout — run the migrate command and you're at the same state.
2. **Applied files are immutable.** The runner stores each file's sha256 and
   refuses to continue if an applied file was edited afterwards. If you need
   to change something, write a new `ALTER TABLE` migration; don't rewrite
   history. (During early development, before data matters, it's fine to
   `dropdb civic && createdb civic` and re-apply from scratch instead.)
3. **The schema's history is reviewable in git.** Each migration is a small
   diff you can read, review, and if necessary write a compensating
   migration for.

## Conventions

- Migration files: `NNNN_short_description.sql`, numbered consecutively from
  `0003`. Never renumber, never edit an applied file.
- Keep migrations self-contained plain SQL — no psql meta-commands (`\i`),
  since the runner executes them through psycopg, not psql.
