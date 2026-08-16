-- Name lookup for CLI callers. An exact match sorts first so that a common
-- surname (there are two dozen politicians named Collins) resolves cleanly
-- when the caller types the full name; anything else comes back as several
-- rows and the caller refuses to guess.
SELECT politician_id, full_name
FROM politicians
WHERE full_name = %(name)s OR full_name ILIKE %(name)s
ORDER BY (full_name = %(name)s) DESC, full_name
