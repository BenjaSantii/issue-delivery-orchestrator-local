import sys
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issue_delivery_orchestrator.cloud_launch import (
    _normalize_remote,
    bootstrap_message,
    bootstrap_outcome,
    build_launch,
    create_delivery_session,
    delivery_readiness,
    launch_message,
    stack_parent_for,
    wait_for_bootstrap,
    workspace_id_from,
)
from issue_delivery_orchestrator.config import settings
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked


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
        self.assertIsNone(readiness["qaModel"])

    def test_spec_can_recommend_the_qa_model(self):
        description = APPROVED.replace("Spec ID: SPEC-TS-1\n", "Spec ID: SPEC-TS-1\nQA Model: haiku-5-5-1m\n"
                                       "QA Effort: medium\n")
        readiness = delivery_readiness(FakeLinear(issue(description)), "TS-1", PREFIX)
        self.assertEqual((readiness["qaModel"], readiness["qaEffort"]), ("haiku-5-5-1m", "medium"))

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
        self.assertIn("IDO-BOOTSTRAP READY", launch["bootstrapMessage"])
        self.assertIn("--mode conductor", launch["message"])

    def test_model_is_passed_before_environment(self):
        arguments = self.launch(self.configuration(), model="opus-5-5-1m")["arguments"]

        self.assertLess(arguments.index("--model"), arguments.index("--env"))

    def test_each_agent_gets_an_explicit_model_by_default(self):
        for agent, model in (("claude", "opus-5-5-1m"), ("codex", "gpt-6.1-sol")):
            with self.subTest(agent=agent):
                launch = self.launch(self.configuration(), agent=agent)
                self.assertEqual(launch["model"], model)
                self.assertEqual(launch["arguments"][launch["arguments"].index("--model") + 1], model)

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

    def test_qa_model_comes_from_flag_then_spec_then_default(self):
        configuration = self.configuration()
        launch = self.launch(configuration)
        self.assertEqual(launch["qa"], {"model": "sonnet-5-5-1m", "effort": "high", "source": "default"})
        self.assertIn("--qa-model sonnet-5-5-1m --qa-effort high", launch["message"])
        self.readiness.update(qaModel="haiku-5-5-1m", qaEffort="medium")
        self.assertEqual(self.launch(configuration)["qa"],
                         {"model": "haiku-5-5-1m", "effort": "medium", "source": "spec"})
        launch = self.launch(configuration, qa_model="opus-5-5-1m")
        self.assertEqual(launch["qa"], {"model": "opus-5-5-1m", "effort": "", "source": "launch"})
        self.assertIn("--qa-model opus-5-5-1m`", launch["message"])
        with self.assertRaisesRegex(RunBlocked, "--qa-effort requires --qa-model"):
            self.launch(configuration, qa_effort="low")

    def test_implement_workers_default_to_sonnet_and_small_lane_lightens_qa(self):
        configuration = self.configuration()
        launch = self.launch(configuration)
        self.assertEqual(launch["implementModel"], "sonnet")
        self.assertIn("--implement-model sonnet --qa-model", launch["message"])
        self.assertEqual(self.launch(configuration, implement_model="opus")["implementModel"], "opus")
        self.readiness["lane"] = "small"
        self.assertEqual(self.launch(configuration)["qa"],
                         {"model": "sonnet-5-5-1m", "effort": "medium", "source": "default"})

    def test_codex_launch_keeps_qa_inheriting_without_a_choice(self):
        launch = self.launch(self.configuration(), agent="codex")
        self.assertIsNone(launch["qa"])
        self.assertIsNone(launch["implementModel"])
        self.assertNotIn("--qa-model", launch["message"])


class MessageTests(unittest.TestCase):
    def test_delivery_message_names_mode_approvals_and_reviewers(self):
        readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)
        message = launch_message(readiness, base="test", stack_parent=None)

        self.assertIn("--mode conductor --base test", message)
        self.assertIn("SPEC-TS-1", message)
        self.assertIn("TICKET-001, TICKET-002", message)
        self.assertIn("mobile-web-reviewer", message)
        self.assertIn("no las sustituyas leyendo archivos a mano", message)

    def test_stacked_message_uses_stack_parent(self):
        readiness = delivery_readiness(FakeLinear(issue()), "TS-1", PREFIX)
        message = launch_message(readiness, base="test", stack_parent="benjamin/ts-2")

        self.assertIn("--mode conductor --stack-parent benjamin/ts-2", message)

    def test_bootstrap_message_installs_toolbox_and_asks_for_marker(self):
        message = bootstrap_message("claude", "benjasanti")

        self.assertIn("claude plugin list --json", message)
        self.assertIn("CODEX_TOOLBOX_PROFILE=benjasanti pnpm conductor:toolbox", message)
        self.assertIn("0.12.1", message)
        self.assertIn("IDO-BOOTSTRAP READY", message)
        self.assertIn("no trabajes ninguna issue", message)

    def test_bootstrap_setup_wait_cannot_match_its_own_shell(self):
        message = bootstrap_message("claude", "benjasanti")

        self.assertIn("pgrep -f '[s]etup-workspace\\.sh'", message)
        self.assertNotIn("pgrep -f setup-workspace.sh", message)


