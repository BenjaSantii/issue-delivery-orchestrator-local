import json
import subprocess
import unittest
from unittest.mock import patch

from issue_delivery_orchestrator.errors import RunBlocked
from issue_delivery_orchestrator.superset_launch import (
    build_superset_launch,
    create_superset_workspace,
    existing_issue_workspace,
    superset_host,
    superset_launch_message,
)

import test_cloud_launch as cloud


def completed(payload):
    return subprocess.CompletedProcess([], 0, json.dumps(payload), "")


class SupersetLaunchTests(unittest.TestCase):
    setUp = cloud.BuildLaunchTests.setUp
    tearDown = cloud.BuildLaunchTests.tearDown
    configuration = cloud.BuildLaunchTests.configuration

    def superset_launch(self, **overrides):
        options = {
            "host_id": "host-1",
            "project_id": "project-1",
            "base": "test",
            "stack_parent": None,
            "agent": "agent-config-1",
            "model": None,
            **overrides,
        }
        return build_superset_launch(
            self.readiness, self.configuration(), self.repository, **options
        )

    def test_workspace_starts_the_agent_on_the_linear_branch_with_the_prompt(self):
        launch = self.superset_launch()
        arguments = launch["arguments"]

        self.assertEqual(arguments[:2], ["workspaces", "create"])
        self.assertEqual(arguments[arguments.index("--host") + 1], "host-1")
        self.assertEqual(arguments[arguments.index("--branch") + 1], "benjamin/ts-1-entregar-algo")
        self.assertIn("--skip-branch-prefix", arguments)
        self.assertEqual(arguments[arguments.index("--base-branch") + 1], "test")
        self.assertEqual(arguments[arguments.index("--agent") + 1], "agent-config-1")
        self.assertEqual(arguments[arguments.index("--model") + 1], "opus")
        self.assertEqual(arguments[arguments.index("--prompt") + 1], launch["message"])

    def test_message_starts_superset_mode_and_skips_the_grill(self):
        message = superset_launch_message(self.readiness, base="test", stack_parent=None)

        self.assertIn("--mode superset --base test", message)
        self.assertIn("SPEC-TS-1", message)
        self.assertIn("TICKET-001, TICKET-002", message)
        self.assertIn("sin repetir el Grill", message)

    def test_stacked_slice_starts_from_its_parent_branch(self):
        message = superset_launch_message(
            self.readiness, base="test", stack_parent="benjamin/ts-0"
        )

        self.assertIn("--mode superset --stack-parent benjamin/ts-0", message)
        with self.assertRaisesRegex(RunBlocked, "benjamin/ts-0 does not exist"):
            self.superset_launch(stack_parent="benjamin/ts-0")

    def test_created_workspace_is_unwrapped(self):
        with patch(
            "issue_delivery_orchestrator.superset_launch.run",
            return_value=completed({"workspace": {"id": "ws-1"}}),
        ):
            self.assertEqual(create_superset_workspace("superset", {"arguments": []}), {"id": "ws-1"})


class SupersetHostTests(unittest.TestCase):
    HOSTS = [
        {"id": "mac-1", "name": "Mi mac", "online": "local"},
        {"id": "vps-1", "name": "Maquina Prueba", "online": "yes"},
        {"id": "old-1", "name": "Vieja", "online": "no"},
    ]

    def host(self, wanted):
        with patch(
            "issue_delivery_orchestrator.superset_launch.run", return_value=completed(self.HOSTS)
        ):
            return superset_host("superset", wanted)

    def test_host_resolves_by_id_or_name(self):
        self.assertEqual(self.host("vps-1")["name"], "Maquina Prueba")
        self.assertEqual(self.host("Maquina Prueba")["id"], "vps-1")

    def test_missing_unknown_or_offline_host_blocks(self):
        for wanted, error in (("", "ISSUE_DELIVERY_SUPERSET_HOST"), ("otra", "found 0"),
                              ("old-1", "offline")):
            with self.subTest(wanted=wanted), self.assertRaisesRegex(RunBlocked, error):
                self.host(wanted)

    def test_active_workspace_on_the_issue_branch_is_found(self):
        workspaces = [
            {"id": "ws-0", "branch": "benjamin/ts-1", "archivedAt": "2026-11-01"},
            {"id": "ws-1", "branch": "benjamin/ts-1", "archivedAt": None},
        ]
        with patch(
            "issue_delivery_orchestrator.superset_launch.run", return_value=completed(workspaces)
        ):
            self.assertEqual(existing_issue_workspace("superset", "vps-1", "benjamin/ts-1")["id"], "ws-1")
            self.assertIsNone(existing_issue_workspace("superset", "vps-1", "benjamin/ts-2"))


if __name__ == "__main__":
    unittest.main()
