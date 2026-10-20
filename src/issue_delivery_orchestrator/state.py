from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .errors import OrchestrationError, RunBlocked
from .util import atomic_write_json, read_json


PHASES = (
    "grill",
    "implement",
    "refactor",
    "manual-revision",
    "pr-creation",
    "review-convergence",
)
REVIEW_METHODS = ("cua-driver", "codex-browser", "playwright")
DEVELOPMENT_MODES = ("codex", "superset", "vanilla", "conductor")
MODE_REVIEWERS = {
    "codex": "codex-browser",
    "superset": "cua-driver",
    "vanilla": "cua-driver",
    "conductor": "playwright",
}
RESUMABLE_STATUSES = {"active", "blocked", "needs_user_decision", "completed_preserved"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def run_root(worktree: Path, run_id: str) -> Path:
    configuration = settings()
    return (
        worktree
        / configuration.runtime_root
        / configuration.runtime_namespace
        / run_id
    )


def create_state(
    *,
    worktree: Path,
    run_id: str,
    issue: dict[str, Any],
    branch: str,
    base: str,
    created_from: str,
    adopted_head: str,
    adopted_status: Iterable[str] = (),
    discarded_status: Iterable[str] = (),
    identities: dict[str, str],
    mode: str = "superset",
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in DEVELOPMENT_MODES:
        raise OrchestrationError(f"Unsupported development mode: {mode}")
    reviewer_method = MODE_REVIEWERS[mode]
    root = run_root(worktree, run_id)
    for child in (
        "agents",
        "browser-profile",
        "evidence",
        "logs",
        "planning",
        "review",
        "validation",
    ):
        (root / child).mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        # Deliberately absent from old state files: do not retrofit in-flight deliveries.
        "observabilityVersion": 1,
        "phaseMeasurement": {"startedAt": now(), "startCommit": adopted_head},
        "runId": run_id,
        "startedAt": now(),
        "updatedAt": now(),
        "status": "active",
        "currentPhase": PHASES[0],
        "issue": issue,
        "branch": branch,
        "base": base,
        "createdFrom": created_from,
        "adoptedHead": adopted_head,
        "adoptedStatus": list(adopted_status),
        "discardedInitialStatus": list(discarded_status),
        "worktree": str(worktree.resolve()),
        "identities": identities,
        "profile": profile or {},
        "phases": [],
        "artifacts": {},
        "runtimes": [],
        "ownedProcesses": [],
        "pr": None,
        "reviewAcknowledgements": [],
        "mode": {
            "name": mode,
            "selectedAt": now(),
        },
        "reviewer": {
            "method": reviewer_method,
            "selectedAt": now(),
        },
        "blocker": None,
    }
    save_state(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    state["updatedAt"] = now()
    root = run_root(Path(state["worktree"]), state["runId"])
    atomic_write_json(root / "state.json", state)


def state_path(state: dict[str, Any]) -> Path:
    return run_root(Path(state["worktree"]), state["runId"]) / "state.json"


def load_state(path: Path) -> dict[str, Any]:
    state = read_json(path)
    if not isinstance(state, dict):
        raise OrchestrationError(f"Invalid run state: {path}")
    if state.get("currentPhase") == "merge-target":
        state["currentPhase"] = "manual-revision"
    return state


def find_runs(
    worktrees_root: Path,
    issue_identifier: str,
    additional_worktrees: Iterable[Path] = (),
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    configuration = settings()
    relative_root = (
        Path(configuration.runtime_root) / configuration.runtime_namespace
    )
    pattern = f"*/{relative_root.as_posix()}/*/state.json"
    paths = set(worktrees_root.glob(pattern))
    for worktree in additional_worktrees:
        paths.update(
            worktree.glob(f"{relative_root.as_posix()}/*/state.json")
        )
    for path in paths:
        try:
            state = load_state(path)
        except (OSError, OrchestrationError):
            continue
        if state.get("issue", {}).get("identifier") == issue_identifier:
            candidates.append(state)
    return sorted(candidates, key=lambda item: item.get("startedAt", ""), reverse=True)


def resumable_run(
    worktrees_root: Path,
    issue_identifier: str,
    additional_worktrees: Iterable[Path] = (),
) -> dict[str, Any] | None:
    return next(
        (
            state
            for state in find_runs(worktrees_root, issue_identifier, additional_worktrees)
            if state.get("status") in RESUMABLE_STATUSES
        ),
        None,
    )


def complete_phase(
    state: dict[str, Any],
    phase: str,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    if state.get("currentPhase") != phase:
        raise RunBlocked(
            f"Cannot complete phase {phase}; current phase is {state.get('currentPhase')}"
        )
    index = PHASES.index(phase)
    state["phases"].append({"phase": phase, "status": "completed", "completedAt": now()})
    state["artifacts"].update(artifacts or {})
    state["blocker"] = None
    if index + 1 < len(PHASES):
        state["currentPhase"] = PHASES[index + 1]
        state["status"] = "active"
    else:
        state["currentPhase"] = None
        state["status"] = "completed_preserved"
        state["completedAt"] = now()
    save_state(state)
    return state


def block_run(state: dict[str, Any], reason: str, decision: bool = False) -> None:
    state["status"] = "needs_user_decision" if decision else "blocked"
    state["blocker"] = {
        "phase": state.get("currentPhase"),
        "reason": reason,
        "createdAt": now(),
    }
    save_state(state)


def resume_run(state: dict[str, Any]) -> None:
    if state.get("status") not in {"blocked", "needs_user_decision", "completed_preserved"}:
        raise RunBlocked(f"Run is not resumable from status {state.get('status')}")
    state["status"] = "active"
    state["blocker"] = None
    save_state(state)


def review_method(state: dict[str, Any]) -> str:
    stored_method = str((state.get("reviewer") or {}).get("method") or "")
    if state.get("mode"):
        mode = run_mode(state)
        expected_method = MODE_REVIEWERS[mode]
        if stored_method and stored_method != expected_method:
            raise OrchestrationError(
                f"Run mode {mode} requires reviewer {expected_method}, got {stored_method}"
            )
        return expected_method
    method = stored_method or "cua-driver"
    if method not in REVIEW_METHODS:
        raise OrchestrationError(f"Run has unsupported reviewer method: {method}")
    return method


def run_mode(state: dict[str, Any]) -> str:
    stored_mode = str((state.get("mode") or {}).get("name") or "")
    if stored_mode:
        if stored_mode not in DEVELOPMENT_MODES:
            raise OrchestrationError(f"Run has unsupported development mode: {stored_mode}")
        return stored_mode
    legacy_method = str((state.get("reviewer") or {}).get("method") or "cua-driver")
    if legacy_method not in REVIEW_METHODS:
        raise OrchestrationError(f"Run has unsupported reviewer method: {legacy_method}")
    return "codex" if legacy_method == "codex-browser" else "superset"


def select_review_method(state: dict[str, Any], method: str) -> dict[str, Any]:
    if state.get("mode"):
        raise RunBlocked(
            "Reviewer is fixed by development mode; choose codex, superset, "
            "vanilla, or conductor when creating a run"
        )
    if method not in REVIEW_METHODS:
        raise OrchestrationError(f"Unsupported reviewer method: {method}")
    completed_manual_revision = any(
        item.get("phase") == "manual-revision" and item.get("status") == "completed"
        for item in state.get("phases", [])
    )
    current_phase = state.get("currentPhase")
    manual_revision_index = PHASES.index("manual-revision")
    selectable_phases = PHASES[: manual_revision_index + 1]
    if completed_manual_revision or current_phase not in selectable_phases:
        raise RunBlocked(
            "Reviewer method can only change before the first manual-revision checkpoint"
        )
    current = review_method(state)
    if current == method and state.get("reviewer"):
        return state["reviewer"]
    state["reviewer"] = {
        "method": method,
        "selectedAt": now(),
    }
    save_state(state)
    return state["reviewer"]
