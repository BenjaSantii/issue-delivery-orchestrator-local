from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .annotations import (
    annotate_png,
    is_png,
    normalize_callouts,
    normalize_png,
    validate_screenshot,
)
from .errors import OrchestrationError
from .headless_assistance import validate_headless_assistance
from .state import review_method, run_mode
from .util import atomic_write_json, ensure_within, read_json, run


def prepare_evidence(state: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    prepared = _prepare_evidence(state, manifest_path)
    return {
        "manifest": str(prepared["manifest_path"].relative_to(prepared["worktree"])),
        "verification": prepared["verification"],
        "headlessAssistance": prepared["headless_assistance"],
        "uploadAssistance": prepared["legacy_upload_assistance"],
        "screenshots": [
            {
                "storyId": item["storyId"],
                "capturePath": str(item["path"].relative_to(prepared["worktree"])),
                "originalPath": str(
                    item["originalPath"].relative_to(prepared["worktree"])
                ),
                "displayPath": str(item["displayPath"].relative_to(prepared["worktree"])),
                "calloutCount": len(item["callouts"]),
                "annotationReason": item["annotationReason"],
            }
            for item in prepared["screenshots"]
        ],
    }


def _prepare_evidence(
    state: dict[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    worktree = Path(state["worktree"]).resolve()
    manifest_path = ensure_within(manifest_path, worktree)
    manifest = read_json(manifest_path)
    verification = _verification(manifest, state, worktree)
    screenshots = _screenshots(manifest, worktree)
    screenshot_paths_by_story: dict[str, set[Path]] = {}
    for screenshot in screenshots:
        screenshot_paths_by_story.setdefault(screenshot["storyId"], set()).add(
            screenshot["path"].resolve()
        )
    headless_assistance = validate_headless_assistance(
        manifest,
        state,
        worktree,
        verification,
        screenshot_paths_by_story,
    )
    legacy_upload_assistance = [
        item for item in headless_assistance if item.get("legacy")
    ]
    if headless_assistance:
        verification["headlessAssistance"] = headless_assistance
    if legacy_upload_assistance:
        verification["uploadAssistance"] = legacy_upload_assistance
    changed = False
    for raw, screenshot in zip(manifest["screenshots"], screenshots):
        if is_png(screenshot["path"]):
            validate_screenshot(screenshot["path"])
            screenshot["originalPath"] = screenshot["path"]
        else:
            original = screenshot["path"].with_name(
                f"{screenshot['path'].stem}.original.png"
            )
            original = ensure_within(original, worktree)
            normalize_png(screenshot["path"], original)
            screenshot["originalPath"] = original
        if screenshot["callouts"]:
            target = screenshot["path"].with_name(
                f"{screenshot['path'].stem}.annotated.png"
            )
            target = ensure_within(target, worktree)
            annotate_png(screenshot["originalPath"], target, screenshot["callouts"])
            screenshot["displayPath"] = target
            annotated_path = str(target.relative_to(worktree))
            if raw.get("annotatedPath") != annotated_path:
                raw["annotatedPath"] = annotated_path
                changed = True
        else:
            screenshot["displayPath"] = screenshot["originalPath"]
            if "annotatedPath" in raw:
                raw.pop("annotatedPath")
                changed = True
    if changed:
        atomic_write_json(manifest_path, manifest)
    return {
        "worktree": worktree,
        "manifest_path": manifest_path,
        "verification": verification,
        "headless_assistance": headless_assistance,
        "legacy_upload_assistance": legacy_upload_assistance,
        "screenshots": screenshots,
    }


def _screenshots(manifest: Any, worktree: Path) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("screenshots"), list):
        raise OrchestrationError("Evidence manifest must contain a screenshots array")
    version = manifest.get("evidenceVersion", 1)
    if version not in {1, 2}:
        raise OrchestrationError(f"Unsupported evidenceVersion {version}")
    result = []
    for index, item in enumerate(manifest["screenshots"], start=1):
        if not isinstance(item, dict):
            raise OrchestrationError(f"Screenshot {index} must be an object")
        path = ensure_within(worktree / str(item.get("path") or ""), worktree)
        if not path.is_file() or path.suffix.lower() != ".png":
            raise OrchestrationError(f"Screenshot {index} is not a PNG file: {path}")
        story_id = str(item.get("storyId") or "").strip()
        if not story_id:
            raise OrchestrationError(f"Screenshot {index} is missing storyId")
        publish_to_github = item.get("publishToGithub", True)
        if not isinstance(publish_to_github, bool):
            raise OrchestrationError(f"Screenshot {index} publishToGithub must be boolean")
        callouts = normalize_callouts(item.get("callouts"), index)
        annotation_reason = str(item.get("annotationReason") or "").strip()
        if callouts and annotation_reason:
            raise OrchestrationError(
                f"Screenshot {index} cannot contain callouts and annotationReason"
            )
        caption = str(item.get("caption") or "").strip()
        if version == 2 and not callouts and not annotation_reason and not caption:
            raise OrchestrationError(
                f"Screenshot {index} must contain a caption, callouts or annotationReason"
            )
        result.append(
            {
                "storyId": story_id,
                "publishToGithub": publish_to_github,
                "title": str(item.get("title") or path.stem).strip(),
                "caption": caption,
                "path": path,
                "originalPath": path,
                "displayPath": path,
                "callouts": callouts,
                "annotationReason": annotation_reason,
            }
        )
    return result


def _verification(
    manifest: Any,
    state: dict[str, Any],
    worktree: Path,
) -> dict[str, Any]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("verification"), dict):
        raise OrchestrationError("Evidence manifest is missing the UI verification receipt")
    verification = manifest["verification"]
    if verification.get("status") != "PASS":
        raise OrchestrationError("Evidence publication requires UI verification status PASS")
    selected_provider = review_method(state)
    declared_provider = str(verification.get("provider") or "").strip()
    if run_mode(state) == "conductor" and (declared_provider == "adb" or verification.get("nativeVerified") is True):
        raise OrchestrationError("Conductor requires web simulation; physical-device/ADB evidence is not used")
    if declared_provider and declared_provider not in {"cua-driver", "codex-browser", "playwright", "adb"}:
        raise OrchestrationError(
            f"Evidence verification has unsupported provider {declared_provider}"
        )
    if declared_provider and declared_provider != "adb" and declared_provider != selected_provider:
        raise OrchestrationError(
            f"Evidence provider mismatch: run uses {selected_provider}, "
            f"manifest uses {declared_provider}"
        )
    if not declared_provider and selected_provider != "cua-driver":
        raise OrchestrationError(
            f"{selected_provider} evidence must declare provider '{selected_provider}'"
        )
    native_review = None
    if declared_provider == "adb":
        native_review = verification.get("nativeReview")
        if not isinstance(native_review, dict):
            raise OrchestrationError("ADB evidence requires nativeReview metadata")
        required = ("userInstruction", "device", "build")
        if any(not isinstance(native_review.get(key), str) or not native_review[key].strip()
               for key in required):
            raise OrchestrationError(
                "ADB evidence requires userInstruction, device and build"
            )
        if native_review.get("transport") not in {"wireless", "usb"}:
            raise OrchestrationError("ADB evidence requires wireless or usb transport")
        native_review = {key: native_review[key].strip() for key in (*required, "transport")}
    verified_commit = str(verification.get("verifiedCommit") or "").strip()
    head = run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    if verified_commit != head:
        raise OrchestrationError(
            f"UI verification is stale: verified {verified_commit or '<empty>'}, "
            f"current HEAD is {head}"
        )
    runtime_id = str(verification.get("runtimeId") or "").strip()
    registered = {item["runtimeId"] for item in state.get("runtimes", [])}
    if runtime_id not in registered:
        raise OrchestrationError(
            f"UI verification references unregistered runtime {runtime_id or '<empty>'}"
        )
    verified_at = str(verification.get("verifiedAt") or "").strip()
    try:
        datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise OrchestrationError(
            "UI verification verifiedAt must be an ISO-8601 timestamp"
        ) from error
    scenario_ids = verification.get("scenarioIds")
    if not isinstance(scenario_ids, list) or not any(str(item).strip() for item in scenario_ids):
        raise OrchestrationError("UI verification must identify at least one scenario")
    receipt = {
        "status": "PASS",
        "verifiedCommit": verified_commit,
        "runtimeId": runtime_id,
        "verifiedAt": verified_at,
        "scenarioIds": [str(item).strip() for item in scenario_ids if str(item).strip()],
    }
    if declared_provider or state.get("reviewer"):
        receipt["provider"] = declared_provider or selected_provider
    if native_review is not None:
        receipt["nativeReview"] = native_review
    if run_mode(state) == "conductor":
        receipt.update({"validationScope": "web-simulation", "nativeVerified": False})
    return receipt
