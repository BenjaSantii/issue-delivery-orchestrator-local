"""QA script: the approved list of cases that manual revision must execute.

The script lives in the spec under `## QA Script`, so it is approved and published with
it. Coverage rules keep it from depending on the reviewer's judgment: every user story
has a happy path, every applicable risk has a case, and every concurrency case names
the committed race test that keeps protecting it after the run.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .errors import OrchestrationError, RunBlocked
from .spec_lint import RISK_CATEGORIES, _blank, _section, _table
from .state import run_root
from .util import ensure_within, read_json, run

QA_HEADING = "## QA Script"
QA_HEADER = (
    "QA ID",
    "Type",
    "Covers",
    "Surface",
    "Steps",
    "Expected",
    "Evidence",
    "Regression test",
)
QA_TYPES = ("happy-path", "edge-case", "concurrency", "authorization", "failure")
QA_SURFACES = ("UI", "API", "command")
RESULT_STATUSES = ("PASS", "FAIL", "BLOCKED", "WAIVED")
_QA_ID = re.compile(r"^QA-\d{3}$")
_STORY_ID = re.compile(r"\bUS-\d{3}\b")
_PATH = re.compile(r"`([^`]+)`")
_SECRET = re.compile(
    r"lin_api_[A-Za-z0-9]{8,}|gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
OUTPUT_EXCERPT_LINES = 12


def qa_cases(spec: str) -> list[dict[str, str]] | None:
    """Return the QA cases of a spec, or None when the spec predates QA scripts."""
    section = _section(spec, QA_HEADING)
    if section is None:
        return None
    header, rows = _table(section)
    if header != QA_HEADER:
        return []
    return [dict(zip(QA_HEADER, row)) for row in rows if len(row) == len(QA_HEADER)]


def lint_qa_script(spec: str) -> list[str]:
    section = _section(spec, QA_HEADING)
    if section is None:
        return [f"Missing section `{QA_HEADING}`"]
    header, rows = _table(section)
    if header != QA_HEADER:
        return [f"`{QA_HEADING}` table header must be: | {' | '.join(QA_HEADER)} |"]
    if not rows:
        return [f"`{QA_HEADING}` table has no rows"]

    errors: list[str] = []
    seen: set[str] = set()
    cases = []
    for row in rows:
        if len(row) != len(QA_HEADER):
            errors.append(f"QA Script row has {len(row)} cells: {' | '.join(row)}")
            continue
        case = dict(zip(QA_HEADER, row))
        cases.append(case)
        qa_id = case["QA ID"]
        if not _QA_ID.match(qa_id):
            errors.append(f"QA ID `{qa_id}` must look like QA-001")
        elif qa_id in seen:
            errors.append(f"Duplicate QA ID `{qa_id}`")
        seen.add(qa_id)
        if case["Type"] not in QA_TYPES:
            errors.append(f"`{qa_id}` Type must be one of {', '.join(QA_TYPES)}")
        if case["Surface"] not in QA_SURFACES:
            errors.append(f"`{qa_id}` Surface must be one of {', '.join(QA_SURFACES)}")
        for field in ("Covers", "Steps", "Expected", "Evidence"):
            if _blank(case[field]):
                errors.append(f"`{qa_id}` needs {field}")
        if case["Type"] == "concurrency":
            if case["Surface"] == "UI":
                errors.append(f"`{qa_id}` concurrency must be exercised through API or command")
            if not _PATH.search(case["Regression test"]):
                errors.append(
                    f"`{qa_id}` concurrency needs the committed race test path in backticks "
                    "under Regression test"
                )

    stories = user_story_ids(spec)
    happy = {
        story
        for case in cases
        if case["Type"] == "happy-path"
        for story in _STORY_ID.findall(case["Covers"])
    }
    for story in stories:
        if story not in happy:
            errors.append(f"`{story}` needs at least one happy-path QA case")
    for story in sorted({s for case in cases for s in _STORY_ID.findall(case["Covers"])}):
        if story not in stories:
            errors.append(f"QA Script covers unknown user story `{story}`")

    covered = " ".join(case["Covers"] for case in cases)
    for category in applicable_risks(spec):
        if category not in covered:
            errors.append(f"Risk `{category}` applies, so a QA case must cover it")
    if "concurrency" in applicable_risks(spec) and not any(
        case["Type"] == "concurrency" for case in cases
    ):
        errors.append("Risk `concurrency` applies, so the QA Script needs a concurrency case")
    return errors


def user_story_ids(spec: str) -> list[str]:
    section = _section(spec, "## User Stories") or ""
    return re.findall(r"(?m)^\s*[-*]\s*(US-\d{3})\s*:", section)


def applicable_risks(spec: str) -> list[str]:
    _, rows = _table(_section(spec, "## Risk Catalog") or "")
    applies = []
    for row in rows:
        if len(row) >= 2 and row[1].strip().lower() == "yes":
            category = row[0].strip("` ").lower()
            if category in RISK_CATEGORIES:
                applies.append(category)
    return applies


def approved_qa_cases(state: dict[str, Any]) -> list[dict[str, str]] | None:
    relative = (state.get("artifacts") or {}).get("spec")
    if not relative:
        return None
    path = Path(state["worktree"]) / relative
    if not path.is_file():
        raise OrchestrationError(f"Approved spec snapshot is missing: {path}")
    return qa_cases(path.read_text())


def validate_qa_results(state: dict[str, Any], results_path: Path) -> dict[str, Any] | None:
    """Check a QA results file against the approved script; None for legacy specs."""
    cases = approved_qa_cases(state)
    if cases is None:
        return None
    worktree = Path(state["worktree"])
    root = run_root(worktree, state["runId"])
    results = read_json(results_path)
    if not isinstance(results, dict) or results.get("qaVersion") != 1:
        raise OrchestrationError("QA results must be a JSON object with qaVersion 1")

    head = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    if str(results.get("verifiedCommit") or "") != head:
        raise OrchestrationError(
            f"QA results are stale: verified {results.get('verifiedCommit') or '<empty>'}, "
            f"current HEAD is {head}"
        )
    runtimes = {item.get("runtimeId") for item in state.get("runtimes", [])}
    if results.get("runtimeId") not in runtimes:
        raise OrchestrationError(
            f"QA results reference unregistered runtime {results.get('runtimeId')}"
        )

    by_id: dict[str, dict[str, Any]] = {}
    for item in results.get("cases") or []:
        qa_id = str((item or {}).get("id") or "")
        if qa_id in by_id:
            raise OrchestrationError(f"QA results list {qa_id} twice")
        by_id[qa_id] = item
    expected = {case["QA ID"]: case for case in cases}
    unknown = sorted(set(by_id) - set(expected))
    missing = sorted(set(expected) - set(by_id))
    if unknown:
        raise OrchestrationError(f"QA results contain cases not in the script: {', '.join(unknown)}")
    if missing:
        raise OrchestrationError(f"QA results are missing cases: {', '.join(missing)}")

    failing = []
    normalized = []
    for qa_id, case in expected.items():
        item = by_id[qa_id]
        status = str(item.get("status") or "")
        if status not in RESULT_STATUSES:
            raise OrchestrationError(f"{qa_id} has invalid status {status or '<empty>'}")
        summary = str(item.get("summary") or "").strip()
        if not summary:
            raise OrchestrationError(f"{qa_id} needs a summary of what was observed")
        _reject_secrets(qa_id, summary)
        entry = {
            "id": qa_id,
            "type": case["Type"],
            "surface": case["Surface"],
            "covers": case["Covers"],
            "status": status,
            "summary": summary,
            "screenshots": [],
            "output": None,
        }
        if status in {"FAIL", "BLOCKED"}:
            failing.append(f"{qa_id} {status}: {summary}")
        if status == "WAIVED":
            waiver = item.get("waiver") or {}
            if _blank(str(waiver.get("reason") or "")) or _blank(
                str(waiver.get("userInstruction") or "")
            ):
                raise OrchestrationError(
                    f"{qa_id} WAIVED requires waiver.reason and the user's waiver.userInstruction"
                )
            entry["waiver"] = {
                "reason": str(waiver["reason"]).strip(),
                "userInstruction": str(waiver["userInstruction"]).strip(),
            }
        if status == "PASS":
            entry["screenshots"] = [
                str(_run_file(root, worktree, raw, qa_id, suffixes={".png"}))
                for raw in item.get("screenshots") or []
            ]
            if case["Surface"] == "UI" and not entry["screenshots"]:
                raise OrchestrationError(f"{qa_id} is a UI case and needs at least one screenshot")
            if item.get("output"):
                output = _run_file(root, worktree, item["output"], qa_id)
                _reject_secrets(qa_id, (worktree / output).read_text(errors="replace"))
                entry["output"] = str(output)
            elif case["Surface"] != "UI":
                raise OrchestrationError(
                    f"{qa_id} is a {case['Surface']} case and needs its command output file"
                )
            if case["Type"] == "concurrency":
                for test_path in _PATH.findall(case["Regression test"]):
                    if not _tracked(worktree, test_path):
                        raise OrchestrationError(
                            f"{qa_id} regression test `{test_path}` is not committed"
                        )
        normalized.append(entry)
    if failing:
        raise RunBlocked(
            "QA cases are not passing; repair them or record the user's waiver:\n- "
            + "\n- ".join(failing)
        )
    return {
        "verifiedCommit": head,
        "runtimeId": results["runtimeId"],
        "verifiedAt": results.get("verifiedAt"),
        "cases": normalized,
    }


def qa_comment_body(
    run_id: str,
    results: dict[str, Any],
    asset_urls: dict[str, str],
    worktree: Path,
) -> str:
    rows = []
    for case in results["cases"]:
        icon = {"PASS": "✅", "WAIVED": "⚪"}.get(case["status"], "❌")
        summary = case["summary"].replace("|", "\\|").replace("\n", " ")
        rows.append(
            f"| {case['id']} | {case['type']} | {case['surface']} | {case['covers']} "
            f"| {icon} {case['status']} | {summary} |"
        )
    sections = [
        f"### {qa_comment_marker(run_id)}",
        "",
        f"Commit `{results['verifiedCommit'][:12]}` · runtime `{results['runtimeId']}`",
        "",
        "| Caso | Tipo | Superficie | Cubre | Estado | Observado |",
        "| -- | -- | -- | -- | -- | -- |",
        *rows,
    ]
    for case in results["cases"]:
        details = []
        for screenshot in case["screenshots"]:
            url = asset_urls.get(screenshot)
            if url:
                details.append(f"![{case['id']}]({url})")
        if case["output"]:
            lines = (worktree / case["output"]).read_text(errors="replace").splitlines()
            excerpt = "\n".join(lines[:OUTPUT_EXCERPT_LINES])
            more = "\n…" if len(lines) > OUTPUT_EXCERPT_LINES else ""
            details.append(f"```\n{excerpt}{more}\n```")
        if case.get("waiver"):
            details.append(
                f"Excepción aprobada por el usuario: {case['waiver']['reason']} "
                f"(«{case['waiver']['userInstruction']}»)"
            )
        if details:
            sections += ["", f"#### {case['id']}", "", *details]
    return "\n".join(sections) + "\n"


def qa_comment_marker(run_id: str) -> str:
    # Matched as plain text so Linear's markdown normalization of the heading cannot
    # turn a republish into a duplicate comment.
    return f"Resultado QA — {run_id}"


def _run_file(
    root: Path,
    worktree: Path,
    raw: Any,
    qa_id: str,
    *,
    suffixes: set[str] | None = None,
) -> Path:
    candidate = Path(str(raw))
    path = ensure_within(candidate if candidate.is_absolute() else worktree / candidate, root)
    if not path.is_file():
        raise OrchestrationError(f"{qa_id} evidence file does not exist: {path}")
    if suffixes and path.suffix.lower() not in suffixes:
        raise OrchestrationError(f"{qa_id} evidence {path.name} must be one of {sorted(suffixes)}")
    return path.relative_to(worktree.resolve())


def _tracked(worktree: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _reject_secrets(qa_id: str, text: str) -> None:
    if _SECRET.search(text):
        raise OrchestrationError(
            f"{qa_id} evidence contains what looks like a secret; redact it before publishing"
        )

