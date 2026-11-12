import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_fresh_qa
from issue_delivery_orchestrator import fresh_qa, observability_cli
from issue_delivery_orchestrator.errors import RunBlocked
import tempfile
from pathlib import Path

from issue_delivery_orchestrator.qa_hosts import claude_alias


AGENTS = [
    {"id": "uuid-dev4", "presetId": "claude", "label": "Claude dev4"},
    {"id": "uuid-dev5", "presetId": "claude", "label": "Claude dev5"},
]


class FakeSuperset:
    """Answers the Superset CLI calls the QA host makes, recording what it was asked to do."""

    def __init__(self):
        self.terminals = [{"terminalId": "term-impl", "exited": False}]
        self.created = []
        self.sent = []
        self.states = {}
        self.screens = {}
        self.transcripts = {}

    def __call__(self, args, **kwargs):
        command = tuple(args[1:3])
        flag = lambda name: args[args.index(name) + 1]
        if command == ("agents", "list"):
            return self.reply(AGENTS)
        if command == ("terminals", "list"):
            return self.reply({"sessions": self.terminals})
        if command == ("agents", "create"):
            terminal = f"term-qa-{len(self.created) + 1}"
            self.created.append(list(args))
            self.terminals.append({"terminalId": terminal, "exited": False})
            self.screens[terminal] = "Quick safety check\n ❯ No, exit\n   Yes, I trust this folder"
            self.states[terminal] = "starting"
            return self.reply({"kind": "terminal", "sessionId": terminal})
        if command == ("agents", "explain"):
            return self.reply({"state": self.states.get(flag("--terminal"))})
        if command == ("terminals", "read"):
            return self.reply({"text": self.screens.get(flag("--terminal"), "")})
        if command == ("terminals", "send"):
            terminal = flag("--terminal")
            self.sent.append((terminal, flag("--keys") if "--keys" in args else flag("--text")))
            if "--keys" in args:
                self.states[terminal] = "working"
            return self.reply({"terminalId": terminal})
        if command == ("agents", "wait"):
            terminal = flag("--terminal")
            self.states[terminal] = "idle"
            return self.reply({"terminalId": terminal, "state": "idle"})
        if command == ("agents", "read"):
            return self.reply({"text": self.transcripts.get(flag("--terminal"), "")})
        raise AssertionError(f"unexpected superset call {args}")

    @staticmethod
    def reply(payload):
        return SimpleNamespace(stdout=json.dumps(payload), returncode=0, stderr="")


