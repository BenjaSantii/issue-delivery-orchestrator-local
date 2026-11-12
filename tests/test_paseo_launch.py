import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator import paseo_launch
from issue_delivery_orchestrator.errors import RunBlocked
from test_t3_launch import NO_SPEC, NO_TICKETS, readiness


def build(repo, ready, **options):
    return paseo_launch.build_paseo_launch(
        ready, repo, host="ssh://root@vps", remote_repository="/home/dev/TurboShop",
        base="test", stack_parent=None, provider=options.pop("provider", "claude-dev4"),
        model=None, **options)


class PaseoLaunchTests(unittest.TestCase):
    def setUp(self):
        raw = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(raw, ignore_errors=True))
        self.repo = Path(raw)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        for item in (patch.object(paseo_launch, "paseo_cli", return_value="paseo"),
                     patch.object(paseo_launch, "paseo_model",
                                  side_effect=lambda cli, provider, model, effort, host: (
                                      f"claude-{model}-5-5", effort or ""))):
            self.model = item.start()
            self.addCleanup(item.stop)

    def test_missing_spec_starts_a_dev4_agent_at_the_grill_in_a_new_worktree(self):
        launch = build(self.repo, readiness(NO_SPEC, NO_TICKETS))

        args = launch["arguments"]
        flag = lambda name: args[args.index(name) + 1]
        self.assertEqual(launch["startsAt"], "grill")
        self.assertEqual((flag("--host"), flag("--provider"), flag("--model")),
                         ("ssh://root@vps", "claude-dev4", "claude-opus-5-5"))
        self.assertEqual((flag("--new-workspace"), flag("--new-branch"), flag("--base"), flag("--cwd")),
                         ("worktree", "bs/log-9-talleres", "origin/test", "/home/dev/TurboShop"))
        self.assertIn("agente de Paseo", launch["message"])
        self.assertIn("--mode paseo --base test", launch["message"])
        self.assertIn("`qa-session`", launch["message"])
        self.assertNotIn("t3_thread_launch", launch["message"])
        self.assertIn("`issue-delivery-prototype` antes de los tickets", launch["message"])

    def test_approved_issue_goes_straight_to_delivery_after_the_worktree_setup(self):
        launch = build(self.repo, readiness(), effort="high", setup="bash /repo/.superset/setup.sh")
        self.assertEqual(launch["startsAt"], "implement")
        self.assertIn("prepara este worktree con `bash /repo/.superset/setup.sh`", launch["message"])
        self.assertNotIn("prepara este worktree", build(self.repo, readiness())["message"])
        self.assertIn("SPEC-LOG-9", launch["message"])
        self.assertEqual(launch["arguments"][launch["arguments"].index("--thinking") + 1], "high")

    def test_without_host_or_repository_the_launch_blocks(self):
        with self.assertRaisesRegex(RunBlocked, "ISSUE_DELIVERY_PASEO_HOST"):
            paseo_launch.build_paseo_launch(readiness(), self.repo, host="", remote_repository="",
                                            base="test", stack_parent=None,
                                            provider="claude-dev4", model=None)


if __name__ == "__main__":
    unittest.main()
