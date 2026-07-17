"""ETL loaders: reference data seeding and FEC bulk loads.

Every loader follows the same contract:
  * the raw downloaded/received bytes become a `sources` row before anything
    else is stored ("no source, no store");
  * writes are idempotent upserts on natural keys — safe to re-run;
  * each run is recorded in `ingestion_runs` with row-count stats.
"""
