from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .github import GitHubClient
from .linear import LinearClient
from .png_codec import png_size


def fingerprint(
    screenshots: list[dict[str, Any]],
    verification: dict[str, Any],
) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(verification, sort_keys=True).encode())
    for item in screenshots:
        digest.update(str(item.get("publishToGithub", True)).encode())
        digest.update(item["storyId"].encode())
        digest.update(item["title"].encode())
        digest.update(item["caption"].encode())
        digest.update(item["path"].read_bytes())
        digest.update(item["originalPath"].read_bytes())
        digest.update(item["displayPath"].read_bytes())
        digest.update(json.dumps(item["callouts"], sort_keys=True).encode())
    return digest.hexdigest()


def upload_linear_asset(
    screenshot: dict[str, Any],
    worktree: Path,
    linear: LinearClient,
) -> dict[str, Any]:
    display_url = linear.upload_file(screenshot["displayPath"])
    original_url = (
        linear.upload_file(screenshot["originalPath"])
        if screenshot["callouts"]
        else display_url
    )
    width, height = png_size(screenshot["displayPath"].read_bytes())
    return {
        "storyId": screenshot["storyId"],
        "width": width,
        "height": height,
        "publishToGithub": screenshot.get("publishToGithub", True),
        "title": screenshot["title"],
        "caption": screenshot["caption"],
        "callouts": screenshot["callouts"],
        "annotationReason": screenshot["annotationReason"],
        "path": str(screenshot["displayPath"].relative_to(worktree)),
        "capturePath": str(screenshot["path"].relative_to(worktree)),
        "originalPath": str(screenshot["originalPath"].relative_to(worktree)),
        "url": display_url,
        "originalUrl": original_url,
    }


def ensure_github_assets(
    state: dict[str, Any],
    assets: list[dict[str, Any]],
    screenshots: list[dict[str, Any]],
    github: GitHubClient,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    selected = [i for i, item in enumerate(screenshots) if item.get("publishToGithub", True)]
    if not selected:
        return assets
    published = _publish_github_assets(
        state, [assets[i] for i in selected], [screenshots[i] for i in selected],
        github, force=force,
    )
    result = list(assets)
    for index, asset in zip(selected, published):
        result[index] = asset
    return result


def _publish_github_assets(
    state: dict[str, Any],
    assets: list[dict[str, Any]],
    screenshots: list[dict[str, Any]],
    github: GitHubClient,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    complete = all(
        asset.get("githubUrl")
        and (not asset.get("callouts") or asset.get("githubOriginalUrl"))
        for asset in assets
    )
    if not force and assets and complete:
        return assets
    files: list[tuple[Path, str]] = []
    targets: list[tuple[str, str | None]] = []
    for index, screenshot in enumerate(screenshots, start=1):
        display_target = evidence_target_path(
            state,
            {**screenshot, "path": screenshot["displayPath"]},
            index,
            variant="annotated" if screenshot["callouts"] else None,
        )
        files.append((screenshot["displayPath"], display_target))
        original_target = None
        if screenshot["callouts"]:
            original_target = evidence_target_path(
                state,
                {**screenshot, "path": screenshot["originalPath"]},
                index,
                variant="original",
            )
            files.append((screenshot["originalPath"], original_target))
        targets.append((display_target, original_target))
    urls = github.publish_evidence_files(
        files,
        message=f"chore(evidence): publish UI evidence for {state['issue']['identifier']}",
    )
    return [
        _with_github_urls(asset, target, urls)
        for asset, target in zip(assets, targets)
    ]


def evidence_target_path(
    state: dict[str, Any],
    screenshot: dict[str, Any],
    index: int,
    *,
    variant: str | None = None,
) -> str:
    digest = hashlib.sha256(screenshot["path"].read_bytes()).hexdigest()[:12]
    story = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        str(screenshot.get("storyId") or f"image-{index}"),
    ).strip("-")
    suffix = f"-{variant}" if variant else ""
    return (
        f".issue-delivery-evidence/{state['issue']['identifier']}/"
        f"{state['runId']}/{index:02d}-{story}{suffix}-{digest}.png"
    )


def _with_github_urls(
    asset: dict[str, Any],
    targets: tuple[str, str | None],
    urls: dict[str, str],
) -> dict[str, Any]:
    display, original = targets
    return {
        **asset,
        "githubPath": display,
        "githubUrl": urls[display],
        "githubOriginalPath": original,
        "githubOriginalUrl": urls[original] if original else urls[display],
    }
