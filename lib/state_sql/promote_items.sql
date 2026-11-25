-- lib/state_cutover.py, after the strip and before 0019: a spawn's old `item`, in no row of its own
-- yet, as its last item (0019 refuses while one is left).
INSERT INTO spawn_lists(n, kind, ord, value, at)
SELECT n, 'item', (SELECT coalesce(max(ord) + 1, 0) FROM spawn_lists l WHERE l.n = s.n AND l.kind = 'item'), item, at FROM agent_spawns s
WHERE item IS NOT NULL AND NOT EXISTS (SELECT 1 FROM spawn_lists l WHERE l.n = s.n AND l.kind = 'item' AND l.value = s.item);
