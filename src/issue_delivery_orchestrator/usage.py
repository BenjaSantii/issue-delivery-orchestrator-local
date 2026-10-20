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
            item = {**{field: usage[field] for field in fields},
                    "model": report.get("model"), "effort": metadata.get("reasoningEffort")}
            if key in requests and requests[key] != item:
                raise OrchestrationError("Conflicting duplicate usage report")
            requests[key] = item
    complete = bool(requests) and not pending and not unsupported
    return {"source": "conductor-sdk", "sessionId": session_id,
            "availability": "reported" if complete else "unavailable",
            "reason": None if complete else "Missing usage or an unfinished/unsupported turn",
            "firstEventAt": first_event, "lastEventAt": last_event,
            "requests": requests if complete else {}}


def token_totals(requests: dict[str, dict[str, Any]]) -> dict[str, int]:
    # Cached input and reasoning are reported separately; never add them to totalTokens.
    return {key: sum(item[key] for item in requests.values()) for key in (
        "inputTokens", "outputTokens", "cachedInputTokens", "cacheWriteInputTokens",
        "reasoningOutputTokens", "totalTokens",
    )}
