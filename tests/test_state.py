import json
import tempfile
import unittest
from pathlib import Path

from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked
from issue_delivery_orchestrator.state import (
    PHASES,
    complete_phase,
    create_state,
    find_runs,
    load_state,
    review_method,
    run_mode,
    run_root,
    select_review_method,
)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.worktrees_root = Path(self.temporary.name)
        self.worktree = self.worktrees_root / "ts-1-run"
        self.worktree.mkdir()
        self.state = create_state(
            worktree=self.worktree,
            run_id="run-1",
            issue={"id": "id", "identifier": "TS-1", "title": "Title"},
            branch="benjamin/ts-1",
            base="development",
            created_from="origin/development",
            adopted_head="abc",
            identities={"linear": "user@example.com", "github": "example-user"},
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_state_inside_ignored_runtime_directory(self):
        path = run_root(self.worktree, "run-1") / "state.json"
        self.assertTrue(path.is_file())
        self.assertEqual(load_state(path)["currentPhase"], "grill")
        self.assertEqual(find_runs(self.worktrees_root, "TS-1")[0]["runId"], "run-1")
        self.assertEqual(run_mode(self.state), "superset")
        self.assertEqual(review_method(self.state), "cua-driver")
        self.assertEqual(self.state["discardedInitialStatus"], [])

    def test_advances_retired_merge_target_phase_to_manual_revision(self):
        path = run_root(self.worktree, "run-1") / "state.json"
        legacy_state = load_state(path)
        legacy_state["currentPhase"] = "merge-target"
        path.write_text(json.dumps(legacy_state), encoding="utf-8")

        self.assertEqual(load_state(path)["currentPhase"], "manual-revision")

    def test_finds_state_inside_additional_registered_worktree(self):
        separate_root = self.worktrees_root / "superset"
        separate_worktree = separate_root / "ts-2"
        separate_worktree.mkdir(parents=True)
        create_state(
            worktree=separate_worktree,
            run_id="run-2",
            issue={"id": "id-2", "identifier": "TS-2", "title": "Title"},
            branch="benjamin/ts-2",
            base="development",
            created_from="adopted:benjamin/ts-2",
            adopted_head="def",
            discarded_status=(" M existing.txt",),
            identities={"linear": "user@example.com", "github": "example-user"},
        )

        found = find_runs(
            self.worktrees_root / "private-only",
            "TS-2",
            (separate_worktree,),
        )

        self.assertEqual(found[0]["runId"], "run-2")
        self.assertEqual(found[0]["adoptedStatus"], [])
        self.assertEqual(found[0]["discardedInitialStatus"], [" M existing.txt"])

    def test_advances_in_order_and_finishes_preserved(self):
        for phase in PHASES:
            complete_phase(self.state, phase)
        self.assertEqual(self.state["status"], "completed_preserved")
        self.assertIsNone(self.state["currentPhase"])

    def test_rejects_out_of_order_checkpoint(self):
        with self.assertRaises(RunBlocked):
            complete_phase(self.state, "implement")

    def test_selects_browser_reviewer_before_manual_revision(self):
        self.state.pop("mode")
        selection = select_review_method(self.state, "codex-browser")

        self.assertEqual(selection["method"], "codex-browser")
        self.assertEqual(review_method(self.state), "codex-browser")

    def test_selects_browser_reviewer_at_start_of_manual_revision(self):
        self.state.pop("mode")
        for phase in PHASES[: PHASES.index("manual-revision")]:
            complete_phase(self.state, phase)

        selection = select_review_method(self.state, "codex-browser")

        self.assertEqual(selection["method"], "codex-browser")
        self.assertEqual(review_method(self.state), "codex-browser")

    def test_rejects_reviewer_change_after_manual_revision(self):
        self.state.pop("mode")
        for phase in PHASES[: PHASES.index("manual-revision") + 1]:
            complete_phase(self.state, phase)

        with self.assertRaisesRegex(RunBlocked, "before the first manual-revision"):
            select_review_method(self.state, "codex-browser")

    def test_legacy_state_without_reviewer_defaults_to_cua(self):
        self.state.pop("mode")
        self.state.pop("reviewer")

        self.assertEqual(run_mode(self.state), "superset")
        self.assertEqual(review_method(self.state), "cua-driver")

    def test_codex_mode_selects_browser_reviewer(self):
        state = create_state(
            worktree=self.worktree,
            run_id="run-codex",
            issue={"id": "id", "identifier": "TS-1", "title": "Title"},
            branch="benjamin/ts-1",
            base="development",
            created_from="codex:origin/development",
            adopted_head="abc",
            identities={"linear": "user@example.com", "github": "example-user"},
            mode="codex",
        )

        self.assertEqual(run_mode(state), "codex")
        self.assertEqual(review_method(state), "codex-browser")

    def test_vanilla_mode_selects_cua_reviewer(self):
        state = create_state(
            worktree=self.worktree,
            run_id="run-vanilla",
            issue={"id": "id", "identifier": "TS-1", "title": "Title"},
            branch="benjamin/ts-1",
            base="development",
            created_from="vanilla:origin/development",
            adopted_head="abc",
            identities={"linear": "user@example.com", "github": "example-user"},
            mode="vanilla",
        )

        self.assertEqual(run_mode(state), "vanilla")
        self.assertEqual(review_method(state), "cua-driver")

    def test_conductor_mode_selects_playwright_reviewer(self):
        state = create_state(
            worktree=self.worktree,
            run_id="run-conductor",
            issue={"id": "id", "identifier": "TS-1", "title": "Title"},
            branch="benjamin/ts-1",
            base="test",
            created_from="conductor:origin/test",
            adopted_head="abc",
            identities={"linear": "user@example.com", "github": "example-user"},
            mode="conductor",
        )

        self.assertEqual(run_mode(state), "conductor")
        self.assertEqual(review_method(state), "playwright")

    def test_conductor_mode_rejects_a_different_stored_reviewer(self):
        state = create_state(
            worktree=self.worktree,
            run_id="run-conductor-mismatch",
            issue={"id": "id", "identifier": "TS-1", "title": "Title"},
            branch="benjamin/ts-1",
            base="test",
            created_from="conductor:origin/test",
            adopted_head="abc",
            identities={"linear": "user@example.com", "github": "example-user"},
            mode="conductor",
        )
        state["reviewer"]["method"] = "cua-driver"

        with self.assertRaisesRegex(OrchestrationError, "requires reviewer playwright"):
            review_method(state)

    def test_new_mode_cannot_change_reviewer_independently(self):
        with self.assertRaisesRegex(RunBlocked, "fixed by development mode"):
            select_review_method(self.state, "codex-browser")


if __name__ == "__main__":
    unittest.main()
