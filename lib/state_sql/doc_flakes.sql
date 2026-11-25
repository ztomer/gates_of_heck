SELECT x.n, x.body,
  json_patch(json_patch('{}', json_object('epoch', x.epoch, 'id', x.flake, 'first', x.first, 'second', x.second, 'repo', x.repo, 'sha', x.sha, 'site', x.site, 'log', CASE WHEN coalesce(x.log_first, x.log_second) IS NOT NULL THEN json_object('first', x.log_first, 'second', x.log_second) END, 'filed', x.filed, 'load', x.load, 'at', CASE WHEN x.flake IS NOT NULL THEN strftime('%Y-%m-%dT%H:%M:%S+00:00', x.epoch, 'unixepoch') END, 'test', CASE WHEN x.flake IS NOT NULL THEN x.test END)), coalesce(x.rest, '{}')) AS doc
FROM flakes x
