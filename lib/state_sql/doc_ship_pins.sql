SELECT x.result, x.body,
  json_patch(json_patch('{}', json_object('tag', x.tag, 'ruff', x.ruff, 'sweep', x.sweep, 'repos', json(nullif(nullif((SELECT json_group_object(c.repo, json_patch('{}', json_object('status', c.status, 'said', c.said))) FROM ship_pin_repos c WHERE c.result = x.result), '[]'), '{}')))), coalesce(x.rest, '{}')) AS doc
FROM ship_pins x
