INSERT INTO industry_codes (catcode, catname, catorder, industry, sector, sector_long, source_id)
VALUES (%(catcode)s, %(catname)s, %(catorder)s, %(industry)s, %(sector)s, %(sector_long)s, %(source_id)s)
ON CONFLICT (catcode) DO UPDATE SET
    catname     = EXCLUDED.catname,
    catorder    = EXCLUDED.catorder,
    industry    = EXCLUDED.industry,
    sector      = EXCLUDED.sector,
    sector_long = EXCLUDED.sector_long,
    source_id   = EXCLUDED.source_id
