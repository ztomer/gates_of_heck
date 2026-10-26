"""A fake `gh` for the Actions-storage sweep's tests: no network, state in a JSON file.

State (FAKE_GH_STATE): {"repos": [gh repo list rows], "data": {"owner/name": {"artifacts": [...],
"caches": [...], "retention": days, "fail": bool, "hang": bool}}}. Every call is appended to
FAKE_GH_LOG, one line each, so a test can assert what was (and was not) DELETEd or PUT. A list read
is printed the way `gh api --paginate` prints it: one JSON object per page of two, concatenated.
"""

FAKE_GH = """\
#!/usr/bin/env python3
import json, os, sys, time

state = json.load(open(os.environ["FAKE_GH_STATE"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[:2] == ["repo", "list"]:
    print(json.dumps(state["repos"]))
    sys.exit(0)
assert args[0] == "api", args
rest = [a for a in args[1:] if a != "--paginate"]
method = "GET"
if rest[:1] == ["-X"]:
    method, rest = rest[1], rest[2:]
path = rest[0].split("?")[0]
_, owner, repo, *tail = path.split("/")
data = state["data"].get(owner + "/" + repo, {})
if data.get("hang"):
    time.sleep(30)
if data.get("fail"):
    sys.stderr.write("HTTP 500: boom\\n")
    sys.exit(1)
if method != "GET":
    sys.exit(0)
tail = "/".join(tail)
if tail == "actions/permissions/artifact-and-log-retention":
    print(json.dumps({"days": data.get("retention", 90), "maximum_allowed_days": 90}))
    sys.exit(0)
key, field = {"actions/artifacts": ("artifacts", "artifacts"), "actions/caches": ("caches", "actions_caches")}[tail]
items = data.get(key, [])
if "--paginate" in args:
    for i in range(0, max(len(items), 1), 2):
        sys.stdout.write(json.dumps({"total_count": len(items), field: items[i:i + 2]}))
else:
    print(json.dumps({"total_count": len(items), field: items}))
"""
