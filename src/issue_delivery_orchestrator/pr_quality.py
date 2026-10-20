"""Gates that make a PR self-evidencing before automated reviewers see it.

- Pre-PR review: required on the exact HEAD outside Conductor. Cloud delivery
  does not require an authenticated nested reviewer CLI.
- Validation block: the PR body carries the validation receipt for the pushed
  HEAD, so reviewers without dependencies can audit what was actually run.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .errors import RunBlocked
from .github import GitHubClient
from .state import run_mode, run_root, save_state
from .util import ensure_within, run

VALIDATION_START = "<!-- issue-delivery:validation:start -->"
VALIDATION_END = "<!-- issue-delivery:validation:end -->"
PRE_REVIEW_RESULTS = ("PASS", "SKIP_ONLY")
_PRE_REVIEW_RESULT = re.compile(r"(?im)^\s*resultado\s*:\s*([A-Z_]+)\s*$")
_MIN_SHA_PREFIX = 10


def head_sha(worktree: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def _mentions_sha(text: str, sha: str) -> bool:
    candidates = re.findall(r"\b[0-9a-f]{%d,40}\b" % _MIN_SHA_PREFIX, text)
    return any(sha.startswith(candidate) for candidate in candidates)


def _read_run_file(state: dict[str, Any], path: Path) -> tuple[Path, str]:
    root = run_root(Path(state["worktree"]), state["runId"])
    resolved = ensure_within(path if path.is_absolute() else Path(state["worktree"]) / path, root)
    if not resolved.is_file():
        raise RunBlocked(f"Receipt not found inside the run directory: {resolved}")
    text = resolved.read_text()
    if not text.strip():
        raise RunBlocked(f"Receipt is empty: {resolved}")
    return resolved, text


def record_pre_review(state: dict[str, Any], receipt: Path) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    resolved, text = _read_run_file(state, receipt)
    sha = head_sha(worktree)
    if not _mentions_sha(text, sha):
        raise RunBlocked(
            f"Pre-PR review receipt must reference the reviewed HEAD {sha[:12]}; "
            "rerun the review after the last commit"
        )
    match = _PRE_REVIEW_RESULT.search(text)
    result = match.group(1) if match else None
    if result not in PRE_REVIEW_RESULTS:
        raise RunBlocked(
            "Pre-PR review receipt needs 'Resultado: PASS' or 'Resultado: SKIP_ONLY'; "
            "fix remaining FIX findings or resolve NEEDS_USER_DECISION/STOP first"
        )
    state["preReview"] = {
        "sha": sha,
        "result": result,
        "receipt": str(resolved.relative_to(worktree.resolve())),
        "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_state(state)
    return state["preReview"]


def assert_pre_review_current(state: dict[str, Any]) -> None:
    if run_mode(state) == "conductor":
        return  # Not required by cloud policy; never fabricate a PASS receipt.
    recorded = state.get("preReview") or {}
    sha = head_sha(Path(state["worktree"]))
    if recorded.get("sha") != sha:
        raise RunBlocked(
            "Run the pre-PR review on the current HEAD and record it with "
            "record-pre-review before opening a new PR"
        )


def merge_validation_block(body: str, content: str) -> str:
    block = f"{VALIDATION_START}\n{content.strip()}\n{VALIDATION_END}"
    pattern = re.compile(
        re.escape(VALIDATION_START) + r".*?" + re.escape(VALIDATION_END),
        re.DOTALL,
    )
    if pattern.search(body):
        return pattern.sub(lambda _: block, body, count=1)
    separator = "\n\n" if body.strip() else ""
    return f"{body.rstrip()}{separator}{block}\n"


def _assert_pushed(worktree: Path, sha: str) -> None:
    upstream = run(
        ["git", "rev-parse", "@{upstream}"], cwd=worktree, check=False
    ).stdout.strip()
    if upstream != sha:
        raise RunBlocked(
            f"HEAD {sha[:12]} is not pushed; push before publishing its validation"
        )


def publish_validation(
    state: dict[str, Any],
    receipt: Path,
    github: GitHubClient,
) -> dict[str, Any]:
    if not state.get("pr"):
        raise RunBlocked("Cannot publish validation before a PR has been recorded")
    worktree = Path(state["worktree"])
    resolved, text = _read_run_file(state, receipt)
    sha = head_sha(worktree)
    if not _mentions_sha(text, sha):
        raise RunBlocked(
            f"Validation receipt must reference the pushed HEAD {sha[:12]}"
        )
    _assert_pushed(worktree, sha)
    url = state["pr"]["url"]
    merged = merge_validation_block(github.body(url), text)
    body_file = run_root(worktree, state["runId"]) / "validation" / "pr-body.md"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(merged)
    github.edit_body(url, body_file)
    state["validationBlock"] = {
        "sha": sha,
        "receipt": str(resolved.relative_to(worktree.resolve())),
        "publishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_state(state)
    return state["validationBlock"]


def assert_validation_current(state: dict[str, Any]) -> None:
    published = state.get("validationBlock") or {}
    sha = head_sha(Path(state["worktree"]))
    if published.get("sha") != sha:
        raise RunBlocked(
            "The PR validation block does not match the current HEAD; "
            "publish it with publish-validation after the last push"
        )
