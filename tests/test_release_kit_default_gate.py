"""release.sh's gate when none is passed: the one the repo's .gatesrc declares (GOH.SHX2).

goh's own release had no default gate, so a bare `release.sh --version X` of goh was refused. A repo
declares its release gate ONCE, as `GOH_RELEASE_GATE` in its .gatesrc; `--gate` and an exported
`GOH_RELEASE_GATE` still beat it, and with none anywhere the release is still refused.
"""

import test_release_kit as rk

kit = rk.kit  # the release flow's fixture: a project repo, a bare origin, a fake gh
run_release = rk.run_release


class TestDefaultGate:
    def test_the_repo_declared_gate_runs_when_none_is_passed(self, kit):
        (kit["proj"] / ".gatesrc").write_text('GOH_RELEASE_GATE="tools/gate.sh --full"\n')
        r = run_release(kit, "--dry-run", gate=None)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "run gate: tools/gate.sh --full" in r.stdout, r.stdout

    def test_no_gate_anywhere_is_still_refused(self, kit):
        r = run_release(kit, "--dry-run", gate=None)
        assert r.returncode != 0 and "no gate configured" in r.stdout + r.stderr
