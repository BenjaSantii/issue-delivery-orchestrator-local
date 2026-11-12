import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from issue_delivery_orchestrator import prototype
from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.linear import extract_marker_block


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


class FakeLinear:
    def __init__(self):
        self.description = "Intro\n\n<!-- ido:spec:start -->\nSpec ID: SPEC-LOG-9\n<!-- ido:spec:end -->\n"
        self.uploads = []

    def issue(self, identifier):
        return SimpleNamespace(id="id-9", identifier="LOG-9", description=self.description)

    def upload_file(self, path):
        self.uploads.append(path.name)
        return f"https://uploads.linear.app/{path.name}"

    def update_description(self, issue_id, description):
        self.description = description


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))
        git(self.repo, "init", "-q")
        (self.repo / ".gitignore").write_text(".local-runtime/\n")
        git(self.repo, "add", ".gitignore")
        self.dir = prototype.prototype_dir(self.repo, "LOG-9", ".local-runtime", "issue-delivery-orchestrator")
        self.dir.mkdir(parents=True)
        (self.dir / "index.html").write_text("<!doctype html><title>LOG-9</title>")

    def test_serve_registers_a_hidden_paseo_service_and_returns_the_tunnel_url(self):
        calls = []

        def paseo(args):
            calls.append(args[1:3])
            if args[1:3] == ["workspace", "ls"]:
                return SimpleNamespace(stdout=json.dumps([{"workspaceId": "wks_1", "cwd": str(self.repo)}]),
                                       returncode=0)
            running = any(call == ["script", "start"] for call in calls)
            listing = [{"scriptName": "prototype", "lifecycle": "running" if running else "stopped",
                        "proxyUrl": "http://prototype--turboshop.localhost:6767"}]
            return SimpleNamespace(stdout=json.dumps(listing), returncode=0)

        with patch.dict(os.environ, {"PASEO_AGENT_ID": "agent-1", "ISSUE_DELIVERY_PREVIEW_PORT": "16767"}), \
                patch("issue_delivery_orchestrator.prototype.run", side_effect=lambda args, check=True, **kw: (
                    paseo(args) if args[0] == "paseo" else
                    subprocess.run(args, capture_output=True, text=True, check=check, **kw))):
            served = prototype.serve(self.repo, self.dir, "paseo")

        self.assertEqual(served["url"], "http://prototype--turboshop.localhost:16767/")
        self.assertIn(["script", "start"], calls)
        script = json.loads((self.repo / "paseo.json").read_text())["scripts"]["prototype"]
        self.assertEqual(script["type"], "service")
        self.assertIn(".local-runtime/issue-delivery-orchestrator/prototype/LOG-9", script["command"])
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "A  .gitignore")

    def test_serve_only_runs_inside_paseo(self):
        with patch.dict(os.environ, {"PASEO_AGENT_ID": ""}), self.assertRaisesRegex(RunBlocked, "Paseo"):
            prototype.serve(self.repo, self.dir, "paseo")

    def test_publish_anchors_the_approved_screens_html_and_hash_in_linear(self):
        linear = FakeLinear()
        with self.assertRaisesRegex(RunBlocked, "PNG"):
            prototype.publish(linear, "LOG-9", self.dir, "dale, apruebo", "ido")
        (self.dir / "antes.png").write_bytes(b"png-a")
        (self.dir / "despues.png").write_bytes(b"png-d")

        published = prototype.publish(linear, "LOG-9", self.dir, "dale, apruebo", "ido")

        block = extract_marker_block(linear.description, marker_prefix="ido", block="prototype")
        self.assertIn("Prototype ID: PROTO-LOG-9", block)
        self.assertIn(f"SHA-256: {prototype.digest(self.dir / 'index.html')}", block)
        self.assertIn("Approval: dale, apruebo", block)
        self.assertIn("[Descargar index.html](https://uploads.linear.app/index.html)", block)
        self.assertIn("![despues](https://uploads.linear.app/despues.png)", block)
        self.assertIn("Spec ID: SPEC-LOG-9", linear.description)
        self.assertEqual(sorted(linear.uploads), ["antes.png", "despues.png", "index.html"])
        self.assertEqual(published["prototypeId"], "PROTO-LOG-9")
        self.assertEqual(json.loads((self.dir / "approval.json").read_text())["sha256"], published["sha256"])


if __name__ == "__main__":
    unittest.main()