def agent_event(*blocks, kind="assistant"):
    payload = {"type": kind, "message": {"role": "assistant", "content": list(blocks)}}
    return {"type": "agent", "content": {"type": "agent", "rawPayload": payload}}


def text(value):
    return {"type": "text", "text": value}


class BootstrapOutcomeTests(unittest.TestCase):
    def test_reads_ready_from_assistant_text(self):
        transcript = {"data": [agent_event(text("Plugin 0.11.0 listo.\n\nIDO-BOOTSTRAP READY"))]}

        self.assertEqual(bootstrap_outcome(transcript), ("READY", ""))

    def test_reads_failure_cause(self):
        transcript = {
            "data": [agent_event(text("IDO-BOOTSTRAP FAILED: LINEAR_API_KEY no está definida"))]
        }

        self.assertEqual(
            bootstrap_outcome(transcript),
            ("FAILED", "LINEAR_API_KEY no está definida"),
        )

    def test_ignores_the_prompt_and_tool_calls(self):
        prompt = {
            "type": "userMessage",
            "content": {"type": "userMessage", "message": "IDO-BOOTSTRAP READY"},
        }
        tool = agent_event(
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo 'IDO-BOOTSTRAP READY'"}}
        )
        tool_output = agent_event(
            {"type": "tool_result", "content": "IDO-BOOTSTRAP READY"}, kind="user"
        )

        self.assertIsNone(bootstrap_outcome({"data": [prompt, tool, tool_output]}))

    def test_marker_must_be_its_own_line(self):
        transcript = {
            "data": [agent_event(text("Cuando termine diré IDO-BOOTSTRAP READY al final."))]
        }

        self.assertIsNone(bootstrap_outcome(transcript))

    def test_last_verdict_wins(self):
        transcript = {
            "data": [
                agent_event(text("IDO-BOOTSTRAP FAILED: falta el plugin")),
                agent_event(text("Lo instalé.\nIDO-BOOTSTRAP READY")),
            ]
        }

        self.assertEqual(bootstrap_outcome(transcript)[0], "READY")


class WaitTests(unittest.TestCase):
    def test_polls_until_ready(self):
        pages = [
            {"data": []},
            {"data": [agent_event(text("Esperando el setup..."))]},
            {"data": [agent_event(text("IDO-BOOTSTRAP READY"))]},
        ]
        sleeps = []
        with patch(
            "issue_delivery_orchestrator.cloud_launch._session_transcript",
            side_effect=pages,
        ):
            outcome = wait_for_bootstrap(
                "conductor", "s-1", timeout_seconds=600, sleep=sleeps.append, clock=lambda: 0
            )

        self.assertEqual(outcome, ("READY", ""))
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_after_timeout(self):
        now = iter([0, 100, 200, 700])
        with patch(
            "issue_delivery_orchestrator.cloud_launch._session_transcript",
            return_value={"data": []},
        ):
            outcome = wait_for_bootstrap(
                "conductor",
                "s-1",
                timeout_seconds=600,
                sleep=lambda _: None,
                clock=lambda: next(now),
            )

        self.assertIsNone(outcome)


