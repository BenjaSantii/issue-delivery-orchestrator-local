import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator import t3_launch
from issue_delivery_orchestrator.errors import RunBlocked

NO_SPEC, NO_TICKETS = t3_launch.NEEDS_GRILL


def readiness(*problems):
    return {"identifier": "LOG-9", "title": "Talleres por vendedor", "branch": "bs/log-9-talleres",
            "specId": "SPEC-LOG-9", "ticketIds": ["TICKET-001", "TICKET-002"],
            "problems": list(problems)}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class T3LaunchTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        git(self.repo, "init", "-q", "-b", "test")
        git(self.repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
            "--allow-empty", "-m", "base")
        real_run = t3_launch.run

        def no_network(args, **kwargs):
            if list(args)[:2] == ["git", "fetch"] or list(args)[1:3] == ["ls-remote", "--exit-code"]:
                return subprocess.CompletedProcess(args, 2, "", "")
            return real_run(args, **kwargs)

        for item in (patch.object(t3_launch, "run", side_effect=no_network),
                     patch.object(t3_launch, "t3_model", side_effect=lambda provider, model:
                                  {"opus": "claude-opus-5-5"}.get(model, model))):
            item.start()
            self.addCleanup(item.stop)

    def build(self, ready, **options):
        return t3_launch.build_t3_launch(ready, self.repo, base="test", stack_parent=None,
                                         provider="claude-dev4", model=None, **options)

    def test_missing_spec_or_tickets_start_at_the_grill_and_approved_ones_at_implement(self):
        grill = self.build(readiness(NO_SPEC, NO_TICKETS))
        delivery = self.build(readiness())

        self.assertEqual((grill["startsAt"], delivery["startsAt"]), ("grill", "implement"))
        self.assertIn("issue-delivery-grill", grill["message"])
        self.assertNotIn("issue-delivery-grill", delivery["message"])
        self.assertIn("SPEC-LOG-9 y los tickets TICKET-001, TICKET-002", delivery["message"])
        for launch in (grill, delivery):
            self.assertIn("--mode t3 --base test", launch["message"])
            self.assertIn("t3_thread_launch", launch["message"])
            args = launch["arguments"]
            for flag, value in (("--worktree", "origin/test"), ("--branch", "bs/log-9-talleres"),
                                ("--provider", "claude-dev4"), ("--model", "claude-opus-5-5"),
                                ("--title", "[LOG-9] Talleres por vendedor")):
                self.assertEqual(args[args.index(flag) + 1], value)

    def test_other_problems_block_before_creating_anything(self):
        with self.assertRaisesRegex(RunBlocked, "is Done"):
            self.build(readiness(NO_SPEC, "LOG-9 is Done; there is nothing to deliver"))

    def test_an_existing_issue_branch_blocks_a_second_launch(self):
        git(self.repo, "branch", "bs/log-9-talleres")
        with self.assertRaisesRegex(RunBlocked, "already exists"):
            self.build(readiness())

    def test_qa_effort_needs_a_qa_model(self):
        with self.assertRaisesRegex(RunBlocked, "--qa-effort requires --qa-model"):
            self.build(readiness(), qa_effort="high")
        launch = self.build(readiness(), qa_model="sonnet", qa_effort="high")
        self.assertIn("--qa-model sonnet --qa-effort high", launch["message"])


if __name__ == "__main__":
    unittest.main()
