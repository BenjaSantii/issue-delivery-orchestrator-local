from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from issue_delivery_orchestrator.errors import OrchestrationError
from issue_delivery_orchestrator.evidence import (
    _evidence_target_path,
    _pr_body,
    _verification,
    prepare_evidence,
    publish_evidence,
)
from issue_delivery_orchestrator.png_codec import PngImage, encode_png
from issue_delivery_orchestrator.state import run_root
from issue_delivery_orchestrator.util import read_json


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class EvidenceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.worktree = Path(self.temporary.name)
        git(self.worktree, "init", "-b", "test")
        git(self.worktree, "config", "user.email", "test@example.com")
        git(self.worktree, "config", "user.name", "Test")
        (self.worktree / "file.txt").write_text("content\n")
        git(self.worktree, "add", "file.txt")
        git(self.worktree, "commit", "-m", "initial")
        self.head = git(self.worktree, "rev-parse", "HEAD")
        self.state = {"runtimes": [{"runtimeId": "rt-1"}]}

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(
        self,
        commit: str | None = None,
        *,
        provider: str | None = None,
    ):
        verification = {
            "status": "PASS",
            "verifiedCommit": commit or self.head,
            "runtimeId": "rt-1",
            "verifiedAt": "2026-07-27T12:00:00Z",
            "scenarioIds": ["REPAIR-1"],
        }
        if provider:
            verification["provider"] = provider
        return {
            "verification": {
                **verification,
            }
        }

    def test_accepts_pass_for_current_head_and_registered_runtime(self):
        receipt = _verification(self.manifest(), self.state, self.worktree)
        self.assertEqual(receipt["verifiedCommit"], self.head)
        self.assertNotIn("provider", receipt)

    def test_accepts_browser_pass_for_browser_run(self):
        self.state["mode"] = {"name": "codex"}
        self.state["reviewer"] = {"method": "codex-browser"}

        receipt = _verification(
            self.manifest(provider="codex-browser"),
            self.state,
            self.worktree,
        )

        self.assertEqual(receipt["provider"], "codex-browser")

    def test_accepts_playwright_pass_for_conductor_run(self):
        self.state["mode"] = {"name": "conductor"}
        self.state["reviewer"] = {"method": "playwright"}

        receipt = _verification(
            self.manifest(provider="playwright"),
            self.state,
            self.worktree,
        )

        self.assertEqual(receipt["provider"], "playwright")
        self.assertEqual(receipt["validationScope"], "web-simulation")
        self.assertFalse(receipt["nativeVerified"])

    def test_conductor_uses_web_simulation_even_with_adb_authorization_metadata(self):
        self.state["mode"] = {"name": "conductor"}
        self.state["reviewer"] = {"method": "playwright"}
        with self.assertRaisesRegex(OrchestrationError, "requires web simulation"):
            _verification(self.adb_manifest(), self.state, self.worktree)

    def test_conductor_run_requires_declared_playwright_provider(self):
        self.state["mode"] = {"name": "conductor"}
        self.state["reviewer"] = {"method": "playwright"}

        with self.assertRaisesRegex(OrchestrationError, "must declare provider 'playwright'"):
            _verification(self.manifest(), self.state, self.worktree)

    def test_rejects_playwright_evidence_for_cua_run(self):
        self.state["mode"] = {"name": "vanilla"}
        self.state["reviewer"] = {"method": "cua-driver"}

        with self.assertRaisesRegex(OrchestrationError, "provider mismatch"):
            _verification(
                self.manifest(provider="playwright"),
                self.state,
                self.worktree,
            )

    def test_rejects_evidence_from_a_different_reviewer(self):
        self.state["mode"] = {"name": "codex"}
        self.state["reviewer"] = {"method": "codex-browser"}

        with self.assertRaisesRegex(OrchestrationError, "provider mismatch"):
            _verification(
                self.manifest(provider="cua-driver"),
                self.state,
                self.worktree,
            )

    def adb_manifest(self):
        manifest = self.manifest(provider="adb")
        manifest["verification"]["nativeReview"] = {
            "userInstruction": "Test on my Android tablet using ADB",
            "device": "Lenovo TB311XU",
            "build": "com.example.app development build",
            "transport": "wireless",
        }
        return manifest

    def test_adb_receipt_preserves_native_method_without_changing_web_reviewer(self):
        for reviewer in ("cua-driver", "codex-browser"):
            self.state["reviewer"] = {"method": reviewer}
            receipt = _verification(self.adb_manifest(), self.state, self.worktree)
            self.assertEqual(receipt["provider"], "adb")
            self.assertEqual(receipt["nativeReview"]["device"], "Lenovo TB311XU")
            self.assertEqual(self.state["reviewer"]["method"], reviewer)
        body = _pr_body("<!-- marker -->", [], provider="adb")
        self.assertIn("ADB en dispositivo Android", body)
        self.assertNotIn("Cua Driver", body)

    def test_adb_requires_authorization_and_device_identity(self):
        for field in ("userInstruction", "device", "build", "transport"):
            with self.subTest(field=field):
                manifest = self.adb_manifest()
                del manifest["verification"]["nativeReview"][field]
                with self.assertRaises(OrchestrationError):
                    _verification(manifest, self.state, self.worktree)

    def test_adb_rejects_browser_headless_assistance(self):
        from issue_delivery_orchestrator.headless_assistance import validate_headless_assistance

        manifest = self.adb_manifest()
        manifest["headlessAssistance"] = [{"kind": "hover"}]
        receipt = _verification(manifest, self.state, self.worktree)
        with self.assertRaisesRegex(OrchestrationError, "unsupported for reviewer adb"):
            validate_headless_assistance(manifest, self.state, self.worktree, receipt, {})

    def test_adb_keeps_existing_verification_gates(self):
        for field, value in (("verifiedCommit", "stale"), ("runtimeId", "unknown"),
                             ("status", "FAIL"), ("scenarioIds", []), ("verifiedAt", "bad")):
            with self.subTest(field=field):
                manifest = self.adb_manifest()
                manifest["verification"][field] = value
                with self.assertRaises(OrchestrationError):
                    _verification(manifest, self.state, self.worktree)

    def test_rejects_stale_pass_after_code_changes(self):
        stale = self.head
        (self.worktree / "file.txt").write_text("changed\n")
        git(self.worktree, "add", "file.txt")
        git(self.worktree, "commit", "-m", "repair after verification")
        with self.assertRaisesRegex(OrchestrationError, "stale"):
            _verification(self.manifest(stale), self.state, self.worktree)

    def test_rejects_unregistered_runtime(self):
        manifest = self.manifest()
        manifest["verification"]["runtimeId"] = "rt-other"
        with self.assertRaisesRegex(OrchestrationError, "unregistered"):
            _verification(manifest, self.state, self.worktree)

    def test_pr_body_uses_github_path_instead_of_private_linear_url(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {
                    "storyId": "REPAIR-1",
                    "title": "Fixed state",
                    "caption": "Verified",
                    "url": "https://uploads.linear.app/private/image",
                    "githubUrl": "../blob/codex-ui-evidence/path/image.png?raw=true",
                }
            ],
        )
        self.assertIn("../blob/codex-ui-evidence/path/image.png?raw=true", body)
        self.assertNotIn("uploads.linear.app", body)

    def test_pr_body_shows_one_image_per_story_and_collapses_the_rest(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {"storyId": "US-1", "title": "Main", "githubUrl": "../main.png"},
                {"storyId": "US-1", "title": "Detail", "githubUrl": "../detail.png",
                 "width": 1170, "height": 2532},
                {"storyId": "US-2", "title": "Other", "githubUrl": "../other.png",
                 "width": 1440, "height": 1000},
            ],
            linear_url="https://linear.app/ts/issue/TS-1",
        )

        self.assertIn("### US-1 — Main\n\n![Main](../main.png)", body)
        self.assertNotIn("### US-1 — Detail", body)
        self.assertIn("<summary>Más capturas de US-1 (1)</summary>", body)
        self.assertLess(body.index("<details>"), body.index("../detail.png"))
        self.assertIn('<img src="../detail.png" alt="Detail" width="300">', body)
        self.assertIn('<img src="../other.png" alt="Other" width="720">', body)
        self.assertIn("### US-2 — Other", body)
        self.assertIn("[Linear](https://linear.app/ts/issue/TS-1)", body)

    def test_linear_only_assets_are_not_uploaded_or_rendered_in_github(self):
        from issue_delivery_orchestrator.evidence_assets import ensure_github_assets
        from unittest.mock import Mock

        github = Mock()
        github.publish_evidence_files.return_value = {"x": "unused"}
        hidden = {"title": "Linear only", "publishToGithub": False}
        visible = {"title": "GitHub", "githubUrl": "../visible.png", "storyId": "US-1"}
        assets = ensure_github_assets({}, [hidden, visible], [hidden, visible], github)
        github.publish_evidence_files.assert_not_called()
        self.assertEqual(assets, [hidden, visible])
        body = _pr_body("marker", assets)
        self.assertIn("../visible.png", body)
        self.assertNotIn("Linear only", body)

    def test_pr_body_names_browser_provider(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {
                    "storyId": "US-1",
                    "title": "Browser state",
                    "caption": "Verified",
                    "githubUrl": "../blob/codex-ui-evidence/path/image.png?raw=true",
                }
            ],
            provider="codex-browser",
        )

        self.assertIn("Browser integrado de Codex", body)

    def test_pr_body_names_playwright_provider(self):
        body = _pr_body("<!-- marker -->", [], provider="playwright")

        self.assertIn("Playwright (Chromium headless en Conductor Cloud)", body)
        self.assertNotIn("Cua Driver", body)

    def test_pr_body_discloses_headless_upload_assistance(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {
                    "storyId": "US-2",
                    "title": "Uploaded state",
                    "caption": "Verified",
                    "githubUrl": "../upload.png",
                }
            ],
            provider="codex-browser",
            upload_assistance=[
                {
                    "storyId": "US-2",
                    "scope": "upload-only",
                    "driver": "playwright-headless",
                }
            ],
        )

        self.assertIn(
            "Playwright headless limitado a uploads en US-2",
            body,
        )

    def test_pr_body_discloses_headless_hover_assistance(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {
                    "storyId": "US-1",
                    "title": "Hover state",
                    "caption": "Verified",
                    "githubUrl": "../hover.png",
                }
            ],
            provider="codex-browser",
            headless_assistance=[
                {
                    "storyId": "US-1",
                    "kind": "hover",
                    "scope": "full-story",
                    "driver": "playwright-headless",
                }
            ],
        )

        self.assertIn(
            "Playwright headless limitado a hover en US-1",
            body,
        )

    def test_accepts_current_headless_upload_receipt_for_browser_run(self):
        screenshot = self.worktree / "upload.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "codex"},
            "reviewer": {"method": "codex-browser"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        fixture = root / "validation" / "headless-upload" / "US-2" / "photo.png"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b"fixture")
        receipt = fixture.parent / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 1,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-2",
                    "scope": "upload-only",
                    "verifiedCommit": self.head,
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:00:00Z",
                    "files": [
                        {
                            "path": str(fixture.relative_to(root)),
                            "mimeType": "image/png",
                            "size": fixture.stat().st_size,
                            "sha256": hashlib.sha256(
                                fixture.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": [
                        "The four-file upload request completed successfully."
                    ],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="codex-browser")
        manifest["verification"]["scenarioIds"] = ["US-2"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "uploadAssistance": [
                    {
                        "storyId": "US-2",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-2",
                        "path": "upload.png",
                        "annotationReason": "The complete gallery is the accepted result.",
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        prepared = prepare_evidence(state, manifest_path)

        self.assertEqual(prepared["uploadAssistance"][0]["storyId"], "US-2")
        self.assertEqual(
            prepared["verification"]["uploadAssistance"][0]["scope"],
            "upload-only",
        )
        self.assertEqual(
            prepared["headlessAssistance"][0]["kind"],
            "file-upload",
        )

    def test_accepts_hover_fallback_after_browser_capability_gap(self):
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "codex"},
            "reviewer": {"method": "codex-browser"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        story_root = root / "validation" / "headless-assistance" / "US-1"
        story_root.mkdir(parents=True)
        screenshot = story_root / "hover.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        receipt = story_root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 2,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-1",
                    "kind": "hover",
                    "scope": "full-story",
                    "verifiedCommit": self.head,
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:01:00Z",
                    "browserAttempt": {
                        "status": "CAPABILITY_GAP",
                        "kind": "hover",
                        "attemptedAt": "2026-07-30T12:00:00Z",
                        "observation": (
                            "element.matches(':hover') remained false after "
                            "tab.cua.move."
                        ),
                    },
                    "artifacts": [
                        {
                            "path": str(screenshot.relative_to(root)),
                            "role": "evidence",
                            "mimeType": "image/png",
                            "size": screenshot.stat().st_size,
                            "sha256": hashlib.sha256(
                                screenshot.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": [
                        "The target changed on hover and selection stayed unchanged.",
                        "Moving the pointer away restored only the hover state.",
                    ],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="codex-browser")
        manifest["verification"]["scenarioIds"] = ["US-1"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "headlessAssistance": [
                    {
                        "storyId": "US-1",
                        "kind": "hover",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-1",
                        "path": str(screenshot.relative_to(self.worktree)),
                        "annotationReason": (
                            "The full control state demonstrates the accepted hover."
                        ),
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        prepared = prepare_evidence(state, manifest_path)

        assistance = prepared["verification"]["headlessAssistance"][0]
        self.assertEqual(assistance["kind"], "hover")
        self.assertEqual(assistance["scope"], "full-story")
        self.assertEqual(
            assistance["browserAttempt"]["status"],
            "CAPABILITY_GAP",
        )
        self.assertEqual(prepared["uploadAssistance"], [])

    def test_accepts_current_file_upload_fallback_with_browser_final_review(self):
        screenshot = self.worktree / "uploaded-state.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "codex"},
            "reviewer": {"method": "codex-browser"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        story_root = root / "validation" / "headless-assistance" / "US-2"
        fixture = story_root / "fixtures" / "photo.png"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b"fixture")
        receipt = story_root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 2,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-2",
                    "kind": "file-upload",
                    "scope": "operation-only",
                    "verifiedCommit": self.head,
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:01:00Z",
                    "browserAttempt": {
                        "status": "CAPABILITY_GAP",
                        "kind": "file-upload",
                        "attemptedAt": "2026-07-30T12:00:00Z",
                        "observation": "Browser could not operate the real file chooser.",
                    },
                    "artifacts": [
                        {
                            "path": str(fixture.relative_to(root)),
                            "role": "fixture",
                            "mimeType": "image/png",
                            "size": fixture.stat().st_size,
                            "sha256": hashlib.sha256(
                                fixture.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": [
                        "Playwright uploaded the fixture through the real file input.",
                        "Browser reopened and visually verified the persisted result.",
                    ],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="codex-browser")
        manifest["verification"]["scenarioIds"] = ["US-2"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "headlessAssistance": [
                    {
                        "storyId": "US-2",
                        "kind": "file-upload",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-2",
                        "path": str(screenshot.relative_to(self.worktree)),
                        "annotationReason": "The persisted gallery is the accepted result.",
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        prepared = prepare_evidence(state, manifest_path)

        assistance = prepared["headlessAssistance"][0]
        self.assertEqual(assistance["kind"], "file-upload")
        self.assertEqual(assistance["scope"], "operation-only")
        self.assertNotIn("legacy", assistance)

    def test_accepts_vanilla_cua_hover_fallback_with_v3_receipt(self):
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "vanilla"},
            "reviewer": {"method": "cua-driver"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        story_root = root / "validation" / "headless-assistance" / "US-3"
        story_root.mkdir(parents=True)
        screenshot = story_root / "hover.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        receipt = story_root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 3,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-3",
                    "kind": "hover",
                    "scope": "full-story",
                    "verifiedCommit": self.head,
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:01:00Z",
                    "primaryAttempt": {
                        "status": "CAPABILITY_GAP",
                        "provider": "cua-driver",
                        "kind": "hover",
                        "attemptedAt": "2026-07-30T12:00:00Z",
                        "observation": (
                            "Cua reached the control but could not keep the "
                            "required hover state active."
                        ),
                    },
                    "artifacts": [
                        {
                            "path": str(screenshot.relative_to(root)),
                            "role": "evidence",
                            "mimeType": "image/png",
                            "size": screenshot.stat().st_size,
                            "sha256": hashlib.sha256(
                                screenshot.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": [
                        "Playwright displayed the expected hover state.",
                        "Moving away preserved the selected state.",
                    ],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="cua-driver")
        manifest["verification"]["scenarioIds"] = ["US-3"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "headlessAssistance": [
                    {
                        "storyId": "US-3",
                        "kind": "hover",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-3",
                        "path": str(screenshot.relative_to(self.worktree)),
                        "annotationReason": "The control state demonstrates hover.",
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        prepared = prepare_evidence(state, manifest_path)

        assistance = prepared["headlessAssistance"][0]
        self.assertEqual(assistance["receiptVersion"], 3)
        self.assertEqual(
            assistance["primaryAttempt"]["provider"],
            "cua-driver",
        )

    def test_rejects_hover_fallback_without_browser_attempt(self):
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "codex"},
            "reviewer": {"method": "codex-browser"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        story_root = root / "validation" / "headless-assistance" / "US-1"
        story_root.mkdir(parents=True)
        screenshot = story_root / "hover.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        receipt = story_root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 2,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-1",
                    "kind": "hover",
                    "scope": "full-story",
                    "verifiedCommit": self.head,
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:01:00Z",
                    "artifacts": [
                        {
                            "path": str(screenshot.relative_to(root)),
                            "role": "evidence",
                            "mimeType": "image/png",
                            "size": screenshot.stat().st_size,
                            "sha256": hashlib.sha256(
                                screenshot.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": ["The hover state was visible."],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="codex-browser")
        manifest["verification"]["scenarioIds"] = ["US-1"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "headlessAssistance": [
                    {
                        "storyId": "US-1",
                        "kind": "hover",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-1",
                        "path": str(screenshot.relative_to(self.worktree)),
                        "annotationReason": "The hover state spans the whole control.",
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        with self.assertRaisesRegex(OrchestrationError, "browserAttempt"):
            prepare_evidence(state, manifest_path)


    def test_rejects_stale_headless_upload_receipt(self):
        screenshot = self.worktree / "upload.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "mode": {"name": "codex"},
            "reviewer": {"method": "codex-browser"},
            "runtimes": [{"runtimeId": "rt-1"}],
        }
        root = run_root(self.worktree, "run-1")
        fixture = root / "validation" / "headless-upload" / "US-2" / "photo.png"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b"fixture")
        receipt = fixture.parent / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "receiptVersion": 1,
                    "status": "PASS",
                    "driver": "playwright-headless",
                    "storyId": "US-2",
                    "scope": "full-story",
                    "verifiedCommit": "stale-commit",
                    "runtimeId": "rt-1",
                    "verifiedAt": "2026-07-30T12:00:00Z",
                    "files": [
                        {
                            "path": str(fixture.relative_to(root)),
                            "mimeType": "image/png",
                            "size": fixture.stat().st_size,
                            "sha256": hashlib.sha256(
                                fixture.read_bytes()
                            ).hexdigest(),
                        }
                    ],
                    "observations": ["The story reached its accepted final state."],
                }
            )
        )
        manifest_path = self.worktree / "manifest.json"
        manifest = self.manifest(provider="codex-browser")
        manifest["verification"]["scenarioIds"] = ["US-2"]
        manifest.update(
            {
                "evidenceVersion": 2,
                "uploadAssistance": [
                    {
                        "storyId": "US-2",
                        "receiptPath": str(receipt.relative_to(self.worktree)),
                    }
                ],
                "screenshots": [
                    {
                        "storyId": "US-2",
                        "path": "upload.png",
                        "annotationReason": "The complete gallery is the accepted result.",
                    }
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest))

        with self.assertRaisesRegex(OrchestrationError, "verifiedCommit"):
            prepare_evidence(state, manifest_path)

    def test_evidence_target_is_content_addressed(self):
        screenshot = {
            "storyId": "REPAIR 1",
            "path": self.worktree / "file.txt",
        }
        state = {"issue": {"identifier": "SFW-1"}, "runId": "run-1"}
        target = _evidence_target_path(state, screenshot, 1)
        self.assertRegex(
            target,
            r"^\.issue-delivery-evidence/SFW-1/run-1/01-REPAIR-1-[a-f0-9]{12}\.png$",
        )

    def test_prepares_annotated_evidence_and_preserves_original(self):
        screenshot = self.worktree / "screen.png"
        original = encode_png(
            PngImage(100, 60, bytearray((245, 245, 245, 255) * 100 * 60))
        )
        screenshot.write_bytes(original)
        manifest_path = self.worktree / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "evidenceVersion": 2,
                    **self.manifest(),
                    "screenshots": [
                        {
                            "storyId": "US-1",
                            "title": "Final state",
                            "caption": "Verified",
                            "path": "screen.png",
                            "callouts": [
                                {
                                    "kind": "highlight",
                                    "caption": "Nuevo selector",
                                    "bounds": {
                                        "x": 0.2,
                                        "y": 0.2,
                                        "width": 0.4,
                                        "height": 0.4,
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        )
        state = {
            "worktree": str(self.worktree),
            "runtimes": [{"runtimeId": "rt-1"}],
        }

        prepared = prepare_evidence(state, manifest_path)

        annotated = self.worktree / prepared["screenshots"][0]["displayPath"]
        self.assertTrue(annotated.is_file())
        self.assertNotEqual(annotated.read_bytes(), original)
        self.assertEqual(screenshot.read_bytes(), original)
        self.assertEqual(
            read_json(manifest_path)["screenshots"][0]["annotatedPath"],
            "screen.annotated.png",
        )

    def test_evidence_v2_requires_callouts_or_reason(self):
        screenshot = self.worktree / "screen.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        manifest_path = self.worktree / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "evidenceVersion": 2,
                    **self.manifest(),
                    "screenshots": [
                        {
                            "storyId": "US-1",
                            "path": "screen.png",
                        }
                    ],
                }
            )
        )
        state = {
            "worktree": str(self.worktree),
            "runtimes": [{"runtimeId": "rt-1"}],
        }

        with self.assertRaisesRegex(OrchestrationError, "callouts or annotationReason"):
            prepare_evidence(state, manifest_path)

    def test_evidence_v2_accepts_global_annotation_reason(self):
        screenshot = self.worktree / "screen.png"
        screenshot.write_bytes(
            encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        )
        manifest_path = self.worktree / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "evidenceVersion": 2,
                    **self.manifest(),
                    "screenshots": [
                        {
                            "storyId": "US-1",
                            "path": "screen.png",
                            "annotationReason": "El cambio afecta la composición completa.",
                        }
                    ],
                }
            )
        )
        state = {
            "worktree": str(self.worktree),
            "runtimes": [{"runtimeId": "rt-1"}],
        }

        prepared = prepare_evidence(state, manifest_path)

        self.assertEqual(prepared["screenshots"][0]["calloutCount"], 0)
        self.assertEqual(
            prepared["screenshots"][0]["displayPath"],
            "screen.png",
        )

    def test_pr_body_explains_callouts_and_links_original(self):
        body = _pr_body(
            "<!-- marker -->",
            [
                {
                    "storyId": "US-1",
                    "title": "Annotated state",
                    "caption": "Verified",
                    "callouts": [
                        {
                            "caption": "Nuevo selector",
                        }
                    ],
                    "githubUrl": "../annotated.png",
                    "githubOriginalUrl": "../original.png",
                }
            ],
        )

        self.assertIn("1. Nuevo selector", body)
        self.assertIn("[Ver captura original sin anotaciones](../original.png)", body)
        self.assertIn("no forman parte de la aplicación", body)

    def test_publication_uploads_annotated_and_original_pngs(self):
        self.assert_publication(self.manifest())

    def test_native_publication_preserves_adb_metadata_and_originals(self):
        receipt = self.assert_publication(self.adb_manifest())
        self.assertEqual(receipt["verification"]["provider"], "adb")
        self.assertEqual(receipt["verification"]["nativeReview"]["transport"], "wireless")

    def test_publishes_validation_folder_without_handwritten_manifest(self):
        from issue_delivery_orchestrator.run_evidence import ui_manifest

        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "issue": {"identifier": "US-1"},
            "runtimes": [{"runtimeId": "rt-1"}],
            "artifacts": {},
            "pr": None,
        }
        folder = run_root(self.worktree, "run-1") / "validation"
        (folder / "ui").mkdir(parents=True)
        png = encode_png(PngImage(10, 10, bytearray((255, 255, 255, 255) * 100)))
        for name in ("US-10.png", "US-2-02-detalle.png", "US-2-01-lista.png", "US-2-01-lista.annotated.png", "US-3-evaluación.png"):
            (folder / "ui" / name).write_bytes(png)
        (folder / "notes.md").write_text(
            "- US-2: El filtro deja solo pendientes\n- US-10: Exporta el CSV\n"
            "- US-3-evaluación: Siete estrellas\n"
        )

        manifest_path = ui_manifest(state)
        manifest = read_json(manifest_path)

        self.assertEqual(
            [(item["storyId"], item["title"], item["caption"]) for item in manifest["screenshots"]],
            [
                ("US-2", "Lista", "El filtro deja solo pendientes"),
                ("US-2", "Detalle", "Detalle"),
                ("US-3", "Evaluación", "Siete estrellas"),
                ("US-10", "US-10", "Exporta el CSV"),
            ],
        )
        self.assertEqual(manifest["verification"]["verifiedCommit"], self.head)
        self.assertEqual(manifest["verification"]["scenarioIds"], ["US-2", "US-3", "US-10"])
        first_run = manifest["verification"]["verifiedAt"]
        self.assertEqual(read_json(ui_manifest(state))["verification"]["verifiedAt"], first_run)

        receipt = self.publish(state, manifest_path)
        self.assertEqual(len(receipt["assets"]), 4)
        self.assertFalse(any(asset["callouts"] for asset in receipt["assets"]))

        manifest_path.write_text(json.dumps({"verification": {}, "screenshots": []}))
        self.assertEqual(read_json(ui_manifest(state)), {"verification": {}, "screenshots": []})

    def publish(self, state, manifest_path):
        linear = SimpleNamespace(
            upload_file=lambda path: f"https://linear.example/{Path(path).name}",
            issue=lambda _id: SimpleNamespace(id="linear-id", url="https://linear/US-1", description=""),
            update_description=lambda *_: None,
            post_comment=lambda *_: None,
        )
        return publish_evidence(state, manifest_path, linear=linear, github=None)

    def assert_publication(self, manifest):
        screenshot = self.worktree / "screen.png"
        screenshot.write_bytes(
            encode_png(PngImage(100, 60, bytearray((245, 245, 245, 255) * 6000)))
        )
        manifest_path = self.worktree / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "evidenceVersion": 2,
                    **manifest,
                    "screenshots": [
                        {
                            "storyId": "US-1",
                            "title": "Final state",
                            "caption": "Verified",
                            "path": "screen.png",
                            "callouts": [
                                {
                                    "kind": "circle",
                                    "caption": "Nuevo estado",
                                    "bounds": {
                                        "x": 0.2,
                                        "y": 0.2,
                                        "width": 0.4,
                                        "height": 0.4,
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        )

        class Linear:
            def __init__(self):
                self.uploads = []
                self.description = ""
                self.comments = 0

            def upload_file(self, path):
                self.uploads.append(Path(path))
                return f"https://linear.example/{Path(path).name}"

            def issue(self, _identifier):
                return SimpleNamespace(id="linear-id", url="https://linear/US-1", description="")

            def update_description(self, _issue_id, description):
                self.description = description

            def post_comment(self, _issue_id, _body):
                self.comments += 1

        linear = Linear()
        state = {
            "worktree": str(self.worktree),
            "runId": "run-1",
            "issue": {"identifier": "US-1"},
            "runtimes": [{"runtimeId": "rt-1"}],
            "artifacts": {},
            "pr": None,
        }

        receipt = publish_evidence(
            state,
            manifest_path,
            linear=linear,
            github=None,
            comments_enabled=False,
        )

        self.assertEqual(len(linear.uploads), 2)
        self.assertTrue(linear.uploads[0].name.endswith(".annotated.png"))
        self.assertEqual(linear.uploads[1], screenshot.resolve())
        self.assertEqual(receipt["assets"][0]["originalPath"], "screen.png")
        self.assertIn("Ver captura original sin anotaciones", linear.description)
        self.assertEqual(linear.comments, 0)
        return receipt


if __name__ == "__main__":
    unittest.main()
