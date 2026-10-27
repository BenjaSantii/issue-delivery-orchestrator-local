import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_qa_script as qa_examples
from issue_delivery_orchestrator import fresh_qa
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked
from issue_delivery_orchestrator.qa_script import qa_comment_body, validate_qa_results
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

    def launch_with(self, caller, catalog, **options):
        from issue_delivery_orchestrator.util import run as actual_run
        sessions = [{"id": "implementation", "name": "Generador", **caller}]
        created = []
        def fake_run(args, **kwargs):
            if args[0] != "conductor":
                return actual_run(args, **kwargs)
            if args[1] == "model":
                return SimpleNamespace(stdout=json.dumps(catalog))
            created.append(args)
            sessions.append({"id": f"review-{len(created)}", "name": args[args.index("--name") + 1],
                             "model": args[args.index("--model") + 1],
                             "effort": args[args.index("--effort") + 1]})
            return SimpleNamespace(stdout="{}")
        with patch("issue_delivery_orchestrator.fresh_qa.conductor_cli", return_value="conductor"), patch(
            "issue_delivery_orchestrator.fresh_qa.workspace_sessions", side_effect=lambda *_: sessions.copy()
        ), patch("issue_delivery_orchestrator.fresh_qa.run", side_effect=fake_run):
            entry = fresh_qa.launch(self.state, "workspace", "implementation", ["http://localhost:3000"], **options)
        return entry, created

    def test_explicit_qa_model_overrides_the_calling_chat_without_duplicating(self):
        self.state.pop("qaSession", None)
        catalog = {"agents": [{"agent": "claude", "models": ["opus-5-5-1m", "sonnet-5-5"],
                               "efforts": ["medium", "high", "max"], "defaultEffort": "high"}]}
        caller = {"model": "opus-5-5-1m", "effort": "max"}
        entry, created = self.launch_with(caller, catalog, model="sonnet-5-5")
        self.assertEqual((entry["model"], entry["effort"], entry["configurationSource"]),
                         ("sonnet-5-5", "high", "explicit"))
        args = created[0]
        self.assertEqual(args[args.index("--model") + 1], "sonnet-5-5")
        again, created = self.launch_with(caller, catalog, model="sonnet-5-5")
        self.assertEqual((again["reviewSessionId"], created), (entry["reviewSessionId"], []))
        with self.assertRaisesRegex(RunBlocked, "different agent/model/effort"):
            self.launch_with(caller, catalog, model="sonnet-5-5", effort="medium")
        self.state.pop("qaSession")
        inherited, _ = self.launch_with(caller, catalog)
        self.assertEqual((inherited["model"], inherited["configurationSource"]), ("opus-5-5-1m", "inherited"))

    def test_explicit_qa_configuration_is_never_guessed(self):
        self.state.pop("qaSession", None)
        catalog = {"agents": [{"agent": "claude", "models": ["sonnet-5-5"], "efforts": ["high"]}]}
        caller = {"model": "sonnet-5-5", "effort": "high"}
        for options in ({"effort": "high"}, {"model": "unknown"}, {"model": "sonnet-5-5", "effort": "max"}):
            with self.subTest(options=options), self.assertRaises(RunBlocked):
                self.launch_with(caller, catalog, **options)
        self.assertNotIn("qaSession", self.state)

    def test_qa_wait_blocks_until_the_reviewer_stops_and_summarizes_results(self):
        from issue_delivery_orchestrator import observability_cli
        self.results()
        statuses = iter(["working", "working", "idle"])
        with patch.object(observability_cli, "conductor_cli", return_value="conductor"), patch.object(
            observability_cli, "_session_status", side_effect=lambda *_: next(statuses)
        ), patch.object(observability_cli.time, "sleep") as sleep:
            outcome = observability_cli.wait_for_qa(self.state, 5)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual((outcome["reviewSessionId"], outcome["status"]), ("review", "idle"))
        self.assertTrue(outcome["results"]["exists"])
        self.assertEqual(outcome["results"]["reviewSessionId"], "review")
        self.assertGreater(sum(outcome["results"]["statusCounts"].values()), 0)

    def test_qa_wait_times_out_and_requires_a_registered_reviewer(self):
        from issue_delivery_orchestrator import observability_cli
        with patch.object(observability_cli, "conductor_cli", return_value="conductor"), patch.object(
            observability_cli, "_session_status", return_value="working"
        ), patch.object(observability_cli.time, "sleep"), patch.object(
            observability_cli.time, "monotonic", side_effect=[0, 0, 400]
        ):
            with self.assertRaisesRegex(RunBlocked, "run qa-wait again"):
                observability_cli.wait_for_qa(self.state, 5)
        self.state.pop("qaSession")
        with self.assertRaisesRegex(RunBlocked, "qa-session first"):
            observability_cli.wait_for_qa(self.state, 5)
        self.assertEqual(fresh_qa.results_summary(self.state), {"exists": False})

    def test_run_qa_configuration_applies_when_no_model_is_passed(self):
        self.state.pop("qaSession", None)
        self.state["qaConfiguration"] = {"model": "sonnet-5-5-1m", "effort": ""}
        catalog = {"agents": [{"agent": "claude", "models": ["opus-5-5-1m", "sonnet-5-5-1m"],
                               "efforts": ["high", "max"], "defaultEffort": "high"}]}
        entry, created = self.launch_with({"model": "opus-5-5-1m", "effort": "max"}, catalog)
        self.assertEqual((entry["model"], entry["effort"], entry["configurationSource"]),
                         ("sonnet-5-5-1m", "high", "explicit"))
        self.assertEqual(created[0][created[0].index("--model") + 1], "sonnet-5-5-1m")

    def qa_round(self, review, keep, failing=()):
        """Write this round's results for HEAD with only `keep` cases, as a partial rerun does."""
        path = self.results()
        data = json.loads(path.read_text())
        data["reviewSessionId"] = review
        data["verifiedCommit"] = qa_examples.git(self.fixture.worktree, "rev-parse", "HEAD")
        data["cases"] = [dict(case, status="FAIL" if case["id"] in failing else case["status"])
                         for case in data["cases"] if case["id"] in keep]
        path.write_text(json.dumps(data))
        return path

    def repair(self, name):
        worktree = self.fixture.worktree
        (worktree / f"{name}.txt").write_text(name)
        qa_examples.git(worktree, "add", ".")
        qa_examples.git(worktree, "commit", "-m", name)

    def rerun(self, *cases):
        catalog = {"agents": [{"agent": "claude", "models": ["sonnet-5-5-1m"], "efforts": ["high"]}]}
        entry, _ = self.launch_with({"model": "sonnet-5-5-1m", "effort": "high"}, catalog, rerun=list(cases))
        return entry

    def test_partial_rerun_carries_previous_pass_and_reruns_requested_and_failing_cases(self):
        self.qa_round("review", {"QA-001", "QA-002", "QA-003"}, failing={"QA-003"})
        self.repair("fix-1")
        entry = self.rerun("QA-001")
        self.assertEqual(entry["rerun"], ["QA-001", "QA-003"])
        self.assertEqual(list(entry["carried"]), ["QA-002"])
        archive = self.fixture.root / "validation" / f"qa-results-{self.fixture.head[:12]}.json"
        self.assertTrue(archive.is_file())
        result = validate_qa_results(self.state, self.qa_round(entry["reviewSessionId"], {"QA-001", "QA-003"}))
        carried = {case["id"]: case for case in result["cases"] if case.get("carried")}
        self.assertEqual(list(carried), ["QA-002"])
        self.assertEqual(carried["QA-002"]["verifiedCommit"], self.fixture.head)
        self.assertEqual(carried["QA-002"]["persistence"]["status"], "VERIFIED")
        self.assertIn("Heredado de", qa_comment_body("run-1", result, {}, self.fixture.worktree))

        # A second repair chains: QA-002 still comes from the first commit, QA-001 from the second.
        second = entry["verifiedCommit"]
        self.repair("fix-2")
        entry = self.rerun("QA-003")
        self.assertEqual(entry["rerun"], ["QA-003"])
        self.assertEqual({k: v["verifiedCommit"] for k, v in entry["carried"].items()},
                         {"QA-001": second, "QA-002": self.fixture.head})
        result = validate_qa_results(self.state, self.qa_round(entry["reviewSessionId"], {"QA-003"}))
        self.assertEqual(sorted(case["id"] for case in result["cases"] if case.get("carried")),
                         ["QA-001", "QA-002"])
        with self.assertRaisesRegex(OrchestrationError, "missing cases: QA-003"):
            validate_qa_results(self.state, self.qa_round(entry["reviewSessionId"], set()))

    def test_partial_rerun_is_refused_without_trustworthy_previous_results(self):
        self.qa_round("other-chat", {"QA-001", "QA-002", "QA-003"})
        self.repair("fix-1")
        with self.assertRaisesRegex(RunBlocked, "another conversation or commit"):
            self.rerun("QA-001")
        self.qa_round("review", {"QA-001", "QA-002", "QA-003"})
        (self.fixture.root / "validation" / "qa-results.json").write_text(json.dumps(
            {**json.loads((self.fixture.root / "validation" / "qa-results.json").read_text()),
             "verifiedCommit": self.fixture.head}))
        self.state["qaSession"]["verifiedCommit"] = "0" * 40
        with self.assertRaisesRegex(RunBlocked, "not an ancestor"):
            self.rerun("QA-001")
        self.state["qaSession"]["verifiedCommit"] = self.fixture.head
        (self.fixture.worktree / self.state["artifacts"]["tickets"]).write_text("changed scope")
        with self.assertRaisesRegex(RunBlocked, "spec or tickets changed"):
            self.rerun("QA-001")
        with self.assertRaisesRegex(RunBlocked, "cannot be combined"):
            self.rerun("all", "QA-001")
