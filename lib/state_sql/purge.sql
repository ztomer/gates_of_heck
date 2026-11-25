-- lib/state_bounds.py's sweep, only once 0017 is applied (a document no longer holds them too): the
-- two large parts of a land's coverage risk, after 14 days; its own columns stay.
DELETE FROM land_cov_carry WHERE at < unixepoch() - 14 * 86400;
DELETE FROM land_cov_files WHERE at < unixepoch() - 14 * 86400;
