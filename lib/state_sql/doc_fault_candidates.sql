SELECT x.n, x.body,
  json_patch(json_patch('{}', json_object('id', x.id, 'item', x.item, 'phase', x.phase, 'repo', x.repo, 'status', x.status, 'done_state', x.done_state, 'faults', json(nullif(nullif((SELECT json_group_array(c.value ORDER BY c.ord) FROM fault_candidate_lists c WHERE c.n = x.n AND c.kind = 'fault'), '[]'), '{}')), 'depends', json(nullif(nullif((SELECT json_group_array(c.value ORDER BY c.ord) FROM fault_candidate_lists c WHERE c.n = x.n AND c.kind = 'depend'), '[]'), '{}')))), coalesce(x.rest, '{}')) AS doc
FROM fault_candidates x
