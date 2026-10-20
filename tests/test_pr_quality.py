import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.pr_quality import (
    VALIDATION_END,
    VALIDATION_START,
    assert_pre_review_current,
    assert_validation_current,
    merge_validation_block,
    publish_validation,
    record_pre_review,
)
from issue_delivery_orchestrator.state import run_root


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class PrQualityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        self.worktree = root / "work"
        _git(root, "init", "--bare", "-q", str(self.remote))
        _git(root, "init", "-q", "-b", "feature", str(self.worktree))
        _git(self.worktree, "config", "user.email", "dev@example.com")
        _git(self.worktree, "config", "user.name", "Dev")
        (self.worktree / "a.txt").write_text("a\n")
        _git(self.worktree, "add", ".")
        _git(self.worktree, "commit", "-q", "-m", "init")
        _git(self.worktree, "remote", "add", "origin", str(self.remote))
        _git(self.worktree, "push", "-q", "-u", "origin", "feature")
        self.sha = _git(self.worktree, "rev-parse", "HEAD")
        self.state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "pr": {"url": "https://github.com/example/repo/pull/10"},
        }
        self.run_dir = run_root(self.worktree, "run-1")
        self.run_dir.mkdir(parents=True)
        save = patch("issue_delivery_orchestrator.pr_quality.save_state")
        self.addCleanup(save.stop)
        save.start()

    def tearDown(self):
        self._tmp.cleanup()

    def _receipt(self, name: str, text: str) -> Path:
        path = self.run_dir / name
        path.write_text(text)
        return path

    def _commit(self):
        (self.worktree / "b.txt").write_text("b\n")
        _git(self.worktree, "add", ".")
        _git(self.worktree, "commit", "-q", "-m", "next")

    def test_merges_validation_block_by_appending_then_replacing(self):
        first = merge_validation_block("## Resumen\nCambio", "## Validación\nv1")
        self.assertTrue(first.startswith("## Resumen\nCambio\n\n" + VALIDATION_START))
        second = merge_validation_block(first, "## Validación\nv2")
        self.assertIn("v2", second)
        self.assertNotIn("v1", second)
        self.assertEqual(second.count(VALIDATION_START), 1)
        self.assertEqual(second.count(VALIDATION_END), 1)
        self.assertTrue(second.startswith("## Resumen"))

    def test_records_pre_review_only_for_current_head_and_clean_result(self):
        with self.assertRaises(RunBlocked):
            record_pre_review(
                self.state,
                self._receipt("pre.md", f"SHA: {self.sha}\nResultado: FIX_PENDING\n"),
            )
        with self.assertRaises(RunBlocked):
            record_pre_review(
                self.state,
                self._receipt("stale.md", "SHA: 0123456789abcdef\nResultado: PASS\n"),
            )
        recorded = record_pre_review(
            self.state,
            self._receipt("ok.md", f"SHA: {self.sha[:12]}\nResultado: PASS\n"),
        )
        self.assertEqual(recorded["sha"], self.sha)
        assert_pre_review_current(self.state)

        self._commit()
        with self.assertRaises(RunBlocked):
            assert_pre_review_current(self.state)

    def test_rejects_receipts_outside_the_run_directory(self):
        outside = self.worktree / "receipt.md"
        outside.write_text(f"SHA: {self.sha}\nResultado: PASS\n")
        with self.assertRaises(Exception):
            record_pre_review(self.state, outside)

    def test_conductor_does_not_require_or_fabricate_a_local_review(self):
        self.state["mode"] = {"name": "conductor"}
        assert_pre_review_current(self.state)
        self.assertNotIn("preReview", self.state)
        self.state["preReview"] = {"sha": "old", "result": "PASS"}
        assert_pre_review_current(self.state)
        self.assertEqual(self.state["preReview"]["sha"], "old")

    def test_other_modes_still_require_a_current_local_review(self):
        for mode in ("codex", "superset", "vanilla"):
            with self.subTest(mode=mode):
                self.state["mode"] = {"name": mode}
                with self.assertRaises(RunBlocked):
                    assert_pre_review_current(self.state)

    def test_publishes_validation_for_pushed_head_and_tracks_staleness(self):
        github = Mock()
        github.body.return_value = "## Resumen\nCambio"
        receipt = self._receipt("validation.md", f"## Validación\nSHA: {self.sha}\n")

        published = publish_validation(self.state, receipt, github)

        self.assertEqual(published["sha"], self.sha)
        body_file = github.edit_body.call_args.args[1]
        self.assertIn(VALIDATION_START, Path(body_file).read_text())
        assert_validation_current(self.state)

        self._commit()
        with self.assertRaises(RunBlocked):
            assert_validation_current(self.state)
        new_sha = _git(self.worktree, "rev-parse", "HEAD")
        unpushed = self._receipt("v2.md", f"SHA: {new_sha}\n")
        with self.assertRaises(RunBlocked):
            publish_validation(self.state, unpushed, github)


if __name__ == "__main__":
    unittest.main()
