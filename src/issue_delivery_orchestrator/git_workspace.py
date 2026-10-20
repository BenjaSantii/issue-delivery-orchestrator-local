from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import settings
from .errors import RunBlocked
from .util import run


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    created_from: str
    adopted_head: str
    adopted_status: tuple[str, ...] = ()
    discarded_status: tuple[str, ...] = ()


class GitWorkspace:
    def __init__(self, repository: Path):
        self.repository = repository.resolve()

    def fetch(self, base: str) -> None:
        run(["git", "fetch", "origin", "--prune"], cwd=self.repository)

    def adopt(self, path: Path, expected_branch: str, issue: str) -> Worktree:
        candidate = self._validated_worktree(path)
        branch = run(["git", "branch", "--show-current"], cwd=candidate).stdout.strip()
        if not branch:
            raise RunBlocked(f"Requested worktree is in detached HEAD state: {candidate}")
        if not _matches_issue_branch(branch, expected_branch, issue):
            raise RunBlocked(
                f"Worktree branch {branch} does not match Linear branch {expected_branch}"
            )
        self._require_ignored_runtime(candidate)
        discarded_status = self._discard_initial_changes(candidate)
        head, status = self._snapshot(candidate)
        return Worktree(
            candidate,
            branch,
            f"adopted:{branch}",
            head,
            status,
            discarded_status,
        )

    def adopt_codex(
        self,
        path: Path,
        expected_branch: str,
        base: str,
        issue: str,
    ) -> Worktree:
        return self._adopt_switchable(
            path,
            expected_branch,
            base,
            issue,
            mode="codex",
            allowed_start_branches=set(),
        )

    def adopt_vanilla(
        self,
        path: Path,
        expected_branch: str,
        base: str,
        issue: str,
        *,
        allow_discard: bool = True,
    ) -> Worktree:
        return self._adopt_switchable(
            path,
            expected_branch,
            base,
            issue,
            mode="vanilla",
            allowed_start_branches={base},
            allow_discard=allow_discard,
        )

    def adopt_conductor(
        self,
        path: Path,
        expected_branch: str,
        base: str,
        issue: str,
    ) -> Worktree:
        # Conductor Cloud starts each workspace on its own scratch branch; any start branch is
        # accepted as long as its history is contained in the target or the remote base.
        return self._adopt_switchable(
            path,
            expected_branch,
            base,
            issue,
            mode="conductor",
            allowed_start_branches=None,
        )

    def _adopt_switchable(
        self,
        path: Path,
        expected_branch: str,
        base: str,
        issue: str,
        *,
        mode: str,
        allowed_start_branches: set[str] | None,
        allow_discard: bool = True,
    ) -> Worktree:
        candidate = self._validated_worktree(path)
        branch = run(["git", "branch", "--show-current"], cwd=candidate).stdout.strip()
        if branch:
            if _matches_issue_branch(branch, expected_branch, issue):
                self._require_ignored_runtime(candidate)
                discarded_status = self._prepare_initial_state(
                    candidate,
                    mode=mode,
                    allow_discard=allow_discard,
                )
                head, status = self._snapshot(candidate)
                return Worktree(
                    candidate,
                    branch,
                    f"{mode}:{branch}",
                    head,
                    status,
                    discarded_status,
                )
            if allowed_start_branches is not None and branch not in allowed_start_branches:
                raise RunBlocked(
                    f"{mode.capitalize()} worktree branch {branch} does not match "
                    f"Linear branch {expected_branch}"
                )

        occupied = self._branch_worktree(expected_branch)
        if occupied:
            raise RunBlocked(
                f"Branch {expected_branch} is already attached to worktree {occupied}"
            )

        local = self._ref_exists(f"refs/heads/{expected_branch}")
        remote = self._ref_exists(f"refs/remotes/origin/{expected_branch}")
        if local:
            target = expected_branch
            switch = ["git", "switch", expected_branch]
        elif remote:
            target = f"origin/{expected_branch}"
            switch = ["git", "switch", "--track", "-c", expected_branch, target]
        else:
            target = f"origin/{base}"
            if not self._ref_exists(f"refs/remotes/{target}"):
                raise RunBlocked(f"Base branch {target} does not exist")
            switch = ["git", "switch", "-c", expected_branch, target]

        self._require_safe_switch_history(candidate, target, base, mode)
        self._require_ignored_runtime(candidate)
        discarded_status = self._prepare_initial_state(
            candidate,
            mode=mode,
            allow_discard=allow_discard,
        )
        run(switch, cwd=candidate)
        self._require_ignored_runtime(candidate)
        head, status = self._snapshot(candidate)
        return Worktree(
            candidate,
            expected_branch,
            f"{mode}:{target}",
            head,
            status,
            discarded_status,
        )

    def paths(self) -> tuple[Path, ...]:
        output = run(["git", "worktree", "list", "--porcelain"], cwd=self.repository).stdout
        return tuple(
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.splitlines()
            if line.startswith("worktree ")
        )

    def _ref_exists(self, ref: str) -> bool:
        return (
            run(
                ["git", "show-ref", "--verify", "--quiet", ref],
                cwd=self.repository,
                check=False,
            ).returncode
            == 0
        )

    def _branch_worktree(self, branch: str) -> Path | None:
        output = run(["git", "worktree", "list", "--porcelain"], cwd=self.repository).stdout
        current_path: Path | None = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{branch}" and current_path:
                return current_path
        return None

    def _validated_worktree(self, path: Path) -> Path:
        candidate = path.expanduser().resolve()
        if not candidate.is_dir():
            raise RunBlocked(f"Requested worktree does not exist: {candidate}")
        top_level = Path(
            run(["git", "rev-parse", "--show-toplevel"], cwd=candidate).stdout.strip()
        ).resolve()
        if top_level != candidate:
            raise RunBlocked(f"Requested path is not a worktree root: {candidate}")
        if self._git_common_dir(candidate) != self._git_common_dir(self.repository):
            raise RunBlocked(f"Requested worktree belongs to a different repository: {candidate}")
        if candidate not in self.paths():
            raise RunBlocked(f"Requested path is not a registered Git worktree: {candidate}")
        return candidate

    @staticmethod
    def _require_ignored_runtime(path: Path) -> None:
        configuration = settings()
        ignored_probe = str(
            Path(configuration.runtime_root)
            / configuration.runtime_namespace
            / ".ignore-probe"
        )
        ignored = run(
            ["git", "check-ignore", "--quiet", "--no-index", ignored_probe],
            cwd=path,
            check=False,
        )
        if ignored.returncode != 0:
            raise RunBlocked(
                f"The adopted worktree must ignore {configuration.runtime_root} before "
                "orchestration state can be stored safely"
            )

    @staticmethod
    def _snapshot(path: Path) -> tuple[str, tuple[str, ...]]:
        head = run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
        status = tuple(
            line
            for line in run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=path,
            ).stdout.splitlines()
            if line
        )
        return head, status

    @staticmethod
    def _prepare_initial_state(
        path: Path,
        *,
        mode: str,
        allow_discard: bool,
    ) -> tuple[str, ...]:
        status = GitWorkspace._snapshot(path)[1]
        if status and not allow_discard:
            raise RunBlocked(
                f"Mode decision: {mode} (source: vanilla-fallback, reviewer: "
                f"cua-driver, worktree: {path}). Inferred {mode} mode found "
                "local changes: "
                f"{', '.join(status)}. Preserve or clean them, or explicitly "
                f"select --mode {mode} to authorize the normal new-run cleanup."
            )
        if not status:
            return ()
        return GitWorkspace._discard_initial_changes(path)

    @staticmethod
    def _discard_initial_changes(path: Path) -> tuple[str, ...]:
        status = GitWorkspace._snapshot(path)[1]
        if not status:
            return ()
        configuration = settings()
        run(["git", "reset", "--hard", "HEAD"], cwd=path)
        run(
            [
                "git",
                "clean",
                "-fd",
                "-e",
                f"{configuration.runtime_root}/",
            ],
            cwd=path,
        )
        remaining = GitWorkspace._snapshot(path)[1]
        if remaining:
            raise RunBlocked(
                "Could not clean the adopted worktree before starting the run: "
                + ", ".join(remaining)
            )
        return status

    def _require_safe_switch_history(
        self,
        path: Path,
        target: str,
        base: str,
        mode: str,
    ) -> None:
        safe_refs = [target]
        remote_base = f"origin/{base}"
        if self._ref_exists(f"refs/remotes/{remote_base}"):
            safe_refs.append(remote_base)
        head_is_preserved = any(
            run(
                ["git", "merge-base", "--is-ancestor", "HEAD", ref],
                cwd=path,
                check=False,
            ).returncode
            == 0
            for ref in safe_refs
        )
        if not head_is_preserved:
            raise RunBlocked(
                f"{mode.capitalize()} worktree has commits not contained in "
                f"{target} or {remote_base}; "
                "preserve them before starting a new run"
            )

    @staticmethod
    def _git_common_dir(path: Path) -> Path:
        raw = Path(run(["git", "rev-parse", "--git-common-dir"], cwd=path).stdout.strip())
        return (path / raw).resolve() if not raw.is_absolute() else raw.resolve()


def _matches_issue_branch(actual: str, expected: str, issue: str) -> bool:
    if actual == expected:
        return True
    issue_marker = issue.lower()
    actual_lower = actual.lower()
    carries_issue = actual_lower == issue_marker or f"/{issue_marker}" in actual_lower
    return carries_issue and expected.startswith(actual)
