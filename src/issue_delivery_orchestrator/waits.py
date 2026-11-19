"""Wait and check time measured from the engine's own pauses and from imported chats."""
from __future__ import annotations

import re
import shlex
from datetime import datetime
from typing import Any, Iterable

from .metrics_receipt import timestamp

REASONS = ("human", "infrastructure")
RUNNERS = {"jest", "vitest", "tsc", "eslint", "pytest", "playwright", "prettier"}
NX_CHECKS = {"test", "lint", "typecheck", "e2e", "type-check"}
SCRIPT_CHECKS = {"test", "lint", "typecheck", "type-check", "e2e"}
WRAPPERS = {"heavy", "timeout", "time", "nice", "env"}
SEPARATORS = re.compile(r"&&|\|\||[;|\n()]")

Interval = tuple[datetime, datetime]


def record(state: dict[str, Any], reason: str, source: str, started: str, finished: str) -> None:
    if reason not in REASONS:
        raise ValueError(f"Unknown wait reason {reason}")
    if timestamp(finished) <= timestamp(started):
        return
    state.setdefault("waitIntervals", []).append(
        {"reason": reason, "source": source, "startedAt": started, "finishedAt": finished})


def is_check_command(command: str) -> bool:
    return any(_segment_is_check(part) for part in SEPARATORS.split(command or ""))


def _segment_is_check(segment: str) -> bool:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and ("=" in words[0] and not words[0].startswith("=") or words[0] in WRAPPERS
                     or words[0].lstrip("-").isdigit()):
        words = words[1:]
    if words[:1] in (["pnpm"], ["npx"], ["npm"], ["yarn"]):
        words = words[1:]
        if words[:1] in (["exec"], ["run"]):
            words = words[1:]
        if words and words[0] in SCRIPT_CHECKS:
            return True
    if words[:1] in (["python"], ["python3"]) and words[1:3] in (["-m", "unittest"], ["-m", "pytest"]):
        return True
    if not words:
        return False
    if words[0] == "nx":
        targets = {part.split(":")[-1] for part in words[1:4]}
        targets |= {name for part in words[1:] if part.startswith("--target")
                    for name in part.split("=", 1)[-1].split(",")}
        return bool(NX_CHECKS & targets)
    if words[0] == "prettier":
        return "--check" in words
    if words[0] == "playwright":
        return words[1:2] == ["test"]
    return words[0] in RUNNERS


def conversation_signals(transcript: dict[str, Any], session_id: str) -> dict[str, Any]:
    rows = sorted((row for row in transcript.get("data", [])
                   if row.get("sessionId") == session_id and row.get("receivedAt")),
                  key=lambda row: timestamp(row["receivedAt"]))
    gaps: list[list[str]] = []
    checks: list[list[str]] = []
    pending_checks: dict[str, str] = {}
    turn_ended: str | None = None
    for row in rows:
        at = row["receivedAt"]
        raw = (row.get("content") or {}).get("rawPayload") or {}
        event = raw.get("event") or {}
        if row.get("type") == "userMessage":
            if turn_ended:
                gaps.append([turn_ended, at])
            turn_ended = None
            continue
        if raw.get("type") == "result" or event.get("type") == "turn.completed":
            turn_ended = at
            continue
        content = (raw.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and is_check_command(
                    str((part.get("input") or {}).get("command") or "")):
                pending_checks[str(part.get("id"))] = at
            elif part.get("type") == "tool_result" and str(part.get("tool_use_id")) in pending_checks:
                checks.append([pending_checks.pop(str(part.get("tool_use_id"))), at])
    return {"coverage": [rows[0]["receivedAt"], rows[-1]["receivedAt"]] if rows else None,
            "humanGaps": gaps, "checks": checks}


def keep_conversation(state: dict[str, Any], session_id: str, label: str,
                      signals: dict[str, Any]) -> None:
    if signals.get("coverage"):
        state.setdefault("conversations", {})[session_id] = {"label": label, **signals}


def _parse(pairs: Iterable[Iterable[str]]) -> list[Interval]:
    return [(timestamp(left), timestamp(right)) for left, right in pairs]


def _union(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _clip(intervals: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    return [(max(left, start), min(right, end)) for left, right in intervals
            if min(right, end) > max(left, start)]


def _minus(intervals: list[Interval], removed: list[Interval]) -> list[Interval]:
    result = []
    for left, right in intervals:
        pieces = [(left, right)]
        for cut_left, cut_right in removed:
            pieces = [part for piece in pieces for part in (
                (piece[0], min(piece[1], cut_left)), (max(piece[0], cut_right), piece[1]))
                if part[1] > part[0]]
        result += pieces
    return result


def _seconds(intervals: list[Interval]) -> float:
    return sum(((right - left).total_seconds() for left, right in intervals), 0.0)


def breakdown(state: dict[str, Any], started: str, finished: str) -> dict[str, Any]:
    start, end = timestamp(started), timestamp(finished)
    engine = state.get("waitIntervals") or []
    conversations = [item for item in (state.get("conversations") or {}).values()]
    main = [item for item in conversations if not str(item.get("label", "")).startswith("QA")]
    covered = any(timestamp(item["coverage"][0]) <= start and end <= timestamp(item["coverage"][1])
                  for item in main)
    human = _union(_clip(_parse((item["startedAt"], item["finishedAt"]) for item in engine
                                if item["reason"] == "human") +
                         _parse(gap for item in main for gap in item["humanGaps"]), start, end))
    infrastructure = _minus(_union(_clip(_parse(
        (item["startedAt"], item["finishedAt"]) for item in engine
        if item["reason"] == "infrastructure"), start, end)), human)
    waiting = _union(human + infrastructure)
    checks = _minus(_union(_clip(_parse(pair for item in conversations for pair in item["checks"]),
                                 start, end)), waiting)
    wall = (end - start).total_seconds()
    return {"humanWaitSeconds": round(_seconds(human), 1),
            "infrastructureWaitSeconds": round(_seconds(infrastructure), 1),
            "checkSeconds": round(_seconds(checks), 1) if conversations else None,
            "executionSeconds": round(wall - _seconds(waiting), 1) if covered else None,
            "conversationCovered": covered}
