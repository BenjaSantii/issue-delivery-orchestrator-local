"""Deterministic checks a project ships in its own repository.

The profile names a script inside the worktree and the command that runs it. The
script prints JSON with ``findings``; each finding has an ``id``, a ``message``,
an optional ``file`` and ``line``, and ``acceptable`` when the project allows an
exception recorded with a reason. Findings without ``acceptable`` can only be
fixed or escalated to the user.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ProjectCheck, settings
from .errors import RunBlocked
from .state import now, save_state
from .stack import integration_ref
from .util import run

GATED_PHASES = frozenset({"implement", "refactor", "review-convergence"})


def _command(check: ProjectCheck, state: dict[str, Any]) -> list[str]:
    return [part.replace("{base}", integration_ref(state)) for part in check.command]


def _run_check(check: ProjectCheck, state: dict[str, Any]) -> list[dict[str, Any]]:
    worktree = Path(state["worktree"])
    result = run(_command(check, state), cwd=worktree, check=False)
    try:
        payload = json.loads(result.stdout)
        findings = payload["findings"]
        if not isinstance(findings, list):
            raise TypeError("findings must be a list")
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RunBlocked(
            f"Project check {check.name} did not return its JSON report ({error}). "
            f"Fix the check run before continuing. Output: {detail}"
        ) from error
    return [
        {
            "check": check.name,
            "id": str(item.get("id") or "unknown"),
            "message": str(item.get("message") or ""),
            "file": item.get("file"),
            "line": item.get("line"),
            "acceptable": item.get("acceptable") is True,
        }
        for item in findings
        if isinstance(item, dict)
    ]


def _exceptions(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list(state.get("projectCheckExceptions") or [])


def _excepted(state: dict[str, Any], finding: dict[str, Any]) -> bool:
    return finding["acceptable"] and any(
        entry["check"] == finding["check"] and entry["id"] == finding["id"]
        for entry in _exceptions(state)
    )


def evaluate(state: dict[str, Any]) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    ran: list[str] = []
    skipped: list[str] = []
    findings: list[dict[str, Any]] = []
    for check in settings().project_checks:
        if not (worktree / check.script).is_file():
            skipped.append(check.name)
            continue
        ran.append(check.name)
        findings.extend(_run_check(check, state))
    open_findings = [finding for finding in findings if not _excepted(state, finding)]
    return {
        "ran": ran,
        "skipped": skipped,
        "findings": open_findings,
        "exceptions": _exceptions(state),
    }


def _format(finding: dict[str, Any]) -> str:
    location = finding.get("file") or ""
    if location and finding.get("line"):
        location = f"{location}:{finding['line']}"
    prefix = f"[{finding['check']}/{finding['id']}]"
    if finding["acceptable"]:
        prefix += " (exception allowed with accept-check)"
    return f"{prefix} {location + ' ' if location else ''}{finding['message']}"


def assert_project_checks(state: dict[str, Any], phase: str) -> dict[str, Any] | None:
    if phase not in GATED_PHASES or not settings().project_checks:
        return None
    report = evaluate(state)
    if report["findings"]:
        lines = "\n".join(_format(finding) for finding in report["findings"])
        raise RunBlocked(
            f"Project checks failed before completing {phase}. Fix each finding and run the "
            f"checkpoint again. A finding without an allowed exception cannot be skipped: if "
            f"it cannot be fixed, block the run with --decision and the reason.\n{lines}"
        )
    return report


def accept(state: dict[str, Any], check: str, finding_id: str, reason: str) -> dict[str, Any]:
    reason = reason.strip()
    if not reason:
        raise RunBlocked("An exception needs a reason the user can read in the handoff")
    report = evaluate(state)
    matches = [
        finding
        for finding in report["findings"]
        if finding["check"] == check and finding["id"] == finding_id
    ]
    if not matches:
        raise RunBlocked(f"Project check {check} does not report {finding_id} right now")
    if not all(finding["acceptable"] for finding in matches):
        raise RunBlocked(
            f"{check}/{finding_id} does not allow exceptions. Fix it, or block the run "
            f"with --decision and the reason."
        )
    entry = {"check": check, "id": finding_id, "reason": reason, "recordedAt": now()}
    state["projectCheckExceptions"] = [*_exceptions(state), entry]
    save_state(state)
    return entry
