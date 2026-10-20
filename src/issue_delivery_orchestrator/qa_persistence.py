"""Require observable database consequences for new-run QA, with an explicit UI-only escape."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .errors import OrchestrationError, RunBlocked
from .util import read_json


def validate_persistence(case: dict[str, Any], item: dict[str, Any], runtime_id: str,
                         artifact: Callable[[str], str], worktree: Path) -> dict[str, Any]:
    raw = item.get("persistence")
    qa_id = case["QA ID"]
    if not isinstance(raw, dict):
        raise RunBlocked(f"{qa_id} needs a persistence verification or a justified NOT_APPLICABLE")
    if raw.get("status") == "NOT_APPLICABLE":
        required = case["Type"] == "concurrency" or re.search(r"\bDB\b", case.get("Evidence", ""), re.I)
        if required or not str(raw.get("reason") or "").strip():
            raise RunBlocked(f"{qa_id} cannot skip database verification without an applicable reason")
        return {"status": "NOT_APPLICABLE", "reason": str(raw["reason"]).strip()}
    if raw.get("status") != "VERIFIED" or raw.get("runtimeId") != runtime_id:
        raise RunBlocked(f"{qa_id} persistence must be VERIFIED against the registered local runtime")
    if not str(raw.get("expectation") or "").strip() or "expectedAfter" not in raw:
        raise OrchestrationError(f"{qa_id} persistence needs expectation and expectedAfter")
    files = {key: artifact(raw.get(key)) for key in ("before", "after", "query", "readOnlyProof")}
    try:
        read_json(worktree / files["before"])
        after = read_json(worktree / files["after"])
        proof = read_json(worktree / files["readOnlyProof"])
    except (ValueError, OSError) as error:
        raise OrchestrationError(f"{qa_id} DB evidence must be readable JSON") from error
    if not isinstance(proof, dict) or proof.get("runtimeId") != runtime_id or (
        proof.get("transactionReadOnly") is not True or proof.get("target") != "local-runtime"
    ):
        raise RunBlocked(f"{qa_id} needs evidence of read-only access to the local runtime DB")
    if not (worktree / files["query"]).read_text().strip():
        raise OrchestrationError(f"{qa_id} needs the read-only query used for verification")
    # Compare serialized JSON so bool/number coercions cannot turn a failed observation into PASS.
    if json.dumps(after, sort_keys=True) != json.dumps(raw["expectedAfter"], sort_keys=True):
        raise RunBlocked(f"{qa_id} observed database state does not match expectedAfter")
    return {"status": "VERIFIED", "runtimeId": runtime_id,
            "expectation": str(raw["expectation"]).strip(), "expectedAfter": raw["expectedAfter"],
            **files}
