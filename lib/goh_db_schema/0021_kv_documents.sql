-- old-writers: refuse
-- applied-by: goh state contract
CREATE TABLE refuse_kv(what TEXT NOT NULL, n INTEGER NOT NULL CHECK (n = 0)) STRICT;
INSERT INTO refuse_kv SELECT 'a pin document its rows do not hold: goh state backfill', count(*) FROM kv WHERE name IN ('pin_push', 'pin_sweeps', 'wf_pin.pins') AND coalesce(CASE WHEN json_valid(body) THEN body ->> '$.rows' END, 0) <> 15;
INSERT INTO refuse_kv SELECT 'a module linux_unsafe has no row of: goh state backfill', count(*) FROM duration_kv d, json_each(CASE WHEN json_valid(d.value) THEN d.value ELSE '{}' END) e WHERE d.key = 'linux_unsafe' AND e.type = 'text' AND NOT EXISTS (SELECT 1 FROM linux_unsafe u WHERE u.module = e.key);
INSERT INTO refuse_kv SELECT 'remote_rates is in no row: goh state backfill', count(*) FROM duration_kv d WHERE d.key = 'remote_rates' AND NOT EXISTS (SELECT 1 FROM duration_kv p WHERE p.key = 'remote_mac_width');
DROP TABLE refuse_kv;
DELETE FROM kv WHERE name IN ('pin_push', 'pin_sweeps', 'wf_pin.pins', 'hook-ledgers.imported');
DELETE FROM duration_kv WHERE key IN ('remote_rates', 'linux_unsafe');
CREATE TRIGGER kv_rows_hold_it BEFORE INSERT ON kv WHEN NEW.name IN ('pin_push', 'pin_sweeps', 'wf_pin.pins', 'hook-ledgers.imported') BEGIN
  SELECT RAISE(ABORT, 'its rows hold it (0021): no document');
END;
CREATE TRIGGER duration_kv_rows_hold_it BEFORE INSERT ON duration_kv WHEN NEW.key IN ('remote_rates', 'linux_unsafe') BEGIN
  SELECT RAISE(ABORT, 'its rows hold it (0021): no document');
END;
