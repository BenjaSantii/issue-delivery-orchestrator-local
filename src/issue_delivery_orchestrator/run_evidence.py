"""Build the UI evidence manifest from the run's validation folder and notes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import OrchestrationError
from .state import now, review_method, run_root
from .util import atomic_write_json, read_json, run

_IMAGE = re.compile(r"^(?P<id>[A-Za-z]+-\d+)(?:-(?P<order>\d+))?(?:-(?P<slug>.+))?$")
_NOTE = re.compile(r"^\s*(?:[-*]\s+)?(?P<key>[A-Za-z]+-\d+[^\s:]*)\s*:\s*(?P<text>.+?)\s*$")
_DERIVED = (".annotated", ".original")


def validation_dir(state: dict[str, Any]) -> Path:
    return run_root(Path(state["worktree"]), state["runId"]) / "validation"


def read_notes(state: dict[str, Any]) -> dict[str, str]:
    path = validation_dir(state) / "notes.md"
    if not path.is_file():
        return {}
    notes = {}
    for line in path.read_text().splitlines():
        match = _NOTE.match(line)
        if match:
            notes[match["key"]] = match["text"]
    return notes


def story_images(state: dict[str, Any], story_id: str | None = None) -> list[Path]:
    folder = validation_dir(state) / "ui"
    if not folder.is_dir():
        return []
    images = []
    for path in folder.glob("*.png"):
        match = _IMAGE.match(path.stem)
        if not match or path.stem.endswith(_DERIVED):
            continue
        if story_id is None or match["id"] == story_id:
            images.append(path)
    return sorted(images, key=_image_order)


def ui_manifest(state: dict[str, Any], runtime_id: str | None = None) -> Path | None:
    """Return the run's UI manifest, generating it from validation/ui when none was written by hand."""
    path = validation_dir(state) / "ui-manifest.json"
    previous = read_json(path, {})
    if path.is_file() and not previous.get("generated"):
        return path
    images = story_images(state)
    if not images:
        return None
    worktree = Path(state["worktree"]).resolve()
    notes = read_notes(state)
    callouts = read_json(validation_dir(state) / "callouts.json", {})
    screenshots = []
    captioned: set[str] = set()
    for image in images:
        match = _IMAGE.match(image.stem)
        story_id = match["id"]
        caption = notes.get(image.stem)
        if caption is None and story_id not in captioned:
            caption = notes.get(story_id)
        captioned.add(story_id)
        slug = (match["slug"] or "").replace("-", " ").replace("_", " ").capitalize()
        if not caption and not slug and not callouts.get(image.stem):
            raise OrchestrationError(
                f"{image.name} needs a line `{image.stem}: <qué muestra>` in validation/notes.md "
                "or a descriptive file name"
            )
        item = {
            "storyId": story_id,
            "title": slug or story_id,
            "caption": caption or slug,
            "path": str(image.resolve().relative_to(worktree)),
        }
        if callouts.get(image.stem):
            item["callouts"] = callouts[image.stem]
        screenshots.append(item)
    verification = {
        "status": "PASS",
        "provider": review_method(state),
        "verifiedCommit": run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip(),
        "runtimeId": runtime_id or _single_runtime(state),
        "scenarioIds": list(dict.fromkeys(item["storyId"] for item in screenshots)),
    }
    earlier = previous.get("verification") or {}
    same_run = all(earlier.get(key) == value for key, value in verification.items())
    verification["verifiedAt"] = earlier["verifiedAt"] if same_run else now()
    manifest = {
        "evidenceVersion": 2,
        "generated": True,
        "verification": verification,
        "screenshots": screenshots,
    }
    atomic_write_json(path, manifest)
    return path


def _single_runtime(state: dict[str, Any]) -> str:
    runtimes = [item["runtimeId"] for item in state.get("runtimes", [])]
    if len(runtimes) != 1:
        raise OrchestrationError(
            f"Pass --runtime: the run has {len(runtimes)} registered runtimes"
        )
    return runtimes[0]


def _image_order(path: Path) -> tuple[str, int, int, str]:
    match = _IMAGE.match(path.stem)
    prefix, number = match["id"].rsplit("-", 1)
    return (prefix, int(number), int(match["order"] or 0), path.stem)
