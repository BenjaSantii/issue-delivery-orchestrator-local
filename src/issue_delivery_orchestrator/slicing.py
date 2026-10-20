"""Measure a run's own delta and gate PR size on an explicit user decision.

Only product lines count toward the thresholds: tests are reported separately,
and lockfiles, generated migrations, snapshots and docs are excluded. The
engine never splits on its own; it pauses for a decision. Keeping one PR is a
valid decision and suppresses new prompts until the delta doubles.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .config import settings
from .errors import RunBlocked
from .stack import integration_ref, pr_target
from .state import save_state
from .util import run

_TEST = re.compile(
    r"(^|/)(__tests__|__mocks__|e2e|tests?)/"
    r"|\.(spec|test)\.[cm]?[jt]sx?$"
    r"|(^|/)test_[^/]*\.py$|_test\.py$"
)
_EXCLUDED = re.compile(
    r"(^|/)(pnpm-lock\.yaml|package-lock\.json|yarn\.lock)$"
    r"|(^|/)migrations/"
    r"|(^|/)__snapshots__/|\.snap$"
    r"|(^|/)contracts/.*\.json$"
    r"|\.md$"
)
DECISIONS = ("single-pr", "split")


def classify(path: str) -> str:
    if _EXCLUDED.search(path):
        return "excluded"
    if _TEST.search(path):
        return "test"
    return "product"


def measure(state: dict[str, Any]) -> dict[str, Any]:
    worktree = Path(state["worktree"])
    run(["git", "fetch", "origin", pr_target(state)], cwd=worktree, check=False)
    ref = integration_ref(state)
    base = run(["git", "merge-base", "HEAD", ref], cwd=worktree).stdout.strip()
    numstat = run(["git", "diff", "--numstat", f"{base}..HEAD"], cwd=worktree).stdout
    totals = {"product": 0, "test": 0, "excluded": 0}
    files: list[dict[str, Any]] = []
    for line in numstat.splitlines():
        added, deleted, path = line.split("\t", 2)
        if added == "-":
            kind, lines = "excluded", 0
        else:
            kind, lines = classify(path), int(added) + int(deleted)
        totals[kind] += lines
        files.append({"path": path, "kind": kind, "lines": lines})
    files.sort(key=lambda item: item["lines"], reverse=True)
    return {
        "against": ref,
        "head": run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip(),
        "productLines": totals["product"],
        "testLines": totals["test"],
        "excludedLines": totals["excluded"],
        "largestProductFiles": [item for item in files if item["kind"] == "product"][:10],
    }


def record_estimate(state: dict[str, Any], lines: int) -> dict[str, Any]:
    if lines <= 0:
        raise RunBlocked("The size estimate must be a positive number of product lines")
    size = state.setdefault("size", {})
    size["estimate"] = lines
    size["estimatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state)
    return size


def _accepted_lines(state: dict[str, Any]) -> int:
    decisions = (state.get("size") or {}).get("decisions", [])
    accepted = [item for item in decisions if item.get("decision") == "single-pr"]
    return int(accepted[-1]["atProductLines"]) if accepted else 0


def evaluate(state: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
    slicing = settings().slicing
    product = measured["productLines"]
    accepted = _accepted_lines(state)
    reprompt_floor = accepted * 2
    estimate = (state.get("size") or {}).get("estimate")
    reasons = []
    threshold = max(slicing.max_product_lines, reprompt_floor)
    if product > threshold:
        reasons.append(f"{product} product lines exceed {threshold}")
    if estimate:
        # An estimate overrun cannot request a split below the PR-size threshold.
        limit = max(int(estimate * slicing.estimate_deviation), threshold)
        if product > limit:
            reasons.append(
                f"{product} product lines exceed the estimate {estimate} "
                f"x{slicing.estimate_deviation:g} ({limit})"
            )
    return {
        **measured,
        "estimate": estimate,
        "acceptedAt": accepted or None,
        "status": "NEEDS_DECISION" if reasons else "OK",
        "reasons": reasons,
    }


def size_check(state: dict[str, Any]) -> dict[str, Any]:
    result = evaluate(state, measure(state))
    size = state.setdefault("size", {})
    size["lastCheck"] = {
        key: result[key]
        for key in ("head", "productLines", "testLines", "status", "reasons")
    }
    save_state(state)
    return result


def record_decision(state: dict[str, Any], decision: str, reason: str) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise RunBlocked(f"Size decision must be one of {', '.join(DECISIONS)}")
    if not reason.strip():
        raise RunBlocked("Record the user's reason for the size decision")
    measured = measure(state)
    entry = {
        "decision": decision,
        "reason": reason.strip(),
        "atProductLines": measured["productLines"],
        "head": measured["head"],
        "decidedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.setdefault("size", {}).setdefault("decisions", []).append(entry)
    save_state(state)
    return entry


def assert_size_accepted(state: dict[str, Any]) -> dict[str, Any]:
    result = evaluate(state, measure(state))
    if result["status"] != "OK":
        raise RunBlocked(
            "PR size needs a user decision before opening it: "
            + "; ".join(result["reasons"])
            + ". Record size-decision single-pr, or split with stack-cut/slice-extract"
        )
    return result