class SupersetQaTests(unittest.TestCase):
    setUp = test_fresh_qa.FreshQATests.setUp

    def prepare(self, spec_lines=""):
        self.state["mode"] = {"name": "superset"}
        self.state.pop("qaSession", None)
        spec = self.fixture.worktree / self.state["artifacts"]["spec"]
        spec.write_text(spec.read_text() + spec_lines)
        self.superset = FakeSuperset()
        patches = [
            patch("issue_delivery_orchestrator.qa_hosts.run", side_effect=self.superset),
            patch("issue_delivery_orchestrator.superset_launch.run", side_effect=self.superset),
            patch("issue_delivery_orchestrator.qa_hosts.superset_cli", return_value="superset"),
            patch("issue_delivery_orchestrator.superset_launch.time.sleep"),
            patch.dict("os.environ", {"SUPERSET_WORKSPACE_ID": "ws-1"}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def launch(self, **options):
        return fresh_qa.launch(self.state, None, None, ["http://localhost:3000"], **options)

    def test_qa_runs_in_a_new_terminal_with_sonnet_and_knows_its_own_id(self):
        self.prepare()

        first = self.launch(agent="Claude dev4")
        second = self.launch(agent="Claude dev4")

        self.assertEqual(first, second)
        self.assertEqual(len(self.superset.created), 1)
        args = self.superset.created[0]
        for flag, value in (("--workspace", "ws-1"), ("--agent", "uuid-dev4"),
                            ("--model", "sonnet"), ("--effort", "high")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertEqual(first["implementationSessionId"], "term-impl")
        self.assertEqual(first["reviewSessionId"], "term-qa-1")
        self.assertEqual(first["configurationSource"], "default")
        session = json.loads((self.fixture.root / "validation" / "qa-session.json").read_text())
        self.assertEqual(session["reviewSessionId"], "term-qa-1")
        self.assertIn("validation/qa-session.json", args[args.index("--prompt") + 1])
        self.assertNotIn("conductor-validation.md", args[args.index("--prompt") + 1])
        self.assertIn(("term-qa-1", "down,enter"), self.superset.sent)

    def test_run_and_spec_choose_the_qa_agent_and_model(self):
        self.prepare("\nQA Model: opus-5-5-1m\nQA Effort: max\n")
        self.state["qaConfiguration"] = {"agent": "Claude dev5", "model": "", "effort": ""}

        entry = self.launch()

        self.assertEqual((entry["agent"], entry["model"], entry["effort"]),
                         ("uuid-dev5", "opus[1m]", "max"))
        self.assertEqual(entry["configurationSource"], "spec")

    def test_small_lane_lightens_the_default_and_typos_block_before_launch(self):
        self.prepare("\nLane: small\n")
        first = self.launch()
        self.assertEqual(first["effort"], "medium")
        for options, error in (({"model": "sonet"}, "not a Claude Code model"),
                               ({"model": "haiku", "effort": "huge"}, "not one of"),
                               ({"agent": "Gemini"}, "No agent Gemini")):
            with self.subTest(options=options), self.assertRaisesRegex(RunBlocked, error):
                self.launch(**options)

    def test_later_qa_keeps_the_registered_implementer_among_live_qa_terminals(self):
        self.prepare()
        first = self.launch()
        self.superset.states[first["reviewSessionId"]] = "idle"
        self.superset.terminals.append({"terminalId": "term-shell", "exited": False})

        replacement = self.launch(replace=True)

        self.assertEqual(replacement["implementationSessionId"], "term-impl")
        self.assertEqual(replacement["replaces"], first["reviewSessionId"])
        self.assertNotEqual(replacement["reviewSessionId"], first["reviewSessionId"])

    def test_caller_must_be_identifiable(self):
        self.prepare()
        self.superset.terminals.append({"terminalId": "term-shell", "exited": False})

        with self.assertRaisesRegex(RunBlocked, "pass --implementation-session"):
            self.launch()
        entry = fresh_qa.launch(self.state, None, "term-impl", ["http://localhost:3000"])
        self.assertEqual(entry["implementationSessionId"], "term-impl")

    def test_wait_reports_a_reviewer_stopped_by_the_usage_limit_and_resume_wakes_it(self):
        self.prepare()
        entry = self.launch()
        self.superset.transcripts[entry["reviewSessionId"]] = "You've hit your session limit · resets 3pm"

        waited = observability_cli.wait_for_qa(self.state, 1)

        self.assertEqual(waited["status"], "idle")
        self.assertEqual((waited["state"], waited["reason"], waited["resets"]),
                         ("stopped", "usage-limit", "3pm"))
        resumed = fresh_qa.resume(self.state)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(self.superset.sent[-1][0], entry["reviewSessionId"])
        self.assertIn("Retoma el QA", self.superset.sent[-1][1])

    def test_conductor_model_ids_become_claude_code_aliases(self):
        for model, alias in (("sonnet-5-5-1m", "sonnet[1m]"), ("opus-5-5", "opus"),
                             ("haiku", "haiku"), ("claude-sonnet-5-5", "claude-sonnet-5-5")):
            with self.subTest(model=model):
                self.assertEqual(claude_alias(model), alias)


T3_MODELS = {"models": [{"slug": "claude-sonnet-5-5", "aliases": ["sonnet", "sonnet-5.5"]},
                        {"slug": "claude-opus-5-5", "aliases": ["opus", "opus-5.5"]}]}


class FakeT3:
    """Answers the t3 thread commands the QA host makes."""

    def __init__(self, worktree):
        self.threads = {"thr-impl": {"threadId": "thr-impl", "providerInstanceId": "claude-dev4",
                                     "model": "claude-opus-5-5", "worktreePath": worktree,
                                     "status": "running", "activeRunId": "run-1", "title": "impl"}}
        self.messages = {}
        self.sent = []

    def __call__(self, args, **kwargs):
        if tuple(args[1:3]) == ("thread", "show"):
            thread = self.threads.get(args[3])
            if thread is None:
                return SimpleNamespace(stdout="", returncode=1, stderr="unknown thread")
            return FakeSuperset.reply({"thread": thread, "messages": self.messages.get(args[3], [])})
        if tuple(args[1:3]) == ("thread", "send"):
            self.sent.append((args[3], kwargs.get("input_text")))
            return FakeSuperset.reply({"threadId": args[3]})
        raise AssertionError(f"unexpected t3 call {args}")

    def open_qa(self, request, thread_id="thr-qa", **overrides):
        self.threads[thread_id] = {"threadId": thread_id, "title": request["title"],
                                   "providerInstanceId": request["providerInstanceId"],
                                   "model": request["model"], "status": "running",
                                   "activeRunId": "run-qa", **overrides,
                                   "worktreePath": overrides.get(
                                       "worktreePath", request["workspace"]["worktreePath"])}
        return thread_id


class T3QaTests(unittest.TestCase):
    setUp = test_fresh_qa.FreshQATests.setUp

    def prepare(self, spec_lines=""):
        self.state["mode"] = {"name": "t3"}
        self.state.pop("qaSession", None)
        spec = self.fixture.worktree / self.state["artifacts"]["spec"]
        spec.write_text(spec.read_text() + spec_lines)
        home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        (home / "caches").mkdir()
        (home / "caches" / "claude-dev4.json").write_text(json.dumps(T3_MODELS))
        self.t3 = FakeT3(str(self.fixture.worktree))
        patches = [
            patch("issue_delivery_orchestrator.qa_hosts.run", side_effect=self.t3),
            patch("issue_delivery_orchestrator.qa_hosts.t3_cli", return_value="t3"),
            patch("issue_delivery_orchestrator.qa_hosts.time.sleep"),
            patch.dict("os.environ", {"T3CODE_THREAD_ID": "thr-impl", "T3CODE_HOME": str(home)}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def launch(self, **options):
        return fresh_qa.launch(self.state, None, None, ["http://localhost:3000"], **options)

    def test_the_agent_opens_the_qa_thread_in_this_worktree_and_the_engine_registers_it(self):
        self.prepare()

        requested = self.launch()

        self.assertEqual(requested["status"], "needs_agent_launch")
        request = requested["launch"]
        self.assertEqual((request["providerInstanceId"], request["model"], request["effort"]),
                         ("claude-dev4", "claude-sonnet-5-5", "high"))
        self.assertEqual(request["workspace"], {"type": "existing_worktree",
                                                "worktreePath": str(self.fixture.worktree)})
        prompt = Path(request["messageFile"]).read_text()
        self.assertIn("validation/qa-session.json", prompt)
        self.assertIn("En T3 la revisión móvil", prompt)
        self.assertNotIn("qaSession", self.state)

        thread = self.t3.open_qa(request)
        entry = self.launch(launched=thread)

        self.assertEqual((entry["implementationSessionId"], entry["reviewSessionId"]), ("thr-impl", "thr-qa"))
        self.assertEqual(entry["hostReceipt"], "t3-thread:thr-impl")
        session = json.loads((self.fixture.root / "validation" / "qa-session.json").read_text())
        self.assertEqual(session["reviewSessionId"], "thr-qa")
        self.assertEqual(self.launch(), entry)

    def test_a_thread_outside_the_worktree_or_with_another_model_is_rejected(self):
        self.prepare("\nQA Model: opus-5-5-1m\nQA Effort: max\n")
        request = self.launch()["launch"]
        self.assertEqual(request["model"], "claude-opus-5-5")
        for overrides, error in (({"worktreePath": "/tmp/elsewhere"}, "existing_worktree"),
                                 ({"title": "QA"}, "must be titled"),
                                 ({"model": "claude-sonnet-5-5"}, "provider/model")):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(RunBlocked, error):
                self.launch(launched=self.t3.open_qa(request, "thr-bad", **overrides))
        with self.assertRaisesRegex(RunBlocked, "not offered by T3"):
            self.launch(model="gpt-5")

    def test_wait_reports_a_reviewer_stopped_by_the_usage_limit_and_resume_wakes_it(self):
        self.prepare()
        entry = self.launch(launched=self.t3.open_qa(self.launch()["launch"]))
        self.t3.threads["thr-qa"].update({"activeRunId": None, "status": "completed"})
        self.t3.messages["thr-qa"] = [{"role": "assistant",
                                       "text": "You've hit your session limit · resets 3pm"}]

        waited = observability_cli.wait_for_qa(self.state, 1)

        self.assertEqual(waited["status"], "completed")
        self.assertEqual((waited["state"], waited["reason"], waited["resets"]),
                         ("stopped", "usage-limit", "3pm"))
        resumed = fresh_qa.resume(self.state)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(self.t3.sent[-1][0], entry["reviewSessionId"])
        self.assertIn("Retoma el QA", self.t3.sent[-1][1])

    def test_launched_is_only_for_t3_runs(self):
        with self.assertRaisesRegex(RunBlocked, "only in t3 mode"):
            fresh_qa.qa_host("superset", "ws-1", "thr-qa")


PASEO_MODELS = [
    {"id": "claude-opus-5-5", "thinkingOptionIds": ["low", "medium", "high", "xhigh", "max"]},
    {"id": "claude-sonnet-5-5", "thinkingOptionIds": ["low", "medium", "high"]},
    {"id": "claude-sonnet-5", "thinkingOptionIds": ["off", "low", "medium", "high"]},
]


class FakePaseo:
    """Answers the paseo agent commands the QA host makes."""

    def __init__(self, worktree):
        self.worktree = worktree
        self.agents = {"agent-impl": {"Id": "agent-impl", "Status": "running", "Cwd": worktree}}
        self.created = []
        self.sent = []
        self.logs = {}

    def __call__(self, args, **kwargs):
        command = args[1]
        if tuple(args[1:3]) == ("provider", "models"):
            return FakeSuperset.reply(PASEO_MODELS)
        if command == "inspect":
            agent = self.agents.get(args[2])
            if agent is None:
                return SimpleNamespace(stdout="", returncode=1, stderr="not found")
            return FakeSuperset.reply(agent)
        if command == "run":
            agent_id = f"agent-qa-{len(self.created) + 1}"
            self.created.append(list(args))
            self.agents[agent_id] = {"Id": agent_id, "Status": "running",
                                     "Cwd": args[args.index("--cwd") + 1]}
            return FakeSuperset.reply({"agentId": agent_id, "status": "running"})
        if command == "wait":
            self.agents[args[2]]["Status"] = "idle"
            return FakeSuperset.reply({"agentId": args[2], "status": "idle"})
        if command == "logs":
            return SimpleNamespace(stdout=self.logs.get(args[2], ""), returncode=0, stderr="")
        if command == "send":
            self.sent.append((args[2], args[args.index("--prompt") + 1]))
            return FakeSuperset.reply({})
        raise AssertionError(f"unexpected paseo call {args}")


class PaseoQaTests(unittest.TestCase):
    setUp = test_fresh_qa.FreshQATests.setUp

    def prepare(self, spec_lines=""):
        self.state["mode"] = {"name": "paseo"}
        self.state.pop("qaSession", None)
        spec = self.fixture.worktree / self.state["artifacts"]["spec"]
        spec.write_text(spec.read_text() + spec_lines)
        self.paseo = FakePaseo(str(self.fixture.worktree))
        patches = [
            patch("issue_delivery_orchestrator.qa_hosts.run", side_effect=self.paseo),
            patch("issue_delivery_orchestrator.qa_hosts.paseo_cli", return_value="paseo"),
            patch.dict("os.environ", {"PASEO_AGENT_ID": "agent-impl"}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def launch(self, **options):
        return fresh_qa.launch(self.state, None, None, ["http://localhost:3000"], **options)

    def test_qa_runs_as_another_agent_in_this_worktree_on_dev5_by_default(self):
        self.prepare()

        entry = self.launch()

        args = self.paseo.created[0]
        flag = lambda name: args[args.index(name) + 1]
        self.assertEqual((flag("--provider"), flag("--model"), flag("--thinking"), flag("--mode")),
                         ("claude-dev5", "claude-sonnet-5-5", "high", "bypassPermissions"))
        self.assertEqual(flag("--cwd"), str(self.fixture.worktree))
        self.assertIn("En Paseo la revisión móvil", args[-1])
        self.assertEqual((entry["implementationSessionId"], entry["reviewSessionId"]),
                         ("agent-impl", "agent-qa-1"))
        self.assertEqual(entry["hostReceipt"], "paseo-agent:agent-impl")
        session = json.loads((self.fixture.root / "validation" / "qa-session.json").read_text())
        self.assertEqual(session["reviewSessionId"], "agent-qa-1")
        self.assertEqual(self.launch(), entry)
        self.assertEqual(len(self.paseo.created), 1)

    def test_spec_or_flags_pick_the_model_and_unknown_choices_block_before_launch(self):
        self.prepare("\nQA Model: opus-5-5-1m\nQA Effort: max\n")
        entry = self.launch()
        self.assertEqual((entry["agent"], entry["model"], entry["effort"]),
                         ("claude-dev5", "claude-opus-5-5", "max"))
        with self.assertRaisesRegex(RunBlocked, "not offered by Paseo"):
            fresh_qa.qa_host("paseo", None).configuration(None, "gpt-5", None, "agent-impl",
                                                         (None, None), "standard")
        with self.assertRaisesRegex(RunBlocked, "Effort max is not offered"):
            fresh_qa.qa_host("paseo", None).configuration(None, "sonnet", "max", "agent-impl",
                                                         (None, None), "standard")

    def test_a_qa_agent_outside_the_worktree_is_rejected(self):
        self.prepare()
        original = self.paseo.__call__

        def elsewhere(args, **kwargs):
            reply = original(args, **kwargs)
            if args[1] == "run":
                self.paseo.agents[f"agent-qa-{len(self.paseo.created)}"]["Cwd"] = "/tmp/elsewhere"
            return reply

        with patch("issue_delivery_orchestrator.qa_hosts.run", side_effect=elsewhere), \
                self.assertRaisesRegex(RunBlocked, "does not run in this run's worktree"):
            self.launch()

    def test_wait_reports_a_reviewer_stopped_by_the_usage_limit_and_resume_wakes_it(self):
        self.prepare()
        entry = self.launch()
        self.paseo.logs["agent-qa-1"] = "[Assistant] You've hit your session limit · resets 3pm"

        waited = observability_cli.wait_for_qa(self.state, 1)

        self.assertEqual(waited["status"], "idle")
        self.assertEqual((waited["state"], waited["reason"], waited["resets"]),
                         ("stopped", "usage-limit", "3pm"))
        resumed = fresh_qa.resume(self.state)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(self.paseo.sent[-1][0], entry["reviewSessionId"])
        self.assertIn("Retoma el QA", self.paseo.sent[-1][1])

    def test_qa_session_must_run_inside_a_paseo_agent(self):
        with patch.dict("os.environ", {"PASEO_AGENT_ID": ""}), \
                self.assertRaisesRegex(RunBlocked, "inside the Paseo agent"):
            fresh_qa.qa_host("paseo", None)


if __name__ == "__main__":
    unittest.main()
