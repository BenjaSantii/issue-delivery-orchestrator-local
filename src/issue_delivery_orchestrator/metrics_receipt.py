"""Validate optional observations; unavailable measurements stay unavailable."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import OrchestrationError


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result
    except (ValueError, TypeError, AttributeError) as error:
        raise OrchestrationError("Metrics timestamps must be ISO-8601 with a timezone") from error


def observations(raw: Any, started: str, finished: str) -> dict[str, Any]:
    if raw is None:
        return {"waitSeconds": None, "checks": None, "findings": None, "repairCycles": None}
    if not isinstance(raw, dict):
        raise OrchestrationError("Metrics receipt must be an object")
    start, end = timestamp(started), timestamp(finished)
    intervals = []
    waits = {"human": 0.0, "infrastructure": 0.0}
    waiting = raw.get("waitingIntervals", [])
    if not isinstance(waiting, list):
        raise OrchestrationError("Waiting intervals must be a list")
    for item in waiting:
        if not isinstance(item, dict):
            raise OrchestrationError("Waiting intervals must be objects")
        reason = item.get("reason")
        if reason not in waits:
            raise OrchestrationError("Unknown metrics waiting reason")
        left, right = timestamp(item.get("startedAt")), timestamp(item.get("finishedAt"))
        if not start <= left <= right <= end:
            raise OrchestrationError("Waiting interval falls outside the measured unit")
        if any(left < other_end and right > other_start for other_start, other_end in intervals):
            raise OrchestrationError("Waiting intervals overlap")
        intervals.append((left, right))
        waits[reason] += (right - left).total_seconds()
    counts = raw.get("checks")
    if counts is not None:
        if not isinstance(counts, dict) or set(counts) != {"passed", "failed", "blocked"}:
            raise OrchestrationError("Checks require passed/failed/blocked counts")
        for value in counts.values():
            positive_integer(value)
    cycles = raw.get("repairCycles")
    if cycles is not None:
        positive_integer(cycles)
    findings = raw.get("findings")
    if findings is not None:
        if not isinstance(findings, list):
            raise OrchestrationError("Findings must be a list")
        seen = set()
        clean = []
        for item in findings:
            if not isinstance(item, dict):
                raise OrchestrationError("Findings must be objects")
            identity = item.get("id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise OrchestrationError("Findings require distinct IDs")
            seen.add(identity)
            if item.get("duplicateOf") is not None and not isinstance(item["duplicateOf"], str):
                raise OrchestrationError("duplicateOf must identify another finding")
            if item.get("severity") not in {"critical", "high", "medium", "low"}:
                raise OrchestrationError("Finding severity is required")
            clean.append({"id": identity, "severity": item["severity"],
                          "duplicateOf": item.get("duplicateOf")})
        findings = clean
    return {"waitSeconds": waits if "waitingIntervals" in raw else None,
            "checks": counts, "findings": findings, "repairCycles": cycles}


def positive_integer(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise OrchestrationError("Metrics counts must be non-negative integers")