class TranscriptPagingTests(unittest.TestCase):
    def test_truncated_pages_shrink_until_they_parse(self):
        from issue_delivery_orchestrator.cloud_launch import _session_transcript

        calls = []

        def fake_run(arguments, **_):
            limit = int(arguments[arguments.index("--limit") + 1])
            offset = int(arguments[arguments.index("--offset") + 1])
            calls.append((limit, offset))
            result = type("Result", (), {})()
            if limit > 30:
                result.stdout = '{"data": [{"type": "agent"'  # cut mid-object
            elif offset == 0:
                result.stdout = json.dumps({"data": [{"type": "agent"}] * limit, "hasMore": True})
            else:
                result.stdout = json.dumps({"data": [{"type": "agent"}], "hasMore": False})
            return result

        with patch("issue_delivery_orchestrator.cloud_launch._cli_output", side_effect=lambda a: fake_run(a).stdout):
            transcript = _session_transcript("conductor", "s-1")

        self.assertEqual(len(transcript["data"]), 26)
        self.assertEqual(calls[:2], [(50, 0), (25, 0)])

    def test_unreadable_page_raises_instead_of_reading_empty(self):
        from issue_delivery_orchestrator.cloud_launch import _session_transcript

        def fake_run(arguments, **_):
            result = type("Result", (), {})()
            result.stdout = "not json"
            return result

        with patch("issue_delivery_orchestrator.cloud_launch._cli_output", side_effect=lambda a: fake_run(a).stdout):
            with self.assertRaisesRegex(OrchestrationError, "unreadable transcript page"):
                _session_transcript("conductor", "s-1")


class ConductorOutputTests(unittest.TestCase):
    def test_workspace_id_prefers_explicit_keys(self):
        self.assertEqual(
            workspace_id_from({"id": "ws-1", "session": {"id": "s-1"}}),
            "ws-1",
        )
        self.assertEqual(workspace_id_from({"workspaceId": "ws-2", "id": "x"}), "ws-2")

    def test_delivery_session_is_not_duplicated_on_resume(self):
        for name in ("Generador", "Entrega"):
            with self.subTest(name=name):
                existing = [{"id": "s-1", "name": "Bootstrap"}, {"id": "s-2", "name": name}]
                with patch(
                    "issue_delivery_orchestrator.cloud_launch.workspace_sessions",
                    return_value=existing,
                ), patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
                    session = create_delivery_session(
                        "conductor", "ws-1", {"agent": "claude", "model": None, "message": "m"}
                    )
                self.assertEqual(session["id"], "s-2")
                self.assertTrue(session["reused"])
                runner.assert_not_called()

    def test_delivery_session_sends_message_on_stdin(self):
        with patch(
            "issue_delivery_orchestrator.cloud_launch.workspace_sessions",
            return_value=[{"id": "s-1", "name": "Bootstrap"}],
        ), patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.stdout = '{"id": "s-3"}'
            session = create_delivery_session(
                "conductor", "ws-1", {"agent": "claude", "model": "opus", "message": "entrega"}
            )

        arguments = runner.call_args.args[0]
        self.assertEqual(arguments[1:5], ["session", "create", "--workspace", "ws-1"])
        self.assertIn("--model", arguments)
        self.assertEqual(arguments[arguments.index("--name") + 1], "Generador")
        self.assertEqual(runner.call_args.kwargs["input_text"], "entrega")
        self.assertEqual(session["id"], "s-3")


class RemoteTests(unittest.TestCase):
    def test_ssh_and_https_remotes_match(self):
        self.assertEqual(
            _normalize_remote("git@github.com:Turbo-Shop/TurboShop.git"),
            _normalize_remote("https://github.com/turbo-shop/turboshop"),
        )


if __name__ == "__main__":
    unittest.main()


