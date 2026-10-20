import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.git_workspace import GitWorkspace, _matches_issue_branch


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class GitWorkspaceTests(unittest.TestCase):
    def test_accepts_only_exact_or_truncated_linear_issue_branch(self):
        expected = "benjamin/ts-12-long-linear-branch-name"

        self.assertTrue(_matches_issue_branch(expected, expected, "TS-12"))
        self.assertTrue(
            _matches_issue_branch(
                "benjamin/ts-12-long-linear",
                expected,
                "TS-12",
            )
        )
        self.assertFalse(
            _matches_issue_branch(
                "benjamin/ts-12-different",
                expected,
                "TS-12",
            )
        )
        self.assertFalse(
            _matches_issue_branch(
                "benjamin/unrelated",
                expected,
                "TS-12",
            )
        )

    def test_adopts_registered_worktree_and_discards_existing_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            superset = root / "superset-worktree"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "development", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".env\n.local-runtime/\n")
            (seed / "file.txt").write_text("base\n")
            git(seed, "add", ".gitignore", "file.txt")
            git(seed, "commit", "-m", "base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "development")
            git(root, "clone", str(remote), str(checkout))
            git(
                checkout,
                "worktree",
                "add",
                "-b",
                "benjamin/ts-12-long-linear",
                str(superset),
                "origin/development",
            )
            (superset / "file.txt").write_text("preexisting\n")
            (superset / "untracked.txt").write_text("remove\n")
            (superset / ".env").write_text("preserve\n")

            workspace = GitWorkspace(checkout)
            result = workspace.adopt(
                superset,
                "benjamin/ts-12-long-linear-branch-name",
                "TS-12",
            )

            self.assertEqual(result.path, superset.resolve())
            self.assertEqual(result.branch, "benjamin/ts-12-long-linear")
            self.assertEqual(result.created_from, "adopted:benjamin/ts-12-long-linear")
            self.assertEqual(result.adopted_status, ())
            self.assertEqual(
                result.discarded_status,
                (" M file.txt", "?? untracked.txt"),
            )
            self.assertEqual((superset / "file.txt").read_text(), "base\n")
            self.assertFalse((superset / "untracked.txt").exists())
            self.assertEqual((superset / ".env").read_text(), "preserve\n")

    def test_adopts_detached_codex_worktree_and_creates_issue_branch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            codex = root / "codex-worktree"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "development", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".local-runtime/\n")
            (seed / "file.txt").write_text("base\n")
            git(seed, "add", ".gitignore", "file.txt")
            git(seed, "commit", "-m", "base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "development")
            git(root, "clone", str(remote), str(checkout))
            git(
                checkout,
                "worktree",
                "add",
                "--detach",
                str(codex),
                "origin/development",
            )

            workspace = GitWorkspace(checkout)
            workspace.fetch("development")
            result = workspace.adopt_codex(
                codex,
                "benjamin/ts-13-codex-mode",
                "development",
                "TS-13",
            )

            self.assertEqual(result.path, codex.resolve())
            self.assertEqual(result.branch, "benjamin/ts-13-codex-mode")
            self.assertEqual(result.created_from, "codex:origin/development")
            self.assertEqual(
                git(codex, "branch", "--show-current"),
                "benjamin/ts-13-codex-mode",
            )

    def test_vanilla_switches_base_checkout_to_linear_branch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "development", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".env\n.local-runtime/\n")
            (seed / "file.txt").write_text("base\n")
            git(seed, "add", ".gitignore", "file.txt")
            git(seed, "commit", "-m", "base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "development")
            git(root, "clone", str(remote), str(checkout))
            git(checkout, "switch", "development")
            (checkout / "file.txt").write_text("discard me\n")
            (checkout / ".env").write_text("preserve\n")

            workspace = GitWorkspace(checkout)
            workspace.fetch("development")
            result = workspace.adopt_vanilla(
                checkout,
                "benjamin/ts-17-vanilla-mode",
                "development",
                "TS-17",
            )

            self.assertEqual(result.path, checkout.resolve())
            self.assertEqual(result.branch, "benjamin/ts-17-vanilla-mode")
            self.assertEqual(result.created_from, "vanilla:origin/development")
            self.assertEqual(result.discarded_status, (" M file.txt",))
            self.assertEqual((checkout / "file.txt").read_text(), "base\n")
            self.assertEqual((checkout / ".env").read_text(), "preserve\n")
            self.assertEqual(
                git(checkout, "branch", "--show-current"),
                "benjamin/ts-17-vanilla-mode",
            )

    def _conductor_checkout(self, root: Path) -> Path:
        remote = root / "remote.git"
        seed = root / "seed"
        checkout = root / "checkout"
        git(root, "init", "--bare", str(remote))
        git(root, "init", "-b", "test", str(seed))
        git(seed, "config", "user.email", "test@example.com")
        git(seed, "config", "user.name", "Test")
        (seed / ".gitignore").write_text(".env\n.local-runtime/\n")
        (seed / "file.txt").write_text("base\n")
        git(seed, "add", ".gitignore", "file.txt")
        git(seed, "commit", "-m", "base")
        git(seed, "remote", "add", "origin", str(remote))
        git(seed, "push", "-u", "origin", "test")
        git(root, "clone", str(remote), str(checkout))
        git(checkout, "config", "user.email", "test@example.com")
        git(checkout, "config", "user.name", "Test")
        # Conductor Cloud names its own scratch branch from the selected base.
        git(checkout, "switch", "-c", "benjasantii/quiet-harbor", "origin/test")
        return checkout

    def test_conductor_switches_scratch_branch_to_linear_branch(self):
        with tempfile.TemporaryDirectory() as raw:
            checkout = self._conductor_checkout(Path(raw))
            (checkout / "file.txt").write_text("setup noise\n")
            (checkout / ".env").write_text("preserve\n")

            workspace = GitWorkspace(checkout)
            workspace.fetch("test")
            result = workspace.adopt_conductor(
                checkout,
                "benjamin/ts-21-conductor-mode",
                "test",
                "TS-21",
            )

            self.assertEqual(result.branch, "benjamin/ts-21-conductor-mode")
            self.assertEqual(result.created_from, "conductor:origin/test")
            self.assertEqual(result.discarded_status, (" M file.txt",))
            self.assertEqual((checkout / ".env").read_text(), "preserve\n")
            self.assertEqual(
                git(checkout, "branch", "--show-current"),
                "benjamin/ts-21-conductor-mode",
            )

    def test_conductor_rejects_scratch_branch_with_unpreserved_commits(self):
        with tempfile.TemporaryDirectory() as raw:
            checkout = self._conductor_checkout(Path(raw))
            (checkout / "scratch.txt").write_text("unpreserved\n")
            git(checkout, "add", "scratch.txt")
            git(checkout, "commit", "-m", "scratch work")

            workspace = GitWorkspace(checkout)
            workspace.fetch("test")
            with self.assertRaisesRegex(RunBlocked, "commits not contained"):
                workspace.adopt_conductor(
                    checkout,
                    "benjamin/ts-22-conductor-mode",
                    "test",
                    "TS-22",
                )

    def test_inferred_vanilla_rejects_dirty_state_without_discarding(self):
        workspace = GitWorkspace(Path("/tmp/repository"))
        dirty = (" M tracked.txt", "?? scratch.txt")

        with (
            patch.object(GitWorkspace, "_snapshot", return_value=("abc", dirty)),
            patch.object(
                GitWorkspace,
                "_discard_initial_changes",
            ) as discard,
            self.assertRaisesRegex(
                RunBlocked,
                "Mode decision: vanilla.*explicitly select --mode vanilla",
            ),
        ):
            workspace._prepare_initial_state(
                Path("/tmp/worktree"),
                mode="vanilla",
                allow_discard=False,
            )

        discard.assert_not_called()

    def test_codex_mode_cleans_stale_worktree_and_preserves_ignored_setup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            codex = root / "codex-worktree"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "test", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".env\n.local-runtime/\n")
            (seed / "file.txt").write_text("old base\n")
            git(seed, "add", ".gitignore", "file.txt")
            git(seed, "commit", "-m", "old base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "test")
            git(root, "clone", str(remote), str(checkout))
            git(
                checkout,
                "worktree",
                "add",
                "--detach",
                str(codex),
                "origin/test",
            )

            (seed / "file.txt").write_text("current base\n")
            git(seed, "add", "file.txt")
            git(seed, "commit", "-m", "advance base")
            git(seed, "push", "origin", "test")

            (codex / "file.txt").write_text("setup changed tracked file\n")
            (codex / "scratch.txt").write_text("remove\n")
            (codex / ".env").write_text("preserve\n")

            workspace = GitWorkspace(checkout)
            workspace.fetch("test")
            result = workspace.adopt_codex(
                codex,
                "benjamin/ts-16-codex-mode",
                "test",
                "TS-16",
            )

            self.assertEqual(result.path, codex.resolve())
            self.assertEqual(result.branch, "benjamin/ts-16-codex-mode")
            self.assertEqual(result.created_from, "codex:origin/test")
            self.assertEqual(result.adopted_status, ())
            self.assertEqual(
                result.discarded_status,
                (" M file.txt", "?? scratch.txt"),
            )
            self.assertEqual((codex / "file.txt").read_text(), "current base\n")
            self.assertFalse((codex / "scratch.txt").exists())
            self.assertEqual((codex / ".env").read_text(), "preserve\n")

    def test_codex_mode_rejects_unrelated_attached_branch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            codex = root / "codex-worktree"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "development", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".local-runtime/\n")
            git(seed, "add", ".gitignore")
            git(seed, "commit", "-m", "base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "development")
            git(root, "clone", str(remote), str(checkout))
            git(
                checkout,
                "worktree",
                "add",
                "-b",
                "unrelated",
                str(codex),
                "origin/development",
            )

            workspace = GitWorkspace(checkout)
            with self.assertRaisesRegex(RunBlocked, "does not match Linear branch"):
                workspace.adopt_codex(
                    codex,
                    "benjamin/ts-14-codex-mode",
                    "development",
                    "TS-14",
                )

    def test_codex_mode_rejects_detached_commits_outside_base(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            remote = root / "remote.git"
            seed = root / "seed"
            checkout = root / "checkout"
            codex = root / "codex-worktree"
            git(root, "init", "--bare", str(remote))
            git(root, "init", "-b", "development", str(seed))
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / ".gitignore").write_text(".local-runtime/\n")
            (seed / "file.txt").write_text("base\n")
            git(seed, "add", ".gitignore", "file.txt")
            git(seed, "commit", "-m", "base")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "-u", "origin", "development")
            git(root, "clone", str(remote), str(checkout))
            git(
                checkout,
                "worktree",
                "add",
                "--detach",
                str(codex),
                "origin/development",
            )
            git(codex, "config", "user.email", "test@example.com")
            git(codex, "config", "user.name", "Test")
            (codex / "detached.txt").write_text("unpreserved\n")
            git(codex, "add", "detached.txt")
            git(codex, "commit", "-m", "detached work")

            workspace = GitWorkspace(checkout)
            workspace.fetch("development")
            with self.assertRaisesRegex(RunBlocked, "commits not contained"):
                workspace.adopt_codex(
                    codex,
                    "benjamin/ts-15-codex-mode",
                    "development",
                    "TS-15",
                )


if __name__ == "__main__":
    unittest.main()
