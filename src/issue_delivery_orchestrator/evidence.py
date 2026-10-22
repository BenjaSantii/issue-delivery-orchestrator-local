from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import settings
from .errors import OrchestrationError
from .evidence_assets import (
    ensure_github_assets as _ensure_github_assets,
    evidence_target_path as _evidence_target_path,
    fingerprint as _fingerprint,
    upload_linear_asset as _upload_linear_asset,
)
from .evidence_manifest import (
    _prepare_evidence,
    _verification,
    prepare_evidence,
)
from .evidence_presentation import (
    linear_run_section as _linear_run_section,
    pr_body as _pr_body,
)
from .github import GitHubClient
from .linear import LinearClient
from .state import run_root, save_state
from .util import atomic_write_json, ensure_within, read_json


def publish_evidence(
    state: dict[str, Any],
    manifest_path: Path,
    *,
    linear: LinearClient,
    github: GitHubClient | None,
    comments_enabled: bool = True,
) -> dict[str, Any]:
    prepared = _prepare_evidence(state, manifest_path)
    worktree = prepared["worktree"]
    verification = prepared["verification"]
    screenshots = prepared["screenshots"]
    if not screenshots:
        raise OrchestrationError("Evidence manifest contains no screenshots")

    root = run_root(worktree, state["runId"])
    receipt_path = root / "evidence" / "publication-receipt.json"
    fingerprint = _fingerprint(screenshots, verification)
    previous = read_json(receipt_path, {})
    if previous.get("fingerprint") == fingerprint:
        assets = previous["assets"]
        newly_uploaded = False
    else:
        assets = [
            _upload_linear_asset(item, worktree, linear)
            for item in screenshots
        ]
        newly_uploaded = True
        atomic_write_json(
            receipt_path,
            {
                "fingerprint": fingerprint,
                "assets": assets,
                "verification": verification,
                "status": "uploaded",
                "linearIssue": None,
                "prCommentId": None,
            },
        )

    pr = state.get("pr")
    if pr and github:
        assets = _ensure_github_assets(state, assets, screenshots, github)

    issue = linear.issue(state["issue"]["identifier"])
    section = _linear_run_section(state["runId"], assets)
    description = upsert_ui_run(issue.description, state["runId"], section)
    linear.update_description(issue.id, description)
    if newly_uploaded and comments_enabled:
        linear.post_comment(
            issue.id,
            f"Evidencia UI final actualizada para la ejecución `{state['runId']}`.",
        )

    pr_comment_id = None
    if pr and github:
        marker = f"<!-- issue-delivery-ui-evidence:{state['runId']} -->"
        pr_body = _pr_body(
            marker,
            assets,
            provider=verification.get("provider", "cua-driver"),
            headless_assistance=verification.get("headlessAssistance", []),
            upload_assistance=verification.get("uploadAssistance", []),
            linear_url=issue.url,
        )
        pr_comment_id = github.upsert_comment(int(pr["number"]), marker, pr_body)

    receipt = {
        "fingerprint": fingerprint,
        "assets": assets,
        "verification": verification,
        "status": "published",
        "linearIssue": issue.url,
        "prCommentId": pr_comment_id,
    }
    atomic_write_json(receipt_path, receipt)
    state["artifacts"]["uiEvidenceReceipt"] = str(receipt_path.relative_to(worktree))
    save_state(state)
    return receipt


def repair_github_evidence(
    state: dict[str, Any],
    *,
    github: GitHubClient,
) -> dict[str, Any]:
    worktree = Path(state["worktree"]).resolve()
    root = run_root(worktree, state["runId"])
    receipt_path = root / "evidence" / "publication-receipt.json"
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict) or not isinstance(receipt.get("assets"), list):
        raise OrchestrationError(f"Published evidence receipt not found: {receipt_path}")
    if not state.get("pr"):
        raise OrchestrationError("Cannot repair GitHub evidence without a recorded PR")
    screenshots = []
    for index, asset in enumerate(receipt["assets"], start=1):
        display_path = ensure_within(worktree / str(asset.get("path") or ""), worktree)
        original_path = ensure_within(
            worktree / str(asset.get("originalPath") or asset.get("path") or ""),
            worktree,
        )
        for path in (display_path, original_path):
            if not path.is_file() or path.suffix.lower() != ".png":
                raise OrchestrationError(f"Evidence asset {index} is unavailable: {path}")
        screenshots.append(
            {
                **asset,
                "path": ensure_within(
                    worktree
                    / str(asset.get("capturePath") or asset.get("originalPath") or ""),
                    worktree,
                ),
                "originalPath": original_path,
                "displayPath": display_path,
                "callouts": asset.get("callouts") or [],
            }
        )
    assets = _ensure_github_assets(
        state,
        receipt["assets"],
        screenshots,
        github,
        force=not all(asset.get("githubUrl") for asset in receipt["assets"]),
    )
    marker = f"<!-- issue-delivery-ui-evidence:{state['runId']} -->"
    comment_id = github.upsert_comment(
        int(state["pr"]["number"]),
        marker,
        _pr_body(
            marker,
            assets,
            provider=(receipt.get("verification") or {}).get(
                "provider",
                "cua-driver",
            ),
            headless_assistance=(receipt.get("verification") or {}).get(
                "headlessAssistance",
                [],
            ),
            upload_assistance=(receipt.get("verification") or {}).get(
                "uploadAssistance",
                [],
            ),
            linear_url=receipt.get("linearIssue"),
        ),
    )
    updated = {
        **receipt,
        "assets": assets,
        "githubEvidenceBranch": github.evidence_branch,
        "prCommentId": comment_id,
        "status": "published",
    }
    atomic_write_json(receipt_path, updated)
    return updated


def upsert_ui_run(description: str, run_id: str, run_section: str) -> str:
    section_name = settings().linear_ui_section
    heading = re.search(rf"(?im)^## {re.escape(section_name)}\s*$", description)
    if not heading:
        return f"{description.rstrip()}\n\n## {section_name}\n\n{run_section.rstrip()}\n"
    following = re.search(
        rf"(?m)^## (?!{re.escape(section_name)}\s*$).+$",
        description[heading.end() :],
    )
    end = heading.end() + (following.start() if following else len(description[heading.end() :]))
    body = description[heading.end() : end]
    run_pattern = re.compile(
        rf"(?ms)^### Issue Delivery {re.escape(run_id)}\s*$.*?(?=^### |\Z)"
    )
    if run_pattern.search(body):
        updated = run_pattern.sub(run_section.rstrip() + "\n", body)
    else:
        updated = body.rstrip() + "\n\n" + run_section.rstrip() + "\n"
    return description[: heading.end()] + updated + description[end:]
