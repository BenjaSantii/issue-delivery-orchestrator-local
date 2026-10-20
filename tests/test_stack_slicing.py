import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.slicing import (
    assert_size_accepted,
    classify,
    record_decision,
    record_estimate,
    size_check,
)
from issue_delivery_orchestrator.stack import (
    link_stack,
    pr_target,
    require_parent_pr,
    restack,
    slice_extract,
    stack_cut,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class RepoCase(unittest.TestCase):
    """A bare origin with a `test` target branch and a feature worktree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "origin.git"
        self.work = root / "work"
        git(root, "init", "--bare", "-q", str(self.remote))
        git(root, "init", "-q", "-b", "test", str(self.work))
        git(self.work, "config", "user.email", "dev@example.com")
        git(self.work, "config", "user.name", "Dev")
        git(self.work, "remote", "add", "origin", str(self.remote))
        self.commit("base.ts", "base\n", "base")
        git(self.work, "push", "-q", "-u", "origin", "test")
        git(self.work, "switch", "-q", "-c", "feature")
        self.state = {"worktree": str(self.work), "runId": "run-1", "branch": "feature"}
        for module in ("stack", "slicing"):
            patcher = patch(f"issue_delivery_orchestrator.{module}.save_state")
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, name: str, content: str, message: str) -> str:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(self.work, "add", ".")
        git(self.work, "commit", "-q", "-m", message)
        return git(self.work, "rev-parse", "HEAD")


class SizeTests(RepoCase):
    def test_classifies_product_test_and_excluded_files(self):
        self.assertEqual(classify("apps/x/src/a.service.ts"), "product")
        self.assertEqual(classify("apps/x/src/a.service.spec.ts"), "test")
        self.assertEqual(classify("apps/x/src/__tests__/a.tsx"), "test")
        self.assertEqual(classify("apps/x/src/database/migrations/1-add.ts"), "excluded")
        self.assertEqual(classify("pnpm-lock.yaml"), "excluded")
        self.assertEqual(classify("apps/bff/src/contracts/v1/openapi.json"), "excluded")

    def test_pauses_over_threshold_until_single_pr_decision_then_reprompts_at_double(self):
        self.commit("src/big.ts", "x\n" * 4001, "big")
        self.commit("src/big.spec.ts", "t\n" * 500, "tests")
        result = size_check(self.state)
        self.assertEqual(result["status"], "NEEDS_DECISION")
        self.assertEqual(result["productLines"], 4001)
        self.assertEqual(result["testLines"], 500)
        with self.assertRaises(RunBlocked):
            assert_size_accepted(self.state)

        record_decision(self.state, "single-pr", "Tightly coupled migration, not worth splitting")
        self.assertEqual(size_check(self.state)["status"], "OK")
        assert_size_accepted(self.state)

        self.commit("src/more.ts", "y\n" * 4002, "more")
        self.assertEqual(size_check(self.state)["status"], "NEEDS_DECISION")

    def test_estimate_overrun_only_pauses_above_size_threshold(self):
        record_estimate(self.state, 100)
        self.commit("src/a.ts", "x\n" * 140, "within")
        self.assertEqual(size_check(self.state)["status"], "OK")
        self.commit("src/b.ts", "x\n" * 3860, "exactly at threshold")
        self.commit("src/large.spec.ts", "t\n" * 5000, "tests do not count")
        self.assertEqual(size_check(self.state)["status"], "OK")
        assert_size_accepted(self.state)
        self.commit("src/c.ts", "x\n", "one line over")
        result = size_check(self.state)
        self.assertEqual(result["status"], "NEEDS_DECISION")
        self.assertEqual(result["productLines"], 4001)
        self.assertTrue(any("estimate 100" in reason for reason in result["reasons"]))

    def test_rejects_decision_without_reason(self):
        with self.assertRaises(RunBlocked):
            record_decision(self.state, "single-pr", "  ")


class StackTests(RepoCase):
    def test_cut_links_and_measures_only_the_child_delta(self):
        first = self.commit("src/contract.ts", "c\n" * 300, "contract")
        self.commit("src/ui.ts", "u\n" * 200, "ui")

        with self.assertRaises(RunBlocked):
            stack_cut(self.state, "HEAD", "slice-1")
        cut = stack_cut(self.state, first, "slice-1")
        self.assertEqual(git(self.work, "rev-parse", "origin/slice-1"), first)
        self.assertEqual(cut["sha"], first)

        link_stack(self.state, "slice-1")
        self.assertEqual(pr_target(self.state), "slice-1")
        self.assertEqual(size_check(self.state)["productLines"], 200)

    def test_rejects_parent_that_is_not_below_head(self):
        git(self.work, "switch", "-q", "-c", "other", "test")
        self.commit("src/other.ts", "o\n", "other")
        git(self.work, "push", "-q", "origin", "other")
        git(self.work, "switch", "-q", "feature")
        self.commit("src/f.ts", "f\n", "feature")
        with self.assertRaises(RunBlocked):
            link_stack(self.state, "other")

    def test_extracts_independent_commits_and_refuses_dependent_ones(self):
        self.commit("src/web.ts", "w\n", "web")
        mobile = self.commit("src/mobile.ts", "m\n", "mobile")
        slice_extract(self.state, [mobile], "slice-mobile")
        self.assertTrue((self.work / "src/web.ts").exists())
        self.assertFalse((self.work / "src/mobile.ts").exists())
        self.assertEqual(
            git(self.work, "show", "--name-only", "--format=", "origin/slice-mobile"),
            "src/mobile.ts",
        )

        dependent = self.commit("src/web.ts", "w\nchanged\n", "depends on web")
        head_before = git(self.work, "rev-parse", "HEAD")
        with self.assertRaises(RunBlocked):
            slice_extract(self.state, [dependent], "slice-dependent")
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(self.work, "branch", "--show-current"), "feature")

    def test_extract_refuses_pushed_branch(self):
        sha = self.commit("src/a.ts", "a\n", "a")
        git(self.work, "push", "-q", "origin", "feature")
        with self.assertRaises(RunBlocked):
            slice_extract(self.state, [sha], "slice-a")

    def _stack(self):
        git(self.work, "switch", "-q", "-c", "parent", "test")
        self.commit("src/parent.ts", "p\n", "parent")
        git(self.work, "push", "-q", "-u", "origin", "parent")
        git(self.work, "switch", "-q", "-C", "feature", "parent")
        self.commit("src/child.ts", "c\n", "child")
        git(self.work, "push", "-q", "-u", "origin", "feature")
        link_stack(self.state, "parent")

    def test_restack_follows_parent_fix_and_force_pushes_with_lease(self):
        self._stack()
        git(self.work, "switch", "-q", "parent")
        self.commit("src/parent.ts", "p\nfix\n", "parent fix")
        git(self.work, "push", "-q", "origin", "parent")
        git(self.work, "switch", "-q", "feature")
        github = Mock(cwd=self.work)
        with patch(
            "issue_delivery_orchestrator.stack._parent_pull_request",
            return_value={"state": "OPEN", "mergedAt": None},
        ):
            result = restack(self.state, github)
        self.assertTrue(result["restacked"])
        self.assertEqual((self.work / "src/parent.ts").read_text(), "p\nfix\n")
        self.assertEqual(
            git(self.work, "rev-parse", "origin/feature"), git(self.work, "rev-parse", "HEAD")
        )
        self.assertEqual(pr_target(self.state), "parent")

    def test_restack_after_squash_merge_keeps_only_child_commits(self):
        self._stack()
        git(self.work, "switch", "-q", "test")
        git(self.work, "merge", "-q", "--squash", "parent")
        git(self.work, "commit", "-q", "-m", "squashed parent")
        git(self.work, "push", "-q", "origin", "test")
        git(self.work, "switch", "-q", "feature")
        with patch(
            "issue_delivery_orchestrator.stack._parent_pull_request",
            return_value={"state": "MERGED", "mergedAt": "2026-09-29"},
        ):
            restack(self.state, Mock(cwd=self.work))
        own = git(self.work, "log", "--format=%s", "origin/test..HEAD").splitlines()
        self.assertEqual(own, ["child"])
        self.assertEqual(pr_target(self.state), "test")

    def test_restack_conflict_aborts_and_keeps_branch(self):
        self._stack()
        git(self.work, "switch", "-q", "parent")
        self.commit("src/child.ts", "parent wrote this\n", "conflicting parent change")
        git(self.work, "push", "-q", "origin", "parent")
        git(self.work, "switch", "-q", "feature")
        head = git(self.work, "rev-parse", "HEAD")
        with patch(
            "issue_delivery_orchestrator.stack._parent_pull_request",
            return_value={"state": "OPEN", "mergedAt": None},
        ):
            with self.assertRaises(RunBlocked):
                restack(self.state, Mock(cwd=self.work))
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_child_pr_requires_parent_pr(self):
        self._stack()
        with patch("issue_delivery_orchestrator.stack._parent_pull_request", return_value=None):
            with self.assertRaises(RunBlocked):
                require_parent_pr(self.state, Mock(cwd=self.work))


if __name__ == "__main__":
    unittest.main()
