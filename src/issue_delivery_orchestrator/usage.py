"""Normalize reported Conductor usage, without billing estimates or cache double counts."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import OrchestrationError


def conductor_usage(transcript: dict[str, Any], session_id: str) -> dict[str, Any]:
    requests: dict[str, dict[str, Any]] = {}
    first_event = None
    last_event = None
    pending = False
    unsupported = False
    claude_totals: dict[str, dict[str, int]] = {}
    claude_turns: set[str] = set()
    claude_effort = None
    for row in transcript.get("data", []):
        if row.get("sessionId") != session_id:
            raise OrchestrationError("Transcript contains a different or unidentified session")
        if row.get("receivedAt"):
            first_event = first_event or row["receivedAt"]
            last_event = row["receivedAt"]
        raw = (row.get("content") or {}).get("rawPayload") or {}
        event = raw.get("event") or {}
        if row.get("type") == "userMessage" or event.get("type") == "turn.started":
            pending = True
            config = (row.get("content") or {}).get("config") or {}
            claude_effort = config.get("thinkingLevel") or claude_effort
        if raw.get("type") == "result":
            pending = False
            if raw.get("uuid") in claude_turns:
                continue
            reported = _claude_requests(raw, claude_totals, session_id, claude_effort)
            if reported is None:
                unsupported = True
                continue
            claude_turns.add(raw["uuid"])
            requests.update(reported)
            continue
        if event.get("type") != "turn.completed":
            continue
        pending = False
        metadata = event.get("conductor_sdk_metadata") or {}
        reported = metadata.get("codexUsage") or {}
        reports = reported.get("reports") if reported.get("schemaVersion") == 1 else None
        if not isinstance(reports, list) or not reports:
            unsupported = True
            continue  # event.usage can contain only the LAST request, not the whole turn.
        for report in reports:
            identity = [session_id, report.get("threadId"), report.get("turnId"),
                        report.get("cumulativeTotalTokens")]
            if any(value is None or value == "" for value in identity):
                raise OrchestrationError("Usage report lacks a stable request identity")
            usage = report.get("usage") or {}
            if type(report["cumulativeTotalTokens"]) is not int or report["cumulativeTotalTokens"] < 0:
                raise OrchestrationError("Invalid cumulative token counter")
            fields = ("inputTokens", "outputTokens", "cachedInputTokens",
                      "cacheWriteInputTokens", "reasoningOutputTokens", "totalTokens")
            for field in fields:
                value = usage.get(field)
                if type(value) is not int or value < 0:
                    raise OrchestrationError(f"Invalid reported {field}")
            if usage["totalTokens"] != usage["inputTokens"] + usage["outputTokens"]:
                raise OrchestrationError("Inconsistent reported token total")
            if usage["cachedInputTokens"] > usage["inputTokens"]:
                raise OrchestrationError("Cached tokens exceed input tokens")
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            item = {**{field: usage[field] for field in fields}, "provider": "codex",
                    "model": report.get("model"), "effort": metadata.get("reasoningEffort")}
            if key in requests and requests[key] != item:
                raise OrchestrationError("Conflicting duplicate usage report")
            requests[key] = item
    # Claude counters accumulate, so finished turns stay exact while a later one is open.
    # A Conductor run is one long turn: without this, its own controller could never import it.
    claude_only = bool(requests) and all(r["provider"] == "claude" for r in requests.values())
    complete = bool(requests) and not unsupported and (not pending or claude_only)
    return {"source": "conductor-sdk", "sessionId": session_id,
            "availability": "reported" if complete else "unavailable",
            "incomplete": complete and pending,
            "reason": ("Open turn: its usage is still pending" if complete and pending
                       else None if complete else "Missing usage or an unfinished/unsupported turn"),
            "firstEventAt": first_event, "lastEventAt": last_event,
            "requests": requests if complete else {}}


CLAUDE_COUNTERS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
                   "cacheCreationInputTokens", "thinkingTokens")
CLAUDE_TURN_COUNTERS = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
                        "cacheReadInputTokens": "cache_read_input_tokens",
                        "cacheCreationInputTokens": "cache_creation_input_tokens"}


def _claude_requests(result: dict[str, Any], previous: dict[str, dict[str, int]],
                     session_id: str, effort: str | None) -> dict[str, dict[str, Any]] | None:
    models = result.get("modelUsage")
    if not result.get("uuid") or not isinstance(models, dict) or not models:
        return None
    current = {}
    for model, counters in models.items():
        values = {field: (counters or {}).get(field, 0) for field in CLAUDE_COUNTERS}
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise OrchestrationError(f"Invalid reported Claude usage for {model}")
        current[model] = values
    # modelUsage accumulates for the life of the Claude process; usage covers only this turn.
    # A lower counter, a missing model, or no delta that covers this turn means a new process.
    turn = result.get("usage") or {}
    zero = dict.fromkeys(CLAUDE_COUNTERS, 0)
    deltas = {model: {field: values[field] - previous.get(model, zero)[field]
                      for field in CLAUDE_COUNTERS} for model, values in current.items()}
    covers_turn = any(all(delta[field] >= turn.get(name, 0) for field, name in CLAUDE_TURN_COUNTERS.items())
                      for delta in deltas.values())
    if set(previous) - set(current) or not covers_turn or any(
        value < 0 for delta in deltas.values() for value in delta.values()
    ):
        deltas = current
    previous.clear()
    previous.update(current)
    requests = {}
    for model, delta in deltas.items():
        if not any(delta.values()):
            continue
        cached, written = delta["cacheReadInputTokens"], delta["cacheCreationInputTokens"]
        prompt = delta["inputTokens"] + cached + written
        key = hashlib.sha256(json.dumps([session_id, result["uuid"], model]).encode()).hexdigest()
        requests[key] = {"inputTokens": prompt, "outputTokens": delta["outputTokens"],
                         "cachedInputTokens": cached, "cacheWriteInputTokens": written,
                         "reasoningOutputTokens": delta["thinkingTokens"],
                         "totalTokens": prompt + delta["outputTokens"],
                         "provider": "claude", "model": model, "effort": effort}
    return requests


def token_totals(requests: dict[str, dict[str, Any]]) -> dict[str, int]:
    # Cached input and reasoning are reported separately; never add them to totalTokens.
    return {key: sum(item[key] for item in requests.values()) for key in (
        "inputTokens", "outputTokens", "cachedInputTokens", "cacheWriteInputTokens",
        "reasoningOutputTokens", "totalTokens",
    )}
