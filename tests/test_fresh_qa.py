import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_qa_script as qa_examples
from issue_delivery_orchestrator import fresh_qa
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked
from issue_delivery_orchestrator.qa_script import validate_qa_results
from issue_delivery_orchestrator.qa_persistence import validate_persistence


class FreshQATests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing QA-result fixture without rerunning its test suite here.
        self.fixture = qa_examples.ResultsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture.state
        self.state.update({"observabilityVersion": 1, "issue": {"identifier": "LOG-1"},
                           "mode": {"name": "conductor"}})
        tickets = self.fixture.root / "planning" / "tickets.md"
        tickets.write_text("Approved tickets")
        self.state["artifacts"]["tickets"] = self.fixture.relative(tickets)
        fresh_qa.register(self.state, "implementation", "review", host_receipt="fixture")

    def results(self):
        path = self.fixture.results()
        data = json.loads(path.read_text())
        data["reviewSessionId"] = "review"
        for case in data["cases"]:
            folder = self.fixture.root / "validation" / case["id"]
            folder.mkdir(exist_ok=True)
            contents = {"before": {"count": 0}, "after": {"count": 1},
                        "readOnlyProof": {"runtimeId": "rt-1", "target": "local-runtime",
                                          "transactionReadOnly": True}}
            artifacts = {}
            for key, value in contents.items():
                file = folder / (key + ".json")
                file.write_text(json.dumps(value))
                artifacts[key] = self.fixture.relative(file)
            query = folder / "query.sql"
            query.write_text("BEGIN READ ONLY; SELECT count(*) FROM seeded_reports; COMMIT;")
            case["persistence"] = {"status": "VERIFIED", "runtimeId": "rt-1",
                "expectation": "One report persisted", "expectedAfter": {"count": 1},
                "query": self.fixture.relative(query), **artifacts}
        path.write_text(json.dumps(data))
        return path

    def test_accepts_independent_review_with_matching_readonly_database_evidence(self):
        result = validate_qa_results(self.state, self.results())
        self.assertEqual(result["reviewSessionId"], "review")
        self.assertTrue(all(c["persistence"]["status"] == "VERIFIED" for c in result["cases"]))

    def test_rejects_wrong_reviewer_and_changed_approved_contract(self):
        path = self.results()
        data = json.loads(path.read_text())
        data["reviewSessionId"] = "implementation"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(RunBlocked, "registered reviewer"):
            validate_qa_results(self.state, path)
        path = self.results()
        (self.fixture.worktree / self.state["artifacts"]["tickets"]).write_text("changed scope")
        with self.assertRaisesRegex(RunBlocked, "approved artifacts changed"):
            validate_qa_results(self.state, path)

    def test_rejects_success_with_wrong_db_state_or_writable_connection(self):
        for corrupt in ("after", "readOnlyProof"):
            path = self.results()
            data = json.loads(path.read_text())
            artifact = self.fixture.worktree / data["cases"][0]["persistence"][corrupt]
            artifact.write_text(json.dumps({"count": 2} if corrupt == "after" else {
                "runtimeId": "rt-1", "target": "local-runtime", "transactionReadOnly": False}))
            with self.subTest(corrupt=corrupt), self.assertRaises(RunBlocked):
                validate_qa_results(self.state, path)

    def test_cannot_skip_concurrency_db_checks_or_use_files_outside_run(self):
        with self.assertRaisesRegex(RunBlocked, "cannot skip"):
            validate_persistence({"QA ID": "QA-001", "Type": "happy-path", "Evidence": "DB before/after"},
                {"persistence": {"status": "NOT_APPLICABLE", "reason": "No DB available"}},
                "rt-1", lambda x: x, self.fixture.worktree)
        path = self.results()
        data = json.loads(path.read_text())
        data["cases"][2]["persistence"] = {"status": "NOT_APPLICABLE", "reason": "No DB available"}
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(RunBlocked, "cannot skip"):
            validate_qa_results(self.state, path)
        path = self.results()
        data = json.loads(path.read_text())
        data["cases"][0]["persistence"]["after"] = "../../outside.json"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(OrchestrationError, "inside the worktree"):
            validate_qa_results(self.state, path)

    def test_launch_inherits_calling_chat_configuration_for_claude_and_codex(self):
        from issue_delivery_orchestrator.util import run as actual_run
        for agent, model, effort in (("codex", "gpt-6.1-sol", "high"), ("claude", "opus-5-5-1m", "max")):
            with self.subTest(agent=agent):
                self.state.pop("qaSession", None)
                sessions = [{"id": "implementation", "name": "Generador", "model": model, "effort": effort}]
                catalog = {"agents": [{"agent": agent, "models": [model], "efforts": [effort, "low"]}]}
                created = []
                def fake_run(args, **kwargs):
                    if args[0] != "conductor":
                        return actual_run(args, **kwargs)
                    if args[1] == "model":
                        return SimpleNamespace(stdout=json.dumps(catalog))
                    created.append((args, kwargs["input_text"]))
                    sessions.append({"id": "review", "name": args[args.index("--name") + 1],
                                     "model": model, "effort": effort})
                    return SimpleNamespace(stdout='{"id":"review"}')
                with patch("issue_delivery_orchestrator.fresh_qa.conductor_cli", return_value="conductor"), patch(
                    "issue_delivery_orchestrator.fresh_qa.workspace_sessions", side_effect=lambda *_: sessions.copy()
                ), patch("issue_delivery_orchestrator.fresh_qa.run", side_effect=fake_run):
                    first = fresh_qa.launch(self.state, "workspace", "implementation", ["http://localhost:3000"])
                    second = fresh_qa.launch(self.state, "workspace", "implementation", ["http://localhost:3000"])
                    sessions[0]["effort"] = "low"
                    with self.assertRaisesRegex(RunBlocked, "different agent/model/effort"):
                        fresh_qa.launch(self.state, "workspace", "implementation", ["http://localhost:3000"])
                self.assertEqual(first, second)
                self.assertEqual(len(created), 1)
                args = created[0][0]
                for flag, value in (("--agent", agent), ("--model", model), ("--effort", effort)):
                    self.assertEqual(args[args.index(flag) + 1], value)
                self.assertNotIn("--resume", args)
                self.assertEqual(first["reviewSessionId"], "review")
                self.assertEqual(first["agent"], agent)
                self.assertIn(self.state["artifacts"]["spec"], created[0][1])
        with self.assertRaisesRegex(RunBlocked, "different, fresh"):
            fresh_qa.register(self.state, "implementation", "implementation", host_receipt="fixture")

    def test_does_not_guess_missing_or_unknown_calling_chat_configuration(self):
        catalog = {"agents": [{"agent": "codex", "models": ["gpt-6.1-sol"], "efforts": ["high"]}]}
        with patch("issue_delivery_orchestrator.fresh_qa.run", return_value=SimpleNamespace(stdout=json.dumps(catalog))):
            for caller in ({}, {"model": "gpt-6.1-sol"}, {"effort": "high"}, {"model": "unknown", "effort": "high"},
                           {"model": "gpt-6.1-sol", "effort": "unknown"}):
                with self.subTest(caller=caller), self.assertRaises(RunBlocked):
                    fresh_qa.caller_configuration("conductor", caller)

    def test_unset_effort_uses_the_agent_default_conductor_runs(self):
        catalog = {"agents": [{"agent": "claude", "models": ["opus-5-5-1m"], "efforts": ["high", "max"],
                               "defaultEffort": "high"}]}
        with patch("issue_delivery_orchestrator.fresh_qa.run", return_value=SimpleNamespace(stdout=json.dumps(catalog))):
            for effort in (None, ""):
                with self.subTest(effort=effort):
                    self.assertEqual(fresh_qa.caller_configuration("conductor", {"model": "opus-5-5-1m", "effort": effort}),
                                     {"agent": "claude", "model": "opus-5-5-1m", "effort": "high"})
