-- The DO UPDATE is a deliberate no-op so RETURNING always yields the race_id
-- (plain DO NOTHING returns no row on conflict).
INSERT INTO races (cycle, state, office, district, senate_class, is_special, source_id)
VALUES (%(cycle)s, %(state)s, %(office)s, %(district)s, %(senate_class)s, %(is_special)s, %(source_id)s)
ON CONFLICT ON CONSTRAINT races_natural_key
DO UPDATE SET cycle = EXCLUDED.cycle
RETURNING race_id
