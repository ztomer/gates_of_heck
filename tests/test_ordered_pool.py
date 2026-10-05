"""checks/_ordered_pool.py: concurrent, and reported as if it were not."""

import sys
import threading
import time

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
from _ordered_pool import in_order  # noqa: E402


def test_items_run_at_once_and_report_in_item_order(capsys):
    """Each item can only finish while the other runs (a serial pool times out), and the SLOW first
    item's output still comes first."""
    arrived = {"a": threading.Event(), "b": threading.Event()}

    def job(name):
        other = "b" if name == "a" else "a"
        arrived[name].set()
        met = arrived[other].wait(timeout=5)
        if name == "a":
            time.sleep(0.2)
        print(f"report {name}")
        print(f"warn {name}", file=sys.stderr)
        return met

    assert in_order(job, ["a", "b"]) == [True, True]
    assert capsys.readouterr().out == "report a\nwarn a\nreport b\nwarn b\n"


def test_no_items_is_no_work():
    assert in_order(lambda item: item, []) == []