class CloudResumeTests(unittest.TestCase):
    def test_message_resumes_without_new_run_and_keeps_local_changes(self):
        from issue_delivery_orchestrator.cloud_launch import resume_message

        message = resume_message("LOG-201")

        self.assertIn("no uses `--new-run`", message)
        self.assertIn("nextAction", message)
        self.assertIn("no los descartes", message)
        self.assertIn("no se heredan", message)

    def test_finds_the_single_active_workspace_by_issue_prefix(self):
        from issue_delivery_orchestrator.cloud_launch import find_issue_workspace

        listing = {"data": [
            {"id": "ws-old", "name": "[LOG-201] viejo", "archivedAt": "2026-10-01"},
            {"id": "ws-1", "name": "[LOG-201] Sales Force", "archivedAt": None},
            {"id": "ws-2", "name": "[LOG-2010] otra", "archivedAt": None},
        ]}
        with patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.stdout = json.dumps(listing)
            self.assertEqual(find_issue_workspace("conductor", "LOG-201")["id"], "ws-1")

    def test_ambiguous_workspace_needs_explicit_id(self):
        from issue_delivery_orchestrator.cloud_launch import find_issue_workspace

        listing = {"data": [
            {"id": "a", "name": "[LOG-201] uno", "archivedAt": None},
            {"id": "b", "name": "[LOG-201] dos", "archivedAt": None},
        ]}
        with patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.stdout = json.dumps(listing)
            with self.assertRaisesRegex(RunBlocked, "Pass --workspace"):
                find_issue_workspace("conductor", "LOG-201")

    def test_resume_session_uses_claude_opus_and_numbers_itself(self):
        from issue_delivery_orchestrator.cloud_launch import create_resume_session

        with patch(
            "issue_delivery_orchestrator.cloud_launch.workspace_sessions",
            return_value=[{"id": "s-1", "name": "Entrega"}, {"id": "s-2", "name": "Reanudación 1"}],
        ), patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.stdout = '{"id": "s-3"}'
            create_resume_session("conductor", "ws-1", "msg", agent="claude", model="opus-5-5-1m")

        arguments = runner.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--agent") + 1], "claude")
        self.assertEqual(arguments[arguments.index("--model") + 1], "opus-5-5-1m")
        self.assertEqual(arguments[arguments.index("--name") + 1], "Reanudación 2")
        self.assertEqual(runner.call_args.kwargs["input_text"], "msg")

    def test_resume_blocks_while_another_session_is_working(self):
        from argparse import Namespace
        from issue_delivery_orchestrator.cli import cloud_resume

        with patch.dict(os.environ, {"CONDUCTOR_IS_LOCAL": ""}), patch(
            "issue_delivery_orchestrator.cli.conductor_cli", return_value="conductor"
        ), patch(
            "issue_delivery_orchestrator.cli.working_sessions",
            return_value=[{"id": "s-1", "name": "Entrega"}],
        ):
            with self.assertRaisesRegex(RunBlocked, "still has working sessions"):
                cloud_resume(Namespace(issue="LOG-201", workspace="ws-1", agent="claude",
                                       model="opus-5-5-1m", confirm=True))


class CliOutputTests(unittest.TestCase):
    def test_large_output_is_read_whole_through_a_file(self):
        from issue_delivery_orchestrator.cloud_launch import _cli_output

        script = "import json,sys; sys.stdout.write(json.dumps({'data': ['x' * 400000]}))"
        output = _cli_output([sys.executable, "-c", script])

        self.assertEqual(len(json.loads(output)["data"][0]), 400000)

    def test_failure_raises_with_stderr(self):
        from issue_delivery_orchestrator.cloud_launch import _cli_output

        with self.assertRaisesRegex(OrchestrationError, "boom"):
            _cli_output([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])


class MergedBlockerTests(unittest.TestCase):
    def readiness(self, **kwargs):
        return delivery_readiness(FakeLinear(issue(**kwargs)), "TS-1", PREFIX)

    def test_blocker_with_merged_pr_no_longer_stacks(self):
        from issue_delivery_orchestrator.cloud_launch import drop_merged_blockers

        readiness = self.readiness(
            blockers=(("blocks", "TS-2", "started"), ("blocks", "TS-3", "started")),
            parent={"identifier": "TS-2", "branchName": "benjamin/ts-2"},
        )

        def fake_run(arguments, **_):
            result = type("Result", (), {"returncode": 0})()
            head = arguments[arguments.index("--head") + 1]
            result.stdout = '[{"number": 7}]' if head == "benjamin/ts-2" else "[]"
            return result

        with patch("issue_delivery_orchestrator.cloud_launch.run", side_effect=fake_run):
            merged = drop_merged_blockers(readiness, Path("."), "test")

        self.assertEqual([item["identifier"] for item in merged], ["TS-2"])
        self.assertEqual([item["identifier"] for item in readiness["openBlockers"]], ["TS-3"])

    def test_merged_parent_lets_the_slice_start_from_the_base(self):
        from issue_delivery_orchestrator.cloud_launch import drop_merged_blockers

        readiness = self.readiness(
            blockers=(("blocks", "TS-2", "started"),),
            parent={"identifier": "TS-2", "branchName": "benjamin/ts-2"},
        )
        with patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.returncode = 0
            runner.return_value.stdout = '[{"number": 7}]'
            drop_merged_blockers(readiness, Path("."), "test")

        self.assertIsNone(stack_parent_for(readiness, None))

    def test_gh_failure_keeps_the_blocker(self):
        from issue_delivery_orchestrator.cloud_launch import drop_merged_blockers

        readiness = self.readiness(blockers=(("blocks", "TS-2", "started"),))
        with patch("issue_delivery_orchestrator.cloud_launch.run") as runner:
            runner.return_value.returncode = 1
            runner.return_value.stdout = ""
            drop_merged_blockers(readiness, Path("."), "test")

        self.assertEqual(len(readiness["openBlockers"]), 1)

