import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator.cloud_launch import (
    _normalize_remote,
    build_launch,
    delivery_readiness,
    launch_message,
    stack_parent_for,
)
from issue_delivery_orchestrator.config import settings
from issue_delivery_orchestrator.errors import RunBlocked


PREFIX = "turboshop-dev-loop"
APPROVED = (
    "## Contexto\n\nTexto.\n\n"
    f"<!-- {PREFIX}:spec:start -->\nSpec ID: SPEC-TS-1\n\n# Spec\n<!-- {PREFIX}:spec:end -->\n\n"
    f"<!-- {PREFIX}:tickets:start -->\nTicket ID: TICKET-001\nStatus: Pending\n\n"
    f"Ticket ID: TICKET-002\nStatus: Pending\n<!-- {PREFIX}:tickets:end -->\n"
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


class FakeLinear:
    def __init__(self, issue):
        self.issue = issue

    def _graphql(self, query, variables):
        return {"issue": self.issue}


def issue(description=APPROVED, *, state="unstarted", blockers=(), parent=None):
    return {
        "id": "id-1",
        "identifier": "TS-1",
        "title": "Entregar algo",
        "url": "https://linear.app/x/issue/TS-1",
        "branchName": "benjamin/ts-1-entregar-algo",
        "description": description,
        "state": {"name": state.title(), "type": state},
        "parent": parent,
        "inverseRelations": {
            "nodes": [
                {
                    "type": kind,
                    "issue": {
                        "identifier": identifier,
                        "title": identifier,
                        "branchName": f"benjamin/{identifier.lower()}",
                        "state": {"name": blocker_state.title(), "type": blocker_state},
                    },
                }
                for kind, identifier, blocker_state in blockers
            ]
        },
    }


class ReadinessTests(unittest.TestCase):
    def test_issue_with_approved_spec_and_tickets_is_ready(self):
        readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)

        self.assertEqual(readiness["problems"], [])
        self.assertEqual(readiness["specId"], "SPEC-TS-1")
        self.assertEqual(readiness["ticketIds"], ["TICKET-001", "TICKET-002"])

    def test_missing_tickets_block_is_reported(self):
        description = APPROVED.split(f"<!-- {PREFIX}:tickets:start -->")[0]
        readiness = delivery_readiness(FakeLinear(issue(description)), "TS-1", PREFIX)

        self.assertIn(
            "No approved tickets block (Ticket ID) in the Linear description",
            readiness["problems"],
        )

    def test_issue_without_any_block_reports_both(self):
        readiness = delivery_readiness(FakeLinear(issue("Sólo contexto")), "TS-1", PREFIX)

        self.assertEqual(len(readiness["problems"]), 2)

    def test_completed_issue_is_not_launchable(self):
        readiness = delivery_readiness(
            FakeLinear(issue(state="completed")), "TS-1", PREFIX
        )

        self.assertTrue(any("nothing to deliver" in item for item in readiness["problems"]))

    def test_only_open_blocking_relations_count(self):
        readiness = delivery_readiness(
            FakeLinear(
                issue(
                    blockers=(
                        ("blocks", "TS-2", "unstarted"),
                        ("blocks", "TS-3", "completed"),
                        ("related", "TS-4", "unstarted"),
                    )
                )
            ),
            "TS-1",
            PREFIX,
        )

        self.assertEqual(
            [item["identifier"] for item in readiness["openBlockers"]],
            ["TS-2"],
        )


class StackParentTests(unittest.TestCase):
    def readiness(self, **kwargs):
        return delivery_readiness(FakeLinear(issue(**kwargs)), "TS-1", PREFIX)

    def test_unblocked_issue_has_no_stack_parent(self):
        self.assertIsNone(stack_parent_for(self.readiness(), None))

    def test_blocking_parent_becomes_the_stack_parent(self):
        readiness = self.readiness(
            blockers=(("blocks", "TS-2", "started"),),
            parent={"identifier": "TS-2", "branchName": "benjamin/ts-2"},
        )

        self.assertEqual(stack_parent_for(readiness, None), "benjamin/ts-2")

    def test_unrelated_blocker_stops_the_launch(self):
        readiness = self.readiness(blockers=(("blocks", "TS-9", "started"),))

        with self.assertRaisesRegex(RunBlocked, "blocked by TS-9"):
            stack_parent_for(readiness, None)

    def test_explicit_stack_parent_wins(self):
        readiness = self.readiness(blockers=(("blocks", "TS-9", "started"),))

        self.assertEqual(stack_parent_for(readiness, "benjamin/x"), "benjamin/x")


class BuildLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        remote = root / "remote.git"
        self.repository = root / "checkout"
        git(root, "init", "--bare", str(remote))
        git(root, "init", "-b", "test", str(self.repository))
        git(self.repository, "config", "user.email", "test@example.com")
        git(self.repository, "config", "user.name", "Test")
        (self.repository / "file.txt").write_text("base\n")
        git(self.repository, "add", "file.txt")
        git(self.repository, "commit", "-m", "base")
        git(self.repository, "remote", "add", "origin", str(remote))
        git(self.repository, "push", "-u", "origin", "test")
        self.readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)

    def tearDown(self):
        self.temporary.cleanup()

    def configuration(self, **environment):
        values = {
            "ISSUE_DELIVERY_CONFIG_HOME": self.temporary.name,
            "LINEAR_EXPECTED_EMAIL": "user@example.com",
            "GITHUB_EXPECTED_LOGIN": "example-user",
            **environment,
        }
        with patch.dict(os.environ, values, clear=False):
            return settings()

    def launch(self, configuration, **overrides):
        options = {
            "base": "test",
            "stack_parent": None,
            "agent": "claude",
            "model": None,
            "toolbox_profile": "benjasanti",
            "project_id": "project-1",
            **overrides,
        }
        return build_launch(self.readiness, configuration, self.repository, **options)

    def test_command_carries_profile_and_identity_but_no_secret(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "lin_api_secret"}):
            launch = self.launch(self.configuration())

        arguments = launch["arguments"]
        self.assertEqual(arguments[:2], ["workspace", "create"])
        self.assertIn("CODEX_TOOLBOX_PROFILE=benjasanti", arguments)
        self.assertIn("LINEAR_EXPECTED_EMAIL=user@example.com", arguments)
        self.assertIn("GITHUB_EXPECTED_LOGIN=example-user", arguments)
        self.assertFalse(any("lin_api" in item for item in arguments))
        self.assertEqual(arguments[arguments.index("--branch") + 1], "test")
        self.assertEqual(launch["behindTarget"], 0)

    def test_model_is_passed_before_environment(self):
        arguments = self.launch(self.configuration(), model="opus-5-5-1m")["arguments"]

        self.assertLess(arguments.index("--model"), arguments.index("--env"))

    def test_missing_base_branch_blocks(self):
        with self.assertRaisesRegex(RunBlocked, "does not exist on origin"):
            self.launch(self.configuration(), base="development")

    def test_stack_parent_must_be_pushed(self):
        with self.assertRaisesRegex(RunBlocked, "benjamin/ts-0 does not exist"):
            self.launch(self.configuration(), stack_parent="benjamin/ts-0")

    def test_missing_identity_blocks_because_toolbox_would_fail(self):
        configuration = self.configuration(LINEAR_EXPECTED_EMAIL="", GITHUB_EXPECTED_LOGIN="")

        with self.assertRaisesRegex(RunBlocked, "LINEAR_EXPECTED_EMAIL"):
            self.launch(configuration)


class MessageTests(unittest.TestCase):
    def test_message_names_mode_approvals_and_reviewers(self):
        readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)
        message = launch_message(
            readiness, base="test", stack_parent=None, toolbox_profile="benjasanti"
        )

        self.assertIn("--mode conductor --base test", message)
        self.assertIn("SPEC-TS-1", message)
        self.assertIn("TICKET-001, TICKET-002", message)
        self.assertIn("CODEX_TOOLBOX_PROFILE=benjasanti pnpm conductor:toolbox", message)
        self.assertIn("mobile-web-reviewer", message)

    def test_stacked_message_uses_stack_parent(self):
        readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)
        message = launch_message(
            readiness, base="test", stack_parent="benjamin/ts-2", toolbox_profile="p"
        )

        self.assertIn("--mode conductor --stack-parent benjamin/ts-2", message)


class RemoteTests(unittest.TestCase):
    def test_ssh_and_https_remotes_match(self):
        self.assertEqual(
            _normalize_remote("git@github.com:Turbo-Shop/TurboShop.git"),
            _normalize_remote("https://github.com/turbo-shop/turboshop"),
        )


if __name__ == "__main__":
    unittest.main()
